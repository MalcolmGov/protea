"""Routing policy (spec §55–§57, architecture proposal §6): who may serve which task, under which constraints.

`configs/routing/*.yaml` (kind `routing`) declares the candidate models, the Protea eligibility thresholds per
ZaraBench category, the canary percentage and the fallback order. The router never invents a candidate.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TaskType = Literal[
    "agent_generation",
    "workflow_generation",
    "tool_calling",
    "connector_selection",
    "structured_output",
    "business_reasoning",
    "repair",
    "optimize",
    "routing",
    "chat",
]
Complexity = Literal["low", "medium", "high"]
Privacy = Literal["standard", "strict"]
CostPolicy = Literal["cheapest", "balanced", "best"]

# task type -> ZaraBench category whose score gates Protea eligibility
TASK_CATEGORY: dict[str, str] = {
    "agent_generation": "agent_generation",
    "workflow_generation": "workflow_generation",
    "tool_calling": "tool_calling",
    "connector_selection": "connector_selection",
    "structured_output": "structured_output",
    "business_reasoning": "business_reasoning",
    "repair": "structured_output",
    "optimize": "agent_generation",
    "routing": "structured_output",
    "chat": "business_reasoning",
}


class Candidate(BaseModel):
    name: str  # route id, e.g. protea-agent, frontier-opus
    provider: str  # provider name for build_provider
    model: str | None = None  # provider default when None
    self_hosted: bool = False  # satisfies privacy=strict
    cost_tier: int = Field(default=2, ge=0, le=5)  # 0 = free/self-hosted … 5 = most expensive
    quality_tier: int = Field(default=2, ge=0, le=5)  # desk/benchmark quality rank used when no matrix score exists
    max_complexity: Complexity = "high"
    task_types: list[str] = Field(default_factory=list)  # an empty list means the candidate serves every task type
    requires_benchmark: bool = False  # Protea-family routes: eligible only with a matrix score ≥ threshold
    version: str | None = None  # pinned model version label recorded on every decision


class RoutingPolicy(BaseModel):
    name: str
    version: str
    candidates: list[Candidate]
    default_route: str  # candidate name used when nothing else qualifies (must not require a benchmark)
    fallback_order: list[str] = Field(default_factory=list)  # candidate names tried after the chosen one fails
    thresholds: dict[str, float] = Field(default_factory=dict)  # ZaraBench category -> min score for benchmark routes
    default_threshold: float = 0.7
    canary_percent: float = Field(default=0.0, ge=0.0, le=100.0)  # share of tenants that may reach benchmark routes
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)  # below this a validated answer still triggers fallback
    max_attempts: int = Field(default=3, ge=1, le=6)
    reports_dir: str = "evaluation/reports"
    suite: str = "zarabench"  # only reports from this suite are capability evidence (security probes never are)
    latency_budget_ms: int | None = None

    @model_validator(mode="after")
    def _consistent(self) -> RoutingPolicy:
        names = {c.name for c in self.candidates}
        if len(names) != len(self.candidates):
            raise ValueError("candidate names must be unique")
        if self.default_route not in names:
            raise ValueError(f"default_route {self.default_route!r} is not a candidate")
        default = self.candidate(self.default_route)
        if default.requires_benchmark:
            raise ValueError("default_route must not require a benchmark score")
        unknown = [n for n in self.fallback_order if n not in names]
        if unknown:
            raise ValueError(f"fallback_order references unknown candidates {unknown}")
        return self

    def candidate(self, name: str) -> Candidate:
        return next(c for c in self.candidates if c.name == name)

    def threshold_for(self, category: str) -> float:
        return self.thresholds.get(category, self.default_threshold)


class RouteRequest(BaseModel):
    """What the router knows about a call before choosing a model."""

    task_type: TaskType = "chat"
    prompt_chars: int = 0
    tool_count: int = 0
    connector_count: int = 0
    financial: bool = False
    has_schema: bool = False
    privacy: Privacy = "standard"
    cost_policy: CostPolicy = "balanced"
    tenant_ref: str | None = None
    pinned_route: str | None = None  # per-agent version pinning (§57)
    latency_budget_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
