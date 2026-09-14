"""Trim over-long / off-taxonomy rows out of a mined split — the fix for the agent_generation collapse.

The mined `agent_generation` targets embed multi-thousand-token `guardrails` / `system_prompt` prose that the
eval never scores (AgentSpecLite types them as bare `string`, no minimum length) but that blows the eval's
generation budget: ~35% of targets exceed the suite's `max_tokens`, so at eval time the JSON truncates mid-object,
`json_parsable` fails, and the whole task scores 0. Training on them teaches the model to emit un-parseable
output. This stage rewrites the split so training stops teaching that:

1. cap named string fields of a target JSON to a max length (keeps the field a valid string; every eval-scored
   field — category/tier/channels/languages/tools — is untouched, so the target still scores identically);
2. drop rows whose target carries an off-taxonomy field value (e.g. `category` the eval can never mark right).

Deterministic, offline, no GPU. Rows of an untouched task type pass through unchanged.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from protea.schemas.examples import TrainingExample


class TrimReport(BaseModel):
    rows_in: int = 0
    rows_kept: int = 0
    rows_dropped_offtaxonomy: int = 0
    fields_trimmed: dict[str, int] = Field(default_factory=dict)
    chars_removed: int = 0

    @property
    def ok(self) -> bool:
        return self.rows_kept == self.rows_in - self.rows_dropped_offtaxonomy


def _target_obj(ex: TrainingExample) -> dict | None:
    """The assistant target parsed as a JSON object, or None if the last turn isn't a bare JSON object."""
    if not ex.messages:
        return None
    content = ex.messages[-1].content
    if not content:
        return None
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def trim(
    rows: list[TrainingExample],
    *,
    task_types: set[str],
    cap_chars: dict[str, int] | None = None,
    drop_field_values: dict[str, set[str]] | None = None,
    marker: str = "\n…[trimmed]",
) -> tuple[list[TrainingExample], TrimReport]:
    """Cap over-long string fields and drop off-taxonomy rows, for targets whose task type is in ``task_types``.

    ``cap_chars`` maps a target field name to a max character length; a longer string field is truncated to that
    length plus ``marker``. ``drop_field_values`` maps a field name to a set of values that make the row ineligible
    (dropped). Rows of other task types, and targets that aren't a bare JSON object, pass through untouched.
    """
    cap_chars = cap_chars or {}
    drop_field_values = drop_field_values or {}
    report = TrimReport(rows_in=len(rows))

    kept: list[TrainingExample] = []
    for ex in rows:
        if str(ex.metadata.task_type) not in task_types:
            kept.append(ex)
            continue
        obj = _target_obj(ex)
        if obj is None:
            kept.append(ex)
            continue

        if any(str(obj.get(field)) in bad for field, bad in drop_field_values.items() if field in obj):
            report.rows_dropped_offtaxonomy += 1
            continue

        changed = False
        for field, limit in cap_chars.items():
            val = obj.get(field)
            if isinstance(val, str) and len(val) > limit:
                report.chars_removed += len(val) - limit
                obj[field] = val[:limit] + marker
                report.fields_trimmed[field] = report.fields_trimmed.get(field, 0) + 1
                changed = True
        if changed:
            ex.messages[-1].content = json.dumps(obj, ensure_ascii=False)
        kept.append(ex)

    report.rows_kept = len(kept)
    return kept, report
