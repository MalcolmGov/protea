"""Presets + connector catalogue → connector_selection; routing corpus → routing; flagship specs → structured_output."""

from __future__ import annotations

from typing import Any

from protea.data_pipeline.discovery import Artifact
from protea.data_pipeline.extractors.agent_packages import AgentPackage
from protea.data_pipeline.normalize.common import DEFAULT_PROMPTS, make_metadata, render_json
from protea.schemas.examples import TaskType, TrainingExample
from protea.schemas.generation import Message


def connector_selection_example(
    art: Artifact,
    pkg: AgentPackage,
    bindings: list[dict[str, str]],
    connector_catalogue: list[dict[str, Any]],
    dataset_version: str,
    prompts: dict[str, str],
) -> TrainingExample | None:
    tool_map = {t.get("name"): t.get("description", "") for t in pkg.tools}
    relevant = [b for b in bindings if b["tool"] in tool_map]
    if not relevant:
        return None
    catalogue = "\n".join(
        f"- {c.get('id')}: {c.get('name', '')} ({c.get('category', '')})" for c in connector_catalogue
    )
    tools = "\n".join(f"- {b['tool']}: {tool_map[b['tool']]}" for b in relevant)
    user = (
        f"Agent: {pkg.name} — {pkg.summary}\n\nAvailable connectors:\n{catalogue}\n- webhook: generic HTTP webhook (fallback)\n\n"
        f"Tools to bind:\n{tools}\n\nChoose one connector per tool."
    )
    return TrainingExample(
        metadata=make_metadata(
            art,
            dataset_version=dataset_version,
            source_type="preset",
            source_id=pkg.id,
            family=pkg.family,
            domain=pkg.category,
            task_type=TaskType.CONNECTOR_SELECTION,
        ),
        messages=[
            Message(role="system", content=prompts.get("connector_selection", DEFAULT_PROMPTS["connector_selection"])),
            Message(role="user", content=user),
            Message(role="assistant", content=render_json({"bindings": relevant})),
        ],
    )


def routing_examples(
    art: Artifact, corpus: dict[str, dict[str, list[str]]], dataset_version: str, prompts: dict[str, str]
) -> list[TrainingExample]:
    lanes = sorted(corpus)
    system = prompts.get("routing", DEFAULT_PROMPTS["routing"]) + "\nLanes: " + ", ".join(lanes)
    out = []
    for lane, spec in corpus.items():
        for i, utterance in enumerate(spec.get("must_claim") or []):
            out.append(
                TrainingExample(
                    metadata=make_metadata(
                        art,
                        dataset_version=dataset_version,
                        source_type="routing_corpus",
                        source_id=f"{lane}:{i}",
                        family=lane,
                        domain="routing",
                        task_type=TaskType.ROUTING,
                        language="en-ZA",
                        difficulty="easy",
                    ),
                    messages=[
                        Message(role="system", content=system),
                        Message(role="user", content=utterance),
                        Message(role="assistant", content=render_json({"lane": lane})),
                    ],
                )
            )
    return out


def flagship_examples(
    art: Artifact, specs: dict[str, Any], dataset_version: str, prompts: dict[str, str]
) -> list[TrainingExample]:
    out = []
    for spec in specs.values():
        if not isinstance(spec, dict) or "id" not in spec:
            continue
        target = {k: v for k, v in spec.items() if k != "__type__"}
        roi = target.get("roi_model")
        if isinstance(roi, dict):
            target["roi_model"] = {k: v for k, v in roi.items() if k != "__type__"}
        user = (
            f"Design an autonomous enterprise agent.\nObjective: {spec.get('objective', '')}\nContext: {spec.get('summary', '')}\n"
            "Return JSON with: id, name, version, objective, summary, category, industry, tier, autonomy_level, skills, tools, connectors, roi_model."
        )
        out.append(
            TrainingExample(
                metadata=make_metadata(
                    art,
                    dataset_version=dataset_version,
                    source_type="flagship_spec",
                    source_id=str(spec["id"]),
                    family=str(spec["id"]),
                    domain=str(spec.get("category", "general")),
                    task_type=TaskType.STRUCTURED_OUTPUT,
                    difficulty="hard",
                ),
                messages=[
                    Message(
                        role="system", content=prompts.get("structured_output", DEFAULT_PROMPTS["structured_output"])
                    ),
                    Message(role="user", content=user),
                    Message(role="assistant", content=render_json(target)),
                ],
            )
        )
    return out
