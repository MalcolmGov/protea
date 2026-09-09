"""Review lane for synthesised training rows (spec §14, strategy-review C3).

Synthesis keeps only completions that satisfy each seed's `expect` (the right tool called, the right thing
said) — that is the *correctness* gate. It does NOT run the softer checks that `dataset build` applies to
mined rows, because synthetic rows never pass through a build until they are blended in. This module is that
missing gate: it takes the kept completions and decides, per row, whether they are safe to train on.

Every row lands in one of three lanes:

* ``blocked``  — must never train: a secret leaked, the row is schema-invalid, its family is sealed in the
  ZaraBench golden lock (eval leakage), or it is missing the synthetic-provenance invariants.
* ``flagged``  — a human should look: PII present (would need scrubbing first), a near-duplicate of another
  kept row or of an existing training row (redundant signal), or a degenerate / refusal-shaped target.
* ``clean``    — none of the above; safe to approve.

The verdict is advisory data, not an action: nothing here trains, deletes, or approves on its own. A human
still signs the review lane off; this only tells them where to look.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from protea.data_pipeline.dedup import jaccard, shingles, target_text
from protea.data_pipeline.scanners.pii import scan_pii
from protea.data_pipeline.scanners.secrets import scan_secrets
from protea.schemas.examples import TaskType, TrainingExample

# A target that is this short and calls no tool is almost certainly degenerate (empty ack, dropped turn).
_MIN_REPLY_CHARS = 8

# Refusal-shaped targets that slipped through the expect gate: low training value, worth a human glance.
_REFUSAL_MARKERS = (
    "i can't help",
    "i cannot help",
    "i can't assist",
    "i cannot assist",
    "i'm unable to",
    "i am unable to",
    "i'm not able to",
    "as an ai",
    "i cannot provide",
    "i can't provide",
)


# The three review lanes, ordered by severity so a later check only ever escalates (never downgrades) a row.
LANE_CLEAN = "clean"
LANE_FLAGGED = "flagged"
LANE_BLOCKED = "blocked"


class RowReview(BaseModel):
    """Per-row verdict. ``lane`` is the worst category any check assigned; ``reasons`` lists every finding."""

    id: str
    family: str | None = None
    lane: str = LANE_CLEAN
    reasons: list[str] = Field(default_factory=list)

    def block(self, reason: str) -> None:
        self.lane = LANE_BLOCKED
        self.reasons.append(reason)

    def flag(self, reason: str) -> None:
        if self.lane != LANE_BLOCKED:  # a block already found is the worse lane; never downgrade it
            self.lane = LANE_FLAGGED
        self.reasons.append(reason)


class ReviewReport(BaseModel):
    total: int = 0
    valid: int = 0
    invalid: int = 0
    errors: list[dict] = Field(default_factory=list)

    blocked: int = 0
    flagged: int = 0
    clean: int = 0

    # Blocking findings
    secret_rows: int = 0
    golden_lock_violations: int = 0
    invariant_violations: int = 0
    # Flagging findings
    pii_rows: int = 0
    pii_by_kind: dict[str, int] = Field(default_factory=dict)
    duplicates_within: int = 0
    duplicates_vs_reference: int = 0
    degenerate: int = 0
    refusal_shaped: int = 0

    # Distribution
    by_family: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, int] = Field(default_factory=dict)
    by_difficulty: dict[str, int] = Field(default_factory=dict)
    non_synthetic: int = 0
    non_tool_calling: int = 0

    rows: list[RowReview] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing is blocked — a clean/flagged mix can still go to a human; a block cannot."""
        return self.invalid == 0 and self.blocked == 0

    @property
    def top_families(self) -> list[tuple[str, int]]:
        return sorted(self.by_family.items(), key=lambda kv: kv[1], reverse=True)


def _row_text(ex: TrainingExample) -> str:
    """All model-authored / user-provided text on the row: message content, tool-call arguments, tool results.

    Deliberately excludes the tool *schemas* — those come from the catalogue and are scanned at build time; a
    hit there would be a source problem, not a synthesis problem, and would only add noise to this gate.
    """
    parts: list[str] = []
    for m in ex.messages:
        if m.content:
            parts.append(m.content)
        for tc in m.tool_calls:
            parts.append(json.dumps(tc.arguments, ensure_ascii=False))
    return "\n".join(parts)


