"""Eval-seeded synthetic tool-calling data (spec §14, strategy-review C3).

Generate → schema validation → expectation validation (the eval's expect block) → dedup → record generator_model → review lane.
Nothing here runs a paid model unless the caller passes a non-mock provider AND confirms.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from protea.data_pipeline.normalize.packages import ToolCallingSeed
from protea.evaluation.driver import MAX_TOOL_ROUNDS, DriveOptions, drive_conversation
from protea.providers.base import ModelProvider
from protea.schemas.examples import ExampleMetadata, LicenseStatus, ReviewStatus, ScanStatus, TaskType, TrainingExample
from protea.schemas.generation import Message, RequestMeta


class SynthesisResult(BaseModel):
    seed_id: str
    ok: bool
    problems: list[str] = Field(default_factory=list)
    example: TrainingExample | None = None


def _seed_target_tool(seed: ToolCallingSeed) -> str | None:
    """The single tool the seed's `expect` requires the completion to call, if any."""
    return (seed.expect or {}).get("tool")


def select_seeds(
    seeds: Iterable[ToolCallingSeed],
    *,
    limit: int,
    held_out: frozenset[str] = frozenset(),
    include_contaminated: bool = False,
    target_tools: frozenset[str] | None = None,
    scenarios: tuple[str, ...] = (),
    balance: bool = False,
) -> tuple[list[ToolCallingSeed], int]:
    """Choose which seeds to synthesize.

    The default (no filters, no balance) preserves the historical behaviour: the first `limit` seeds in file
    order that are neither contaminated nor held out. The knobs exist because file order over-represents whichever
    scenarios happen to come first — that is how a build can end up with 74 `handoff_to_human` completions and zero
    `book_appointment`/`log_*` ones even though the seeds for those exist. Targeting fixes that deterministically:

    - `target_tools`: keep only seeds whose `expect.tool` is in this set (the under-covered tools).
    - `scenarios`: keep only seeds whose `seed_id` contains one of these substrings (e.g. "write-after-confirm").
    - `balance`: fill `limit` round-robin across distinct `expect.tool` values instead of file order, so no single
      tool dominates the batch. File order is preserved within each tool group, so selection stays deterministic.

    Returns the chosen seeds and the count skipped because their family is held out by the golden lock.
    """
    pool: list[ToolCallingSeed] = []
    skipped_held_out = 0
    for s in seeds:
        if s.contaminated and not include_contaminated:
            continue
        if s.family in held_out:
            skipped_held_out += 1
            continue
        if target_tools is not None and _seed_target_tool(s) not in target_tools:
            continue
        if scenarios and not any(sc in s.seed_id for sc in scenarios):
            continue
        pool.append(s)

    if balance:
        chosen = _round_robin_by_tool(pool, limit)
    else:
        chosen = pool[:limit]
    return chosen, skipped_held_out


def _round_robin_by_tool(pool: list[ToolCallingSeed], limit: int) -> list[ToolCallingSeed]:
    """Interleave seeds across their `expect.tool` groups so a batch covers many tools evenly, not file order."""
    groups: OrderedDict[str | None, list[ToolCallingSeed]] = OrderedDict()
    for s in pool:
        groups.setdefault(_seed_target_tool(s), []).append(s)
    chosen: list[ToolCallingSeed] = []
    while len(chosen) < limit and any(groups.values()):
        for bucket in groups.values():
            if bucket:
                chosen.append(bucket.pop(0))
                if len(chosen) >= limit:
                    break
    return chosen


def selection_summary(seeds: list[ToolCallingSeed]) -> dict[str, Any]:
    """A spend-free preview of a selection: how many seeds, and the spread by target tool and scenario suffix."""
    by_tool = Counter(_seed_target_tool(s) or "(no tool call)" for s in seeds)
    by_scenario = Counter(s.seed_id.rsplit(":", 1)[-1] for s in seeds)
    return {
        "seeds": len(seeds),
        "by_target_tool": dict(by_tool.most_common()),
        "by_scenario": dict(by_scenario.most_common()),
    }


def _tool_problems(expect: dict[str, Any], tool_calls: list[str]) -> list[str]:
    problems = []
    if "tool" in expect and expect["tool"] not in tool_calls:
        problems.append(f"expected tool {expect['tool']} was not called")
    if expect.get("tool_any") and not (set(expect["tool_any"]) & set(tool_calls)):
        problems.append("none of tool_any was called")
    forbidden = set(expect.get("tool_none") or []) & set(tool_calls)
    if forbidden:
        problems.append(f"forbidden tool(s) called: {sorted(forbidden)}")
    if expect.get("no_tool") and tool_calls:
        problems.append(f"no_tool expected but called {tool_calls}")
    return problems


def _reply_problems(expect: dict[str, Any], reply: str) -> list[str]:
    problems = []
    if expect.get("says_any") and not any(str(s).lower() in reply for s in expect["says_any"]):
        problems.append("reply contains none of says_any")
    problems.extend(
        f"reply contains forbidden phrase {s!r}" for s in expect.get("says_none") or [] if str(s).lower() in reply
    )
    if not reply.strip():
        problems.append("empty final reply")
    return problems


def check_expectations(expect: dict[str, Any], tool_calls: list[str], final_reply: str) -> list[str]:
    """Apply the eval grammar (tool / tool_any / tool_none / no_tool / says_any / says_none) to a completed conversation."""
    return _tool_problems(expect, tool_calls) + _reply_problems(expect, (final_reply or "").lower())


def _metadata(seed: ToolCallingSeed, provider: ModelProvider, dataset_version: str) -> ExampleMetadata:
    return ExampleMetadata(
        dataset_version=dataset_version,
        source_type="eval_seeded_synthetic",
        source_repo=seed.source_repo,
        source_commit=seed.source_commit,
        source_path=seed.source_path,
        source_id=seed.seed_id,
        family=seed.family,
        domain=seed.domain,
        task_type=TaskType.TOOL_CALLING,
        difficulty="hard" if seed.followups else "medium",
        language=seed.language,
        synthetic=True,
        generator_model=f"{provider.name}:{provider.model}",
        review_status=ReviewStatus.PENDING,
        pii_scan=ScanStatus.PASSED,
        secret_scan=ScanStatus.PASSED,
        license_status=LicenseStatus.UNKNOWN,  # set by the teacher policy, never assumed
        rule_checks=ScanStatus.PASSED,
    )


async def synthesize(seed: ToolCallingSeed, provider: ModelProvider, *, dataset_version: str) -> SynthesisResult:
    transcript = await drive_conversation(
        provider,
        [Message(role="system", content=seed.system_message())],
        [seed.input, *seed.followups],
        seed.tools,
        options=DriveOptions(
            max_rounds=MAX_TOOL_ROUNDS,
            max_tokens=600,
            temperature=0.2,
            metadata=RequestMeta(task_type="tool_calling", channel="synthetic"),
        ),
    )
    if transcript.error:
        return SynthesisResult(seed_id=seed.seed_id, ok=False, problems=[transcript.error])
    problems = check_expectations(seed.expect, transcript.called, transcript.final_text)
    if transcript.truncated:
        problems.append("still calling tools at the round limit")
    if problems:
        return SynthesisResult(seed_id=seed.seed_id, ok=False, problems=problems)
    try:
        example = TrainingExample(
            metadata=_metadata(seed, provider, dataset_version), messages=transcript.messages, tools=seed.tools
        )
    except ValueError as exc:
        return SynthesisResult(seed_id=seed.seed_id, ok=False, problems=[f"schema: {exc}"])
    return SynthesisResult(seed_id=seed.seed_id, ok=True, example=example)
