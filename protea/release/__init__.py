"""Release pipeline (Phase 10, ADR-011): gated promotion, canary by tenant share, rollback.

`protea.release.config` is imported by the config registry; the pipeline is imported lazily to avoid a cycle."""

from __future__ import annotations

from typing import Any

from protea.release.config import CanaryPlan, ReleaseConfig, ReleaseGates, RollbackTriggers

__all__ = [
    "STAGES",
    "CanaryPlan",
    "Evidence",
    "ReleaseConfig",
    "ReleaseGates",
    "ReleasePipeline",
    "ReleaseReport",
    "RollbackTriggers",
    "StageCheck",
    "should_rollback",
]

_LAZY = ("STAGES", "Evidence", "ReleasePipeline", "ReleaseReport", "StageCheck", "should_rollback")


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from protea.release import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