def _final_reply(ex: TrainingExample) -> str:
    return ex.messages[-1].content or ""


def review_examples(
    examples: list[TrainingExample],
    *,
    reference_targets: list[tuple[str, set[str]]] | None = None,
    held_out_families: frozenset[str] | set[str] = frozenset(),
    dup_threshold: float = 0.92,
) -> ReviewReport:
    """Score already-parsed rows. ``reference_targets`` is (task_type, target-shingles) for an existing
    training set to cross-dedup against — build it with :func:`reference_shingles`."""
    report = ReviewReport()
    reviews: dict[str, RowReview] = {}

    # First pass: per-row checks that don't need the whole batch.
    for ex in examples:
        meta = ex.metadata
        rv = RowReview(id=meta.id, family=meta.family)
        reviews[meta.id] = rv

        report.by_family[meta.family or "?"] = report.by_family.get(meta.family or "?", 0) + 1
        report.by_language[meta.language] = report.by_language.get(meta.language, 0) + 1
        report.by_difficulty[meta.difficulty] = report.by_difficulty.get(meta.difficulty, 0) + 1

        # Synthetic-provenance invariants — these rows must declare what made them.
        if not meta.synthetic or not meta.generator_model:
            rv.block("missing synthetic/generator_model provenance")
            report.invariant_violations += 1
            report.non_synthetic += int(not meta.synthetic)
        if meta.task_type != TaskType.TOOL_CALLING:
            report.non_tool_calling += 1

        # Golden-lock: a sealed family in the training lane is eval leakage — a hard block.
        if meta.family in held_out_families:
            rv.block(f"family {meta.family!r} is sealed in the golden lock")
            report.golden_lock_violations += 1

        text = _row_text(ex)

        secrets = scan_secrets(text)
        if secrets:
            rv.block("secret: " + ", ".join(sorted({h.rule for h in secrets})))
            report.secret_rows += 1

        pii = scan_pii(text)
        if pii:
            kinds = sorted({h.kind for h in pii})
            rv.flag("pii: " + ", ".join(kinds))
            report.pii_rows += 1
            for k in kinds:
                report.pii_by_kind[k] = report.pii_by_kind.get(k, 0) + 1

        reply = _final_reply(ex)
        if len(reply.strip()) < _MIN_REPLY_CHARS and not ex.messages[-1].tool_calls:
            rv.flag("degenerate: empty/near-empty final reply")
            report.degenerate += 1
        if any(marker in reply.lower() for marker in _REFUSAL_MARKERS):
            rv.flag("refusal-shaped final reply")
            report.refusal_shaped += 1

    # Second pass: near-duplicate detection needs the whole batch (and the reference set).
    by_task: dict[str, list[tuple[str, set[str]]]] = {}
    ref_by_task: dict[str, list[set[str]]] = {}
    for tt, sh in reference_targets or []:
        ref_by_task.setdefault(tt, []).append(sh)

    for ex in examples:
        tt = ex.metadata.task_type.value
        sh = shingles(target_text(ex))
        rv = reviews[ex.metadata.id]

        # vs already-kept rows earlier in this batch
        dup_within = any(jaccard(sh, osh) >= dup_threshold for _, osh in by_task.get(tt, []))
        if dup_within:
            rv.flag("near-duplicate of another kept row")
            report.duplicates_within += 1
        else:
            by_task.setdefault(tt, []).append((ex.metadata.id, sh))

        # vs the existing training set
        if any(jaccard(sh, rsh) >= dup_threshold for rsh in ref_by_task.get(tt, [])):
            rv.flag("near-duplicate of an existing training row")
            report.duplicates_vs_reference += 1

    report.rows = list(reviews.values())
    report.blocked = sum(1 for r in report.rows if r.lane == LANE_BLOCKED)
    report.flagged = sum(1 for r in report.rows if r.lane == LANE_FLAGGED)
    report.clean = sum(1 for r in report.rows if r.lane == LANE_CLEAN)
    return report


def reference_shingles(examples: list[TrainingExample]) -> list[tuple[str, set[str]]]:
    """Turn an existing training set into (task_type, target-shingles) pairs for cross-dedup."""
    return [(ex.metadata.task_type.value, shingles(target_text(ex))) for ex in examples]
