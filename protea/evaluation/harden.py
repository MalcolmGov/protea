"""Derive content floors from the references, so a valid shape stops being a passing answer (Tier 0.4, F1).

`protea evaluate audit` measured the hole: a stub — schema-shaped JSON with `"x"` for every string and `[]` for
every list — scores **62.1%** on the sealed 0.1.1 set, and **1.00 on every `agent_generation` task**, because the
checks are structural. This module closes that band *without touching the sealed set*: it reads a suite's
references, derives a floor per field, and emits a new suite version whose every reference still passes.

Rules (deterministic; printed per task by the CLI so the change is reviewable line by line):

* **String fields that carry content.** For every string field whose reference is at least `substantial_chars`
  long, require `max(floor_chars, min(cap_chars, keep_chars × reference length))`. The cap matters: a reference
  can be thousands of characters (the pre-trim agent specs were), and demanding a proportional answer would
  rebuild the truncation trap that caused the P0 collapse. The floor matters more: `"x"` fails at any size.
* **List fields that carry content.** A reference with at least one item in a list field requires at least
  `min_items` — an empty `tools: []` is not a plan.
* **Prose answers.** A prose reference of at least `substantial_words` sets `min_words`, capped at `cap_words`
  and never above the reference's own length, so a two-word refusal reference keeps a two-word floor.

What this is not: a quality bar. A padding model can satisfy every floor, and none of these checks read for
*truth*. They exist to shrink the free band — the part of the score a model collects for shape alone — so that a
ZaraScore difference means something. The judge (ADR-007) remains the path to grading content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from protea.evaluation.tasks import EvalTask


@dataclass(frozen=True)
class HardenRules:
    """Thresholds for the derived floors. Defaults are deliberately conservative (a floor, not a target)."""

    # The floor exists to kill tokens like `"x"`, not to demand length. It is the same as `substantial_chars`, so
    # a field the rule already accepted can always satisfy the floor it then gets — a floor above the reference's
    # own length would make the suite unsatisfiable, which is how the 80 this started as was caught.
    floor_chars: int = 40
    keep_chars: float = 0.10  # ...plus one tenth of the reference's own length
    cap_chars: int = 400  # ...capped here, so a long reference never demands a long answer
    substantial_chars: int = 40  # below this the reference is not evidence of a content-bearing field
    min_items: int = 1  # a reference list with items means the answer needs items
    floor_words: int = 12  # a prose answer of fewer words is a placeholder
    substantial_words: int = 24  # a shorter reference does not justify demanding language
    cap_words: int = 60

    def min_chars_for(self, reference_length: int) -> int:
        """Never above the reference's own length: a floor the witness cannot reach is a broken task."""
        derived = max(self.floor_chars, min(self.cap_chars, int(self.keep_chars * reference_length)))
        return min(derived, reference_length)

    def min_words_for(self, reference_words: int) -> int:
        return max(1, min(self.floor_words, int(self.cap_words), reference_words))


@dataclass
class HardenReport:
    """What was derived, with the evidence for each line — the review artefact for a suite version change."""

    tasks: int = 0
    hardened: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    requirements: dict[str, int] = field(default_factory=dict)  # check name -> tasks carrying it
    examples: list[str] = field(default_factory=list)  # a few derived lines, for the CLI output

    def summary(self) -> str:
        lines = [f"hardened {self.hardened}/{self.tasks} tasks"]
        for name, count in sorted(self.by_category.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:22} {count}")
        lines.append("requirements: " + ", ".join(f"{k}×{v}" for k, v in sorted(self.requirements.items())))
        return "\n".join(lines)


def _reference_object(task: EvalTask) -> dict[str, Any] | None:
    """The reference answer as an object, when it is JSON — the only shape floors can be derived from."""
    if task.reference is None or not task.reference.text:
        return None
    try:
        obj = json.loads(task.reference.text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _walk(obj: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """Every dotted path in the object: top-level scalars/lists plus one level of nested dicts."""
    out: list[tuple[str, Any]] = []
    for key, value in obj.items():
        path = f"{prefix}{key}"
        out.append((path, value))
        if isinstance(value, dict):
            out.extend(_walk(value, f"{path}."))
    return out


def harden_task(task: EvalTask, rules: HardenRules | None = None) -> tuple[EvalTask, list[str]]:
    """Return a copy of `task` with content floors derived from its own reference, plus the derived lines."""
    rules = rules or HardenRules()
    expect = task.expect
    min_chars = dict(expect.json_min_chars)
    min_items = dict(expect.json_min_items)
    min_words = expect.min_words
    lines: list[str] = []

    obj = _reference_object(task)
    if obj is not None:
        for path, value in _walk(obj):
            if isinstance(value, str) and len(value.strip()) >= rules.substantial_chars:
                derived = rules.min_chars_for(len(value.strip()))
                if derived > min_chars.get(path, 0):
                    min_chars[path] = derived
                    lines.append(f"{task.id}: field_len:{path} ≥ {derived} (reference {len(value.strip())} chars)")
            elif isinstance(value, list) and len(value) >= rules.min_items:
                derived = rules.min_items
                if derived > min_items.get(path, 0):
                    min_items[path] = derived
                    lines.append(f"{task.id}: field_items:{path} ≥ {derived} (reference {len(value)} items)")
    else:
        text = (task.reference.text if task.reference else "") or ""
        words = len(text.split())
        if words >= rules.substantial_words and (min_words is None or rules.min_words_for(words) > min_words):
            min_words = rules.min_words_for(words)
            lines.append(f"{task.id}: min_words ≥ {min_words} (reference {words} words)")

    if not lines:
        return task, []
    expect = expect.model_copy(
        update={"json_min_chars": min_chars, "json_min_items": min_items, "min_words": min_words}
    )
    if expect == task.expect:  # pragma: no cover - defensive: a line was recorded, so something must differ
        return task, []
    return task.model_copy(update={"expect": expect}), lines


def harden(tasks: list[EvalTask], rules: HardenRules | None = None) -> tuple[list[EvalTask], HardenReport]:
    """Harden a whole suite. Deterministic: same tasks and rules in, same suite out."""
    rules = rules or HardenRules()
    out: list[EvalTask] = []
    report = HardenReport(tasks=len(tasks))
    for task in tasks:
        hardened, lines = harden_task(task, rules)
        out.append(hardened)
        if not lines:
            continue
        report.hardened += 1
        name = task.category.value if hasattr(task.category, "value") else str(task.category)
        report.by_category[name] = report.by_category.get(name, 0) + 1
        for line in lines:
            key = line.split(": ", 1)[1].split(" ≥")[0]
            report.requirements[key] = report.requirements.get(key, 0) + 1
        if len(report.examples) < 12:
            report.examples.extend(lines[: 12 - len(report.examples)])
    return out, report


def check_references_pass(tasks: list[EvalTask]) -> list[str]:
    """Every reference must still satisfy its own (hardened) expectations — the repo's `evaluate author` rule.

    Runs the real evaluators against each reference: no provider, no inference, no judge.
    """
    from protea.evaluation.driver import Transcript
    from protea.evaluation.evaluators import evaluate

    failures: list[str] = []
    for task in tasks:
        transcript = Transcript(
            messages=[], tool_calls=list(task.reference.tool_calls if task.reference else []),
            final_text=(task.reference.text if task.reference else ""), rounds=1,
        )
        result = evaluate(task, transcript)
        if not result.passed:
            failures.append(f"{task.id}: {', '.join(result.failure_modes)}")
    return failures


__all__ = ["HardenReport", "HardenRules", "check_references_pass", "harden", "harden_task"]
