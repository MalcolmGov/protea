"""Deterministic family-level splits and golden selection (spec §25). Same family never straddles a split."""

from __future__ import annotations

import hashlib

from protea.data_pipeline.sources import GoldenSpec, SplitRatios
from protea.schemas.examples import Split, TrainingExample


def _bucket(key: str, seed: int) -> float:
    h = hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
    return int(h[:12], 16) / 16**12


def assign_splits(examples: list[TrainingExample], ratios: SplitRatios, seed: int) -> dict[str, int]:
    counts = {s.value: 0 for s in Split}
    for ex in examples:
        key = ex.metadata.family or ex.metadata.source_id or ex.metadata.id
        r = _bucket(f"{ex.metadata.task_type.value}:{key}", seed)
        if r < ratios.train:
            ex.metadata.split = Split.TRAIN
        elif r < ratios.train + ratios.validation:
            ex.metadata.split = Split.VALIDATION
        else:
            ex.metadata.split = Split.TEST
        counts[ex.metadata.split.value] += 1
    return counts


def select_golden(examples: list[TrainingExample], golden: GoldenSpec) -> list[str]:
    """Promote up to N test examples per task type to golden, deterministically. Returns the golden ids."""
    chosen: list[str] = []
    by_task: dict[str, list[TrainingExample]] = {}
    for ex in examples:
        if ex.metadata.split == Split.TEST and not ex.metadata.duplicate_of:
            by_task.setdefault(ex.metadata.task_type.value, []).append(ex)
    for task, items in by_task.items():
        items.sort(key=lambda e, task=task: _bucket(f"golden:{task}:{e.metadata.id}", golden.seed))
        for ex in items[: golden.per_task_type]:
            ex.metadata.split = Split.GOLDEN
            chosen.append(ex.metadata.id)
    return chosen


def golden_leak(
    train_ids: set[str], golden_ids: set[str], train_families: set[str], golden_families: set[str]
) -> list[str]:
    problems = []
    if train_ids & golden_ids:
        problems.append(f"{len(train_ids & golden_ids)} golden ids present in training data")
    if train_families & golden_families:
        problems.append(f"{len(train_families & golden_families)} families shared between training and golden")
    return problems
