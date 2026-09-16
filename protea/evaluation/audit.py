"""The suite's adversarial floor: what a content-free stub scores (instrument validity, ADR-007).

A benchmark is only informative when its floor is *low*. If a stub — schema-shaped JSON with `"x"` for every
string, the one declared tool call, and no knowledge at all — scores well, then a category's checks are not
measuring the capability the category is named after, and a ZaraScore quoted from it means less than it looks.

This audit makes that floor explicit, deterministic and free (no model, no GPU, no judge), so it can be run in
CI next to `evaluate verify` and quoted whenever a ZaraScore is. It is deliberately a *weak* attacker: it never
tries to satisfy a phrase check, never hedges, never reasons. A high stub score therefore means a real model
would score at least that, on shape alone — the part of the score that is free.

Measured on the sealed 0.1.1 set on 2026-09-16, this stub scores **1.00 on `agent_generation`** (25/25 tasks —
the highest-weighted category, 0.20 of the score) and 0.89 on `structured_output`, for a **stub ZaraScore of
62.1%** (strict 37.5%) against a frozen base of 80.7%. See `docs/evaluation-review.md` F9 and `docs/zarabench.md`.
"""

from __future__ import annotations

import json
from collections import defaultdict

from pydantic import BaseModel, Field

from protea.config.models import EvaluationConfig
from protea.evaluation.driver import Transcript
from protea.evaluation.evaluators import evaluate
from protea.evaluation.tasks import EvalTask
from protea.schemas.generation import ToolCall

STUB_TEXT = "Here is some general guidance you can follow."
STUB_REFUSAL = "I'm sorry, I can't help with that."


def _stub_object(task: EvalTask) -> dict:
    """The shortest object that could satisfy this task's structural checks: schema-shaped, no content."""
    schema = task.expect.schema_ or {}
    obj: dict = {}
    for name, spec in (schema.get("properties") or {}).items():
        match spec.get("type"):
            case "array":
                obj[name] = []
            case "object":
                obj[name] = {}
            case "number" | "integer":
                obj[name] = 0
            case _:
                obj[name] = "x"
    for key in schema.get("required") or []:
        obj.setdefault(key, "x")
    # A shape-only stub still knows which keys the task demands: they are in the expectation, not in the answer.
    for key in task.expect.json_required or []:
        obj.setdefault(key, "x")
    for path, want in (task.expect.json_fields or {}).items():
        if "." not in path:  # a nested expectation is beyond a shape-only stub
            obj[path] = want
    return obj


def stub_transcript(task: EvalTask) -> Transcript:
    """What the stub answers for one task: expected tool call if one is demanded, else shape or prose."""
    e = task.expect
    calls: list[ToolCall] = []
    if _wants_json(e):
        text = json.dumps(_stub_object(task))
    else:
        tool = e.tool or (e.tool_any[0] if e.tool_any else None)
        if tool and not e.no_tool:
            # No arguments: a stub can guess a declared tool's *name* from the prompt, but not an order id. Handing
            # it the expected arguments would score the answer, not the shape, and would flatter the instrument.
            calls = [ToolCall(id="stub-1", name=tool, arguments={})]
            text = STUB_REFUSAL if e.refuses else STUB_TEXT
        else:
            text = STUB_REFUSAL if e.refuses else STUB_TEXT
    return Transcript(messages=[], tool_calls=calls, final_text=text, rounds=1)


def _wants_json(e) -> bool:
    return bool(
        e.json_only or e.schema_ or e.json_equals is not None or e.json_fields or e.json_required or e.workflow
    )


class CategoryFloorRow(BaseModel):
    category: str
    n: int
    stub_mean: float  # mean task score the stub achieves
    stub_full_marks: int  # tasks where the stub scores 1.00
    weight: float


class AuditResult(BaseModel):
    tasks: int
    stub_zarascore: float  # Σ weight × category stub mean
    stub_zarascore_strict: float
    categories: list[CategoryFloorRow] = Field(default_factory=list)
    worst_offenders: list[str] = Field(default_factory=list)  # categories the stub fully satisfies

    def render(self) -> str:
        lines = [
            f"{'category':22} {'n':>3} {'weight':>7} {'stub mean':>10} {'full marks':>11}",
            f"{'-' * 22} {'-' * 3} {'-' * 7} {'-' * 10} {'-' * 11}",
        ]
        for r in sorted(self.categories, key=lambda r: -r.stub_mean):
            lines.append(f"{r.category:22} {r.n:>3} {r.weight:>7.2f} {r.stub_mean:>10.2f} {r.stub_full_marks:>7}/{r.n}")
        lines += [
            "",
            f"stub ZaraScore: {self.stub_zarascore * 100:.1f}%  (strict {self.stub_zarascore_strict * 100:.1f}%)"
            f" — the share of the score available to a content-free answer on shape alone",
        ]
        if self.worst_offenders:
            lines.append(f"fully satisfied by a stub: {', '.join(self.worst_offenders)}")
        return "\n".join(lines) + "\n"


def audit(tasks: list[EvalTask], cfg: EvaluationConfig) -> AuditResult:
    """Run the stub through the real evaluators — no provider, no inference, no judge."""
    weights = {c.name: c.weight for c in cfg.categories}
    by_category: dict[str, list[float]] = defaultdict(list)
    strict: dict[str, list[float]] = defaultdict(list)
    for task in tasks:
        result = evaluate(task, stub_transcript(task))
        name = task.category.value if hasattr(task.category, "value") else str(task.category)
        by_category[name].append(result.score)
        strict[name].append(1.0 if result.passed else 0.0)
    rows = [
        CategoryFloorRow(
            category=name,
            n=len(scores),
            stub_mean=sum(scores) / len(scores),
            stub_full_marks=sum(1 for s in scores if s == 1.0),
            weight=weights.get(name, 0.0),
        )
        for name, scores in sorted(by_category.items())
    ]
    return AuditResult(
        tasks=len(tasks),
        stub_zarascore=sum(r.weight * r.stub_mean for r in rows),
        stub_zarascore_strict=sum(
            weights.get(name, 0.0) * (sum(scores) / len(scores)) for name, scores in strict.items()
        ),
        categories=rows,
        worst_offenders=[r.category for r in rows if r.stub_mean == 1.0],
    )


__all__ = ["AuditResult", "CategoryFloorRow", "audit", "stub_transcript"]
