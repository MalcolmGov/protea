"""Blend reviewed synthetic rows into a mined training split (spec §14) — the stage between review and train.

`dataset build` mines the source catalogues and emits splits with no `tool_calling` rows (that family is created
by synthesis). `dataset synthesize` makes those rows; `dataset review` sorts them. This stage merges the kept
ones into the build's train split so a training run can consume a single file:

1. drop rows the review rejected (blocked) — and, unless kept, rows still flagged/pending that a human hasn't
   approved;
2. scrub PII in the synthetic rows (the build already scrubbed the mined rows; synthetic rows have not been
   through a build, so their `pii_scan` is still pending) — deterministic synthetic replacement, same as build;
3. dedup the combined set (near-duplicate targets), dropping later duplicates;
4. mark the survivors approved and emit the merged split.

Never trains; only assembles the file a training run points at. Mined rows pass through untouched.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from protea.data_pipeline.dedup import mark_duplicates
from protea.data_pipeline.scanners.pii import redact
from protea.schemas.examples import ReviewStatus, ScanStatus, TrainingExample


class BlendReport(BaseModel):
    mined_rows: int = 0
    synthetic_in: int = 0
    dropped_rejected: int = 0
    dropped_unapproved: int = 0
    dropped_capped: int = 0
    pii_scrubbed_rows: int = 0
    pii_redactions: dict[str, int] = Field(default_factory=dict)
    dropped_duplicate: int = 0
    synthetic_kept: int = 0
    merged_total: int = 0

    @property
    def ok(self) -> bool:
        return self.merged_total == self.mined_rows + self.synthetic_kept


def _apply_cap(
    rows: list[TrainingExample], cap: dict[str, int]
) -> tuple[list[TrainingExample], int]:
    """Deterministically down-sample synthetic rows to at most ``cap[task_type]`` per task type.

    Order is by a stable hash of the row id (not file order or family), so the kept subset is reproducible
    and not biased toward whichever families sort first. A task type absent from ``cap`` is left untouched.
    Returns the kept rows (original order preserved) and the number dropped.
    """
    keep_ids: set[str] = set()
    by_task: dict[str, list[TrainingExample]] = {}
    for ex in rows:
        by_task.setdefault(str(ex.metadata.task_type), []).append(ex)
    for task, group in by_task.items():
        limit = cap.get(task)
        if limit is None or len(group) <= limit:
            keep_ids.update(id(ex) for ex in group)
            continue
        ordered = sorted(group, key=lambda e: hashlib.sha1(str(e.metadata.id).encode()).hexdigest())
        keep_ids.update(id(ex) for ex in ordered[:limit])
    kept = [ex for ex in rows if id(ex) in keep_ids]
    return kept, len(rows) - len(kept)


def _scrub_row(ex: TrainingExample, mode: str) -> dict[str, int]:
    """Redact PII in every message's text in place. Returns the per-kind counts for this row."""
    counts: dict[str, int] = {}
    for m in ex.messages:
        if m.content:
            new, c = redact(m.content, mode=mode)
            m.content = new
            for k, n in c.items():
                counts[k] = counts.get(k, 0) + n
    if counts:
        ex.metadata.redactions = {**ex.metadata.redactions, **counts}
        ex.metadata.pii_scan = ScanStatus.PASSED  # scrubbed → no raw PII remains
    return counts


def blend(
    mined: list[TrainingExample],
    synthetic: list[TrainingExample],
    *,
    keep_flagged: bool = True,
    pii_mode: str = "synthetic",
    dedup_threshold: float = 0.92,
    synthetic_cap: dict[str, int] | None = None,
) -> tuple[list[TrainingExample], BlendReport]:
    """Merge reviewed synthetic rows into a mined split. ``keep_flagged`` keeps rows whose review left them
    pending (flagged/clean-but-unapproved); set False to take only rows already stamped ``approved``.

    ``synthetic_cap`` optionally limits how many synthetic rows of a given task type survive (e.g.
    ``{"tool_calling": 250}``), deterministically down-sampling the rest. Use it to keep one synthesized
    family from swamping the blend — the imbalance behind protea-agent-0.2's regression (see ADR / lineage).
    Mined rows are never capped.
    """
    report = BlendReport(mined_rows=len(mined), synthetic_in=len(synthetic))

    kept: list[TrainingExample] = []
    for ex in synthetic:
        status = ex.metadata.review_status
        if status == ReviewStatus.REJECTED:
            report.dropped_rejected += 1
            continue
        if status != ReviewStatus.APPROVED and not keep_flagged:
            report.dropped_unapproved += 1
            continue
        counts = _scrub_row(ex, pii_mode)
        if counts:
            report.pii_scrubbed_rows += 1
            for k, n in counts.items():
                report.pii_redactions[k] = report.pii_redactions.get(k, 0) + n
        ex.metadata.review_status = ReviewStatus.APPROVED  # blended rows are, by construction, signed off
        kept.append(ex)

    if synthetic_cap:
        kept, report.dropped_capped = _apply_cap(kept, synthetic_cap)

    # Dedup the whole set together: mined rows first so a synthetic near-duplicate of a mined row loses, and
    # synthetic-vs-synthetic dups collapse. mark_duplicates is family/task-type aware.
    combined = mined + kept
    mark_duplicates(combined, threshold=dedup_threshold)
    merged = [ex for ex in combined if ex.metadata.duplicate_of is None]

    report.dropped_duplicate = len(combined) - len(merged)
    report.synthetic_kept = sum(1 for ex in merged if ex.metadata.synthetic)
    report.merged_total = len(merged)
    return merged, report
