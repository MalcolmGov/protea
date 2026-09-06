"""Family-aware near-duplicate detection over assistant targets (shingle Jaccard; no external deps)."""

from __future__ import annotations

import re

from protea.schemas.examples import TrainingExample

_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str, k: int = 5) -> set[str]:
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def target_text(ex: TrainingExample) -> str:
    last = ex.messages[-1]
    return (last.content or "") + " " + " ".join(tc.name for tc in last.tool_calls)


def mark_duplicates(examples: list[TrainingExample], threshold: float = 0.92) -> int:
    """Within each task type, mark later examples whose target is near-identical to an earlier one. Returns count."""
    by_task: dict[str, list[tuple[TrainingExample, set[str]]]] = {}
    marked = 0
    for ex in examples:
        bucket = by_task.setdefault(ex.metadata.task_type.value, [])
        sh = shingles(target_text(ex))
        dup_of = None
        for other, osh in bucket:
            if jaccard(sh, osh) >= threshold:
                dup_of = other.metadata.id
                break
        if dup_of:
            ex.metadata.duplicate_of = dup_of
            marked += 1
        else:
            bucket.append((ex, sh))
    return marked
