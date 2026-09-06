"""Capability matrix: ZaraBench category scores per served model, read from committed benchmark reports.

The matrix is the only evidence that lets a benchmark-gated route (Protea) serve a task type; without a report a
Protea route is never eligible, whatever the config says (spec §23, §56)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ModelScores(BaseModel):
    model: str
    provider: str
    suite: str
    version: str
    run_id: str
    created_at: str
    partial: bool = False
    zarascore: float = 0.0
    categories: dict[str, float] = Field(default_factory=dict)
    task_set_hash: str = ""


class CapabilityMatrix(BaseModel):
    models: dict[str, ModelScores] = Field(default_factory=dict)  # key: "provider:model"

    def score(self, key: str, category: str) -> float | None:
        entry = self.models.get(key)
        if entry is None:
            return None
        return entry.categories.get(category)

    def eligible(self, key: str, category: str, threshold: float) -> tuple[bool, str]:
        entry = self.models.get(key)
        if entry is None:
            return False, f"no benchmark report for {key}"
        if entry.partial:
            return False, f"benchmark report for {key} is partial (judge checks skipped)"
        score = entry.categories.get(category)
        if score is None:
            return False, f"no {category} score for {key}"
        if score < threshold:
            return False, f"{category} score {score:.2f} < threshold {threshold:.2f} for {key}"
        return True, f"{category} score {score:.2f} ≥ {threshold:.2f}"

    def summary(self) -> list[dict[str, Any]]:
        return [
            {"model": k, "zarascore": v.zarascore, "partial": v.partial, "run": v.run_id, **v.categories}
            for k, v in sorted(self.models.items())
        ]


def _entry(data: dict[str, Any]) -> ModelScores | None:
    try:
        cats = {c["name"]: float(c["score"]) for c in data.get("categories", []) if c.get("n", 0) > 0}
        return ModelScores(
            model=data["model"],
            provider=data["provider"],
            suite=data["suite"],
            version=data["version"],
            run_id=data["run_id"],
            created_at=data["created_at"],
            partial=bool(data.get("partial", False)),
            zarascore=float(data.get("zarascore", 0.0)),
            categories=cats,
            task_set_hash=data.get("task_set_hash", ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_matrix(
    reports_dir: Path, *, suite: str | None = None, exclude_providers: tuple[str, ...] = ("mock", "reference")
) -> CapabilityMatrix:
    """Latest report per provider:model wins (by created_at). Mock and reference runs are never evidence."""
    matrix = CapabilityMatrix()
    if not reports_dir.exists():
        return matrix
    for path in sorted(reports_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entry = _entry(data)
        if entry is None or entry.provider in exclude_providers:
            continue
        if suite and entry.suite != suite:
            continue
        key = f"{entry.provider}:{entry.model}"
        current = matrix.models.get(key)
        if current is None or entry.created_at > current.created_at:
            matrix.models[key] = entry
    return matrix
