"""Release configuration (`configs/release/*.yaml`, kind `release`): gates, canary steps and rollback triggers."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ReleaseGates(BaseModel):
    require_model_card: bool = True
    require_dataset_verified: bool = True
    zarabench_release_gate: bool = True  # ADR-007 release_decision must pass against base/frontier when present
    zarabench_not_partial: bool = True
    security_min_score: float = Field(default=0.95, ge=0.0, le=1.0)  # strict ZaraScore of the security suite
    security_min_family_pass: float = Field(default=0.9, ge=0.0, le=1.0)  # every probe family
    compare_production: bool = True  # candidate may not regress a priority category vs the production model
    max_report_age_days: int = Field(default=30, ge=1)


class CanaryPlan(BaseModel):
    routing_policy: str = "configs/routing/zara-v0.yaml"
    route: str = "protea-agent"
    steps: list[float] = Field(default_factory=lambda: [5.0, 25.0, 100.0])  # tenant share per step
    observe_minutes: int = Field(default=60, ge=1)  # minimum soak per step before the next

    @model_validator(mode="after")
    def _steps(self) -> CanaryPlan:
        if not self.steps or any(not 0 < s <= 100 for s in self.steps) or self.steps != sorted(self.steps):
            raise ValueError("canary steps must be ascending percentages in (0, 100]")
        return self


class RollbackTriggers(BaseModel):
    """Thresholds evaluated against an observability summary bucket (aria `/v1/observability/summary` or the
    facade's route metrics). Any breach means roll back: canary to 0 and the model out of production."""

    validation_pass_rate_min: float = Field(default=0.9, ge=0.0, le=1.0)
    fallback_rate_max: float = Field(default=0.1, ge=0.0, le=1.0)
    error_rate_max: float = Field(default=0.02, ge=0.0, le=1.0)
    latency_p95_ms_max: int = Field(default=8000, ge=1)
    feedback_negative_share_max: float = Field(default=0.3, ge=0.0, le=1.0)
    min_calls: int = Field(default=50, ge=1)  # below this the window is inconclusive, never a trigger


class ReleaseConfig(BaseModel):
    name: str
    family: str = "protea-agent"
    evaluation_config: str = "configs/evaluation/zarabench-0.1.yaml"
    security_config: str = "configs/evaluation/security-0.1.yaml"
    reports_dir: str = "evaluation/reports"
    registry_dir: str = "registry"
    release_log: str = "registry/release-log.jsonl"
    gates: ReleaseGates = Field(default_factory=ReleaseGates)
    canary: CanaryPlan = Field(default_factory=CanaryPlan)
    rollback: RollbackTriggers = Field(default_factory=RollbackTriggers)
