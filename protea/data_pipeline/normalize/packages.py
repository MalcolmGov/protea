"""Agent packages → agent_generation and structured_output examples, plus tool-calling seeds."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from protea.data_pipeline.discovery import Artifact
from protea.data_pipeline.extractors.agent_packages import AgentPackage
from protea.data_pipeline.normalize.common import DEFAULT_PROMPTS, make_metadata, render_json
from protea.schemas.examples import LANGUAGE_TAGS, TaskType, TrainingExample
from protea.schemas.generation import Message, ToolSchema

AGENT_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "AgentSpecLite",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "category": {"type": "string"},
        "tier": {"type": "string", "enum": ["standard", "pro", "enterprise"]},
        "objective": {"type": "string"},
        "channels": {"type": "array", "items": {"type": "string"}},
        "languages": {"type": "array", "items": {"type": "string"}},
        "market": {"type": ["string", "null"]},
        "compliance": {"type": "array", "items": {"type": "string"}},
        "model_policy": {"type": "object"},
        "handoff": {"type": "object"},
        "tools": {"type": "array", "items": {"type": "object", "required": ["name", "description", "parameters"]}},
        "system_prompt": {"type": "string"},
        "guardrails": {"type": "string"},
    },
    "required": ["id", "name", "category", "tier", "objective", "channels", "tools", "system_prompt", "guardrails"],
}

MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "AgentManifest",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "category": {"type": "string"},
        "tier": {"type": "string"},
        "channels": {"type": "array", "items": {"type": "string"}},
        "languages": {"type": "array", "items": {"type": "string"}},
        "compliance": {"type": "array", "items": {"type": "string"}},
        "handoff": {"type": "object"},
        "tools": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "name", "category", "tier", "channels", "languages", "tools"],
}


def _brief(pkg: AgentPackage) -> str:
    parts = [f"Business brief: {pkg.summary or pkg.name}."]
    if pkg.market:
        parts.append(f"Market: {pkg.market}.")
    if pkg.channels:
        parts.append(f"Channels: {', '.join(pkg.channels)}.")
    if pkg.languages:
        parts.append(f"Languages: {', '.join(pkg.languages)}.")
    if pkg.compliance:
        parts.append(f"Compliance: {', '.join(pkg.compliance)}.")
    triggers = (pkg.handoff or {}).get("triggers") or []
    if triggers:
        parts.append(f"Escalate to a human on: {', '.join(map(str, triggers))}.")
    parts.append(
        "Available tool capabilities: "
        + "; ".join(f"{t.get('name')} — {t.get('description', '')}" for t in pkg.tools)
        + "."
    )
    return "\n".join(parts)


def _spec_lite(pkg: AgentPackage) -> dict[str, Any]:
    return {
        "id": pkg.family,
        "name": pkg.name,
        "category": pkg.category,
        "tier": pkg.tier,
        "objective": pkg.summary,
        "channels": pkg.channels,
        "languages": pkg.languages,
        "market": pkg.market,
        "compliance": pkg.compliance,
        "model_policy": pkg.model,
        "handoff": pkg.handoff,
        "tools": [
            {k: t[k] for k in ("name", "description", "parameters", "side_effects", "returns", "auth_scope") if k in t}
            for t in pkg.tools
        ],
        "system_prompt": pkg.system_prompt,
        "guardrails": pkg.guardrails,
    }


def agent_generation_example(
    art: Artifact, pkg: AgentPackage, dataset_version: str, prompts: dict[str, str], redactions: dict[str, int]
) -> TrainingExample:
    system = prompts.get("agent_generation", DEFAULT_PROMPTS["agent_generation"])
    user = _brief(pkg) + "\n\nReturn the agent as JSON matching this schema:\n" + render_json(AGENT_SPEC_SCHEMA)
    return TrainingExample(
        metadata=make_metadata(
            art,
            dataset_version=dataset_version,
            source_type="agent_definition",
            source_id=pkg.id,
            family=pkg.family,
            domain=pkg.category,
            task_type=TaskType.AGENT_GENERATION,
            difficulty="hard" if len(pkg.tools) >= 5 else "medium",
            redactions=redactions,
        ),
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
            Message(role="assistant", content=render_json(_spec_lite(pkg))),
        ],
    )


def manifest_example(
    art: Artifact, pkg: AgentPackage, dataset_version: str, prompts: dict[str, str], redactions: dict[str, int]
) -> TrainingExample:
    system = prompts.get("structured_output", DEFAULT_PROMPTS["structured_output"])
    tool_lines = "\n".join(f"- {t.get('name')}: {t.get('description', '')}" for t in pkg.tools)
    user = (
        f"Describe the agent '{pkg.name}' as a manifest. It is a {pkg.tier}-tier {pkg.category} agent"
        f"{' for the ' + pkg.market + ' market' if pkg.market else ''}. {pkg.summary}\n"
        f"It runs on {', '.join(pkg.channels) or 'web'} in {', '.join(pkg.languages) or 'en'}."
        + (f" Compliance: {', '.join(pkg.compliance)}." if pkg.compliance else "")
        + (
            f" Human handoff triggers: {', '.join(map(str, pkg.handoff.get('triggers', [])))}."
            if pkg.handoff.get("triggers")
            else ""
        )
        + f"\nTools:\n{tool_lines}\n\nSchema:\n{render_json(MANIFEST_SCHEMA)}"
    )
    target = {
        "id": pkg.family,
        "name": pkg.name,
        "category": pkg.category,
        "tier": pkg.tier,
        "channels": pkg.channels,
        "languages": pkg.languages,
        "compliance": pkg.compliance,
        "handoff": pkg.handoff,
        "tools": sorted(pkg.tool_names),
    }
    return TrainingExample(
        metadata=make_metadata(
            art,
            dataset_version=dataset_version,
            source_type="agent_definition",
            source_id=pkg.id,
            family=pkg.family,
            domain=pkg.category,
            task_type=TaskType.STRUCTURED_OUTPUT,
            difficulty="easy",
            redactions=redactions,
        ),
        messages=[
            Message(role="system", content=system),
            Message(role="user", content=user),
            Message(role="assistant", content=render_json(target)),
        ],
    )


class ToolCallingSeed(BaseModel):
    """Everything a teacher model needs to complete one eval-defined turn, plus the expectations that validate it."""

    seed_id: str
    source_repo: str
    source_commit: str | None
    source_path: str
    agent_id: str
    family: str
    domain: str
    language: str
    system_prompt: str
    knowledge_excerpt: str
    tools: list[ToolSchema]
    input: str
    followups: list[str] = Field(default_factory=list)
    expect: dict[str, Any]
    contaminated: bool = False
    authored: bool = False


def _unknown_tool_refs(ex: dict[str, Any], tool_names: set[str]) -> list[str]:
    problems = []
    if "tool" in ex and ex["tool"] not in tool_names:
        problems.append(f"expect.tool names unknown tool {ex['tool']!r}")
    for key in ("tool_any", "tool_none"):
        problems.extend(
            f"expect.{key} names unknown tool {name!r}" for name in ex.get(key) or [] if name not in tool_names
        )
    return problems


def _contradictions(ex: dict[str, Any]) -> list[str]:
    problems = []
    if ex.get("no_tool") and ("tool" in ex or ex.get("tool_any")):
        problems.append("contradictory: no_tool with a required tool")
    if "tool" in ex and ex["tool"] in (ex.get("tool_none") or []):
        problems.append("contradictory: tool required and forbidden")
    return problems


def eval_rule_checks(e: dict[str, Any], tool_names: set[str]) -> list[str]:
    """Deterministic checks on an eval before it becomes a seed. Returns a list of problems (empty = passes)."""
    problems = []
    ex = e.get("expect") or {}
    if not isinstance(e.get("input"), str) or not e["input"].strip():
        problems.append("missing input")
    if not ex:
        problems.append("missing expect")
    problems += _unknown_tool_refs(ex, tool_names)
    problems += _contradictions(ex)
    lang = e.get("lang") or "en"
    if lang not in LANGUAGE_TAGS:
        problems.append(f"unknown language tag {lang!r}")
    return problems


def tool_calling_seeds(
    art: Artifact, pkg: AgentPackage, contaminated_ids: set[str], max_per_agent: int | None
) -> tuple[list[ToolCallingSeed], list[tuple[str, list[str]]]]:
    seeds: list[ToolCallingSeed] = []
    rejected: list[tuple[str, list[str]]] = []
    tools = [
        ToolSchema(
            name=t.get("name", ""),
            description=t.get("description", ""),
            parameters=t.get("parameters") or {"type": "object", "properties": {}},
        )
        for t in pkg.tools
    ]
    for e in pkg.evals:
        eid = str(e.get("id", ""))
        problems = eval_rule_checks(e, pkg.tool_names)
        if problems:
            rejected.append((eid, problems))
            continue
        seeds.append(
            ToolCallingSeed(
                seed_id=f"{pkg.id}:{eid}",
                source_repo=art.repo,
                source_commit=art.commit,
                source_path=art.relpath,
                agent_id=pkg.id,
                family=pkg.family,
                domain=pkg.category,
                language=e.get("lang") or "en",
                system_prompt=pkg.system_prompt,
                knowledge_excerpt=pkg.knowledge[:6000],
                tools=tools,
                input=e["input"],
                followups=[f for f in (e.get("followups") or []) if isinstance(f, str)],
                expect=dict(e.get("expect") or {}),
                contaminated=eid in contaminated_ids,
                authored=art.authored,
            )
        )
        if max_per_agent and len(seeds) >= max_per_agent:
            break
    return seeds, rejected
