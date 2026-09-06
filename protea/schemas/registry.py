"""Dataset and model registry entries with the release lifecycle (spec §21)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    repo: str
    commit: str
    paths: list[str] = Field(default_factory=list)


class DatasetStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class DatasetEntry(BaseModel):
    name: str
    version: str
    task_types: list[str] = Field(default_factory=list)
    splits: dict[str, int] = Field(default_factory=dict)
    path: str
    sha256: str
    card_path: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    generator_models: list[str] = Field(default_factory=list)
    golden: bool = False
    status: DatasetStatus = DatasetStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return f"{self.name}-{self.version}"


class ModelStatus(StrEnum):
    EXPERIMENTAL = "experimental"
    CANDIDATE = "candidate"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


# Allowed lifecycle moves. Rejection/deprecation are terminal except that a
# deprecated production model may be reinstated to staging for a rollback.
MODEL_TRANSITIONS: dict[ModelStatus, set[ModelStatus]] = {
    ModelStatus.EXPERIMENTAL: {ModelStatus.CANDIDATE, ModelStatus.REJECTED},
    ModelStatus.CANDIDATE: {ModelStatus.STAGING, ModelStatus.REJECTED},
    ModelStatus.STAGING: {ModelStatus.PRODUCTION, ModelStatus.REJECTED, ModelStatus.CANDIDATE},
    ModelStatus.PRODUCTION: {ModelStatus.DEPRECATED},
    ModelStatus.DEPRECATED: {ModelStatus.STAGING},
    ModelStatus.REJECTED: set(),
}

MODEL_FAMILIES = ("protea-agent", "protea-runtime", "protea-embed", "protea-guard", "protea-voice", "protea-code")


class ModelEntry(BaseModel):
    family: str
    version: str
    base_model: str
    training_dataset: str  # dataset key, e.g. agent-training-0.1.0
    evaluation_suite: str | None = None
    training_method: str = "qlora"
    deployment_status: ModelStatus = ModelStatus.EXPERIMENTAL
    checkpoint_uri: str | None = None
    artifact_sha256: str | None = None
    git_commit: str | None = None
    experiment_id: str | None = None
    model_card_path: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    history: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.family}-{self.version}"

    def can_transition(self, to: ModelStatus) -> bool:
        return to in MODEL_TRANSITIONS[self.deployment_status]
