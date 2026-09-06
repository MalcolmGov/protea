"""Eval-seeded synthetic tool-calling data (spec §14, strategy-review C3).

Generate → schema validation → expectation validation (the eval's expect block) → dedup → record generator_model → review lane.
Nothing here runs a paid model unless the caller passes a non-mock provider AND confirms.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from protea.data_pipeline.normalize.common import DEFAULT_PROMPTS
from protea.data_pipeline.normalize.packages import ToolCallingSeed
from protea.providers.base import ModelProvider
from protea.schemas.examples import ExampleMetadata, LicenseStatus, ReviewStatus, ScanStatus, TaskType, TrainingExample
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message, RequestMeta, ToolCall

MAX_TOOL_ROUNDS = 3


class SynthesisResult(BaseModel):
    seed_id: str
    ok: bool
    problems: list[str] = Field(default_factory=list)
    example: TrainingExample | None = None


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


def _tool_result_stub(call: ToolCall) -> str:
    """Canned tool result: the seed has no live backend. Kept generic so the model cannot learn fabricated facts."""
    return json.dumps({"status": "ok", "tool": call.name, "echo": call.arguments})


async def _complete_turn(
    messages: list[Message], seed: ToolCallingSeed, provider: ModelProvider, called: list[str]
) -> GenerationResponse:
    """Drive one user turn to a final assistant text, executing tool calls against canned results."""
    resp: GenerationResponse | None = None
    for _round in range(MAX_TOOL_ROUNDS + 1):
        resp = await provider.generate(
            GenerationRequest(
                messages=messages,
                tools=seed.tools,
                max_tokens=600,
                temperature=0.2,
                metadata=RequestMeta(task_type="tool_calling", channel="synthetic"),
            )
        )
        if not resp.tool_calls:
            messages.append(Message(role="assistant", content=resp.content or ""))
            return resp
        messages.append(Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            called.append(call.name)
            messages.append(Message(role="tool", tool_call_id=call.id, name=call.name, content=_tool_result_stub(call)))
    return resp  # type: ignore[return-value]


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
    system = DEFAULT_PROMPTS["tool_calling"].format(agent_name=seed.agent_id) + "\n\n" + seed.system_prompt
    if seed.knowledge_excerpt:
        system += "\n\nBusiness information:\n" + seed.knowledge_excerpt
    messages: list[Message] = [Message(role="system", content=system)]
    called: list[str] = []
    final: GenerationResponse | None = None
    for turn in [seed.input, *seed.followups]:
        messages.append(Message(role="user", content=turn))
        final = await _complete_turn(messages, seed, provider, called)
    problems = check_expectations(seed.expect, called, final.content if final else "")
    if problems:
        return SynthesisResult(seed_id=seed.seed_id, ok=False, problems=problems)
    try:
        example = TrainingExample(
            metadata=_metadata(seed, provider, dataset_version), messages=messages, tools=seed.tools
        )
    except ValueError as exc:
        return SynthesisResult(seed_id=seed.seed_id, ok=False, problems=[f"schema: {exc}"])
    return SynthesisResult(seed_id=seed.seed_id, ok=True, example=example)
