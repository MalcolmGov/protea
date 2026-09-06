"""Benchmark task contract (spec §22): one task = a conversation to drive plus the expectations that grade it.

The `Expect` grammar is a superset of the estate's `evals.jsonl` grammar (tool / tool_any / tool_none / no_tool /
says_any / says_none / refuses / lang) with the structured-output, workflow and judge checks ZaraBench adds.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from protea.schemas.examples import LANGUAGE_TAGS
from protea.schemas.generation import Message, ToolCall, ToolSchema


class Category(StrEnum):
    AGENT_GENERATION = "agent_generation"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    CONNECTOR_SELECTION = "connector_selection"
    WORKFLOW_GENERATION = "workflow_generation"
    BUSINESS_REASONING = "business_reasoning"
    FAILURE_RECOVERY = "failure_recovery"
    SAFETY = "safety"
    HALLUCINATION = "hallucination"
    INSTRUCTION_FOLLOWING = "instruction_following"


WORKFLOW_NODE_TYPES = frozenset({"trigger", "lookup", "retrieve", "reasoning", "action", "handoff"})


class WorkflowExpect(BaseModel):
    """A workflow answer is JSON {nodes: [{id, type, tool?}], edges: [{from, to}]} and must be a DAG."""

    required_types: list[str] = Field(default_factory=lambda: ["trigger"])
    known_tools: list[str] = Field(default_factory=list)
    must_use_tools: list[str] = Field(default_factory=list)
    max_nodes: int = 25


class Expect(BaseModel, extra="forbid"):
    # tool grammar (shared with the estate's evals)
    tool: str | None = None
    tool_any: list[str] = Field(default_factory=list)
    tool_none: list[str] = Field(default_factory=list)
    no_tool: bool = False
    args: dict[str, dict[str, Any]] = Field(default_factory=dict)  # tool name -> subset of expected arguments
    # reply text
    says_any: list[str] = Field(default_factory=list)
    says_none: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)  # every phrase, case-insensitive
    must_not_include: list[str] = Field(default_factory=list)
    max_words: int | None = None
    # structured output
    json_only: bool = False
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    json_equals: Any | None = None
    json_fields: dict[str, Any] = Field(default_factory=dict)  # dotted path -> expected value
    json_required: list[str] = Field(default_factory=list)  # top-level keys
    known_tools: list[str] = Field(default_factory=list)  # names allowed in output `tools`
    known_connectors: list[str] = Field(default_factory=list)  # ids allowed in output `bindings`
    bindings: dict[str, str] = Field(default_factory=dict)  # tool -> expected connector
    workflow: WorkflowExpect | None = None
    # judge-dependent
    refuses: bool | None = None
    lang: str | None = None
    rubric: str | None = None
    rubric_min: float = 0.7  # normalised 0-1

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _consistent(self) -> Expect:
        if self.no_tool and (self.tool or self.tool_any):
            raise ValueError("contradictory: no_tool with a required tool")
        if self.tool and self.tool in self.tool_none:
            raise ValueError("contradictory: tool required and forbidden")
        if self.lang and self.lang not in LANGUAGE_TAGS:
            raise ValueError(f"unknown language tag {self.lang!r}")
        return self

    def needs_judge(self) -> bool:
        return self.refuses is not None or self.lang is not None or self.rubric is not None


class Reference(BaseModel):
    """What a correct answer looks like; drives the `reference` provider that proves the evaluators are satisfiable."""

    tool_calls: list[ToolCall] = Field(default_factory=list)
    text: str = ""


class TaskSource(BaseModel):
    repo: str | None = None
    commit: str | None = None
    path: str | None = None
    id: str | None = None


class EvalTask(BaseModel):
    id: str
    category: Category
    language: str = "en"
    family: str | None = None
    domain: str = "general"
    difficulty: str = "medium"
    tags: list[str] = Field(default_factory=list)
    source: TaskSource = Field(default_factory=TaskSource)
    messages: list[Message]  # system? + first user turn
    followups: list[str] = Field(default_factory=list)
    tools: list[ToolSchema] = Field(default_factory=list)
    tool_results: dict[str, list[str]] = Field(default_factory=dict)  # canned results per tool, cycled per call
    expect: Expect
    reference: Reference | None = None

    @model_validator(mode="after")
    def _shape(self) -> EvalTask:
        if not self.messages or self.messages[-1].role != "user":
            raise ValueError("task messages must end with the first user turn")
        if self.language not in LANGUAGE_TAGS:
            raise ValueError(f"unknown language tag {self.language!r}")
        names = {t.name for t in self.tools}
        for ref in [self.expect.tool, *self.expect.tool_any, *self.expect.tool_none, *self.expect.args]:
            if ref and ref not in names:
                raise ValueError(f"expect references undeclared tool {ref!r}")
        return self


def load_tasks(path: Path) -> list[EvalTask]:
    tasks: list[EvalTask] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                task = EvalTask.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: {str(exc).splitlines()[0]}") from exc
            if task.id in seen:
                raise ValueError(f"{path}:{n}: duplicate task id {task.id!r}")
            seen.add(task.id)
            tasks.append(task)
    return tasks


def write_tasks(tasks: list[EvalTask], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(t.model_dump_json(by_alias=True, exclude_none=True) + "\n")


def task_set_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def task_stats(tasks: list[EvalTask]) -> dict[str, Any]:
    by_cat: dict[str, int] = {}
    by_lang: dict[str, int] = {}
    judge = 0
    referenced = 0
    families: set[str] = set()
    for t in tasks:
        by_cat[t.category.value] = by_cat.get(t.category.value, 0) + 1
        by_lang[t.language] = by_lang.get(t.language, 0) + 1
        judge += t.expect.needs_judge()
        referenced += t.reference is not None
        if t.family:
            families.add(t.family)
    return {
        "total": len(tasks),
        "by_category": dict(sorted(by_cat.items())),
        "by_language": dict(sorted(by_lang.items())),
        "needs_judge": judge,
        "with_reference": referenced,
        "families": sorted(families),
    }


def approx_prompt_chars(task: EvalTask) -> int:
    chars = sum(len(m.content or "") for m in task.messages) + sum(len(f) for f in task.followups)
    chars += sum(len(json.dumps(t.parameters)) + len(t.description) for t in task.tools)
    return chars
