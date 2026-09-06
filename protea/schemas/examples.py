"""Training-example contract: chat messages with tool calls plus a provenance envelope (spec §8, §15)."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from protea.schemas.generation import Message, ToolSchema


class TaskType(StrEnum):
    AGENT_GENERATION = "agent_generation"
    TOOL_CALLING = "tool_calling"
    CONNECTOR_SELECTION = "connector_selection"
    WORKFLOW_GENERATION = "workflow_generation"
    BUSINESS_REASONING = "business_reasoning"
    STRUCTURED_OUTPUT = "structured_output"
    RECOVERY = "recovery"
    OPTIMIZATION = "optimization"
    MULTILINGUAL = "multilingual"
    ROUTING = "routing"


class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    GOLDEN = "golden"


class ScanStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class LicenseStatus(StrEnum):
    APPROVED = "approved"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


# Language tags reserved for the Africa specialisation track (spec §60) plus the catalogue's languages.
LANGUAGE_TAGS = frozenset({"en", "en-ZA", "af", "zu", "xh", "st", "tn", "sw", "fr", "pt", "es", "de", "it", "hi", "zh"})


class ExampleMetadata(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    dataset_version: str
    source_type: str  # agent_definition | eval | preset | registry | synthetic | routing_corpus | ...
    source_repo: str | None = None
    source_commit: str | None = None
    source_path: str | None = None
    source_id: str | None = None
    family: str | None = None  # dedup / split key (e.g. catalogue family without market prefix)
    domain: str = "general"
    task_type: TaskType
    difficulty: str = "medium"
    language: str = "en"
    synthetic: bool = False
    generator_model: str | None = None  # required when synthetic
    review_status: ReviewStatus = ReviewStatus.PENDING
    pii_scan: ScanStatus = ScanStatus.PENDING
    secret_scan: ScanStatus = ScanStatus.PENDING
    license_status: LicenseStatus = LicenseStatus.UNKNOWN
    split: Split | None = None
    duplicate_of: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> ExampleMetadata:
        if self.synthetic and not self.generator_model:
            raise ValueError("synthetic examples must record generator_model")
        if not self.synthetic and not (self.source_repo and self.source_path):
            raise ValueError("non-synthetic examples must be traceable: source_repo and source_path required")
        if self.language not in LANGUAGE_TAGS:
            raise ValueError(f"unknown language tag {self.language!r}; add it to LANGUAGE_TAGS deliberately")
        return self


def _check_turn(i: int, m: Message, known_tools: set[str], open_calls: set[str]) -> None:
    """Per-message rules: declared tools only, every tool result answers an open call, no empty assistant turns."""
    if m.role == "assistant":
        if not m.content and not m.tool_calls:
            raise ValueError(f"message {i}: empty assistant turn")
        for tc in m.tool_calls:
            if known_tools and tc.name not in known_tools:
                raise ValueError(f"message {i}: tool call to undeclared tool {tc.name!r}")
            open_calls.add(tc.id)
    elif m.role == "tool":
        if not m.tool_call_id or m.tool_call_id not in open_calls:
            raise ValueError(f"message {i}: tool result without a preceding matching tool call")
        open_calls.discard(m.tool_call_id)


class TrainingExample(BaseModel):
    metadata: ExampleMetadata
    messages: list[Message]
    tools: list[ToolSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _conversation_rules(self) -> TrainingExample:
        msgs = self.messages
        if len(msgs) < 2:
            raise ValueError("an example needs at least a user and an assistant message")
        if msgs[0].role not in ("system", "user"):
            raise ValueError("first message must be system or user")
        if msgs[-1].role != "assistant":
            raise ValueError("last message must be the assistant target")
        known_tools = {t.name for t in self.tools}
        open_calls: set[str] = set()
        for i, m in enumerate(msgs):
            _check_turn(i, m, known_tools, open_calls)
        if open_calls and not msgs[-1].tool_calls:
            raise ValueError("unanswered tool calls before the final assistant turn")
        return self

    def approx_tokens(self) -> int:
        chars = sum(
            len(m.content or "") + sum(len(json.dumps(tc.arguments)) for tc in m.tool_calls) for m in self.messages
        )
        chars += sum(len(json.dumps(t.parameters)) + len(t.description) for t in self.tools)
        return max(1, chars // 4)


class ValidationReport(BaseModel):
    path: str
    total: int = 0
    valid: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    by_task_type: dict[str, int] = Field(default_factory=dict)
    by_split: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    by_source_type: dict[str, int] = Field(default_factory=dict)
    synthetic: int = 0
    approx_tokens: int = 0
    duplicate_ids: int = 0
    golden_ids: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.valid == self.total and self.duplicate_ids == 0


def iter_examples(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            yield lineno, line


def validate_jsonl(path: Path, max_errors: int = 50) -> ValidationReport:
    """Validate a dataset file line by line and produce the §66 statistics."""
    report = ValidationReport(path=str(path))
    seen: Counter[str] = Counter()
    tt: Counter[str] = Counter()
    sp: Counter[str] = Counter()
    lg: Counter[str] = Counter()
    st: Counter[str] = Counter()
    for lineno, line in iter_examples(path):
        report.total += 1
        try:
            ex = TrainingExample.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            if len(report.errors) < max_errors:
                report.errors.append({"line": lineno, "error": str(exc).splitlines()[0][:300]})
            continue
        report.valid += 1
        seen[ex.metadata.id] += 1
        tt[ex.metadata.task_type.value] += 1
        sp[(ex.metadata.split or "unassigned")] += 1
        lg[ex.metadata.language] += 1
        st[ex.metadata.source_type] += 1
        report.synthetic += int(ex.metadata.synthetic)
        report.approx_tokens += ex.approx_tokens()
        if ex.metadata.split == Split.GOLDEN:
            report.golden_ids.append(ex.metadata.id)
    report.duplicate_ids = sum(1 for c in seen.values() if c > 1)
    report.by_task_type = dict(tt)
    report.by_split = dict(sp)
    report.by_language = dict(lg)
    report.by_source_type = dict(st)
    return report
