"""Model router (Phase 7, ADR-010): policy-driven model selection, validated fallback, confidence, events."""

from protea.router.capability import CapabilityMatrix, ModelScores, load_matrix
from protea.router.classifier import classify, complexity, describe, is_financial
from protea.router.confidence import Confidence, ConfidenceSignal, SuccessHistory, compute_confidence
from protea.router.events import (
    FallbackEvent,
    InMemoryRouteSink,
    JsonlRouteSink,
    LoggingRouteSink,
    RouteEvent,
    RouteSink,
)
from protea.router.policy import TASK_CATEGORY, Candidate, RouteRequest, RoutingPolicy
from protea.router.router import CandidateVerdict, ModelRouter, NoRouteError, RouteDecision, RouteOutcome, canary_bucket

__all__ = [
    "TASK_CATEGORY",
    "Candidate",
    "CandidateVerdict",
    "CapabilityMatrix",
    "Confidence",
    "ConfidenceSignal",
    "FallbackEvent",
    "InMemoryRouteSink",
    "JsonlRouteSink",
    "LoggingRouteSink",
    "ModelRouter",
    "ModelScores",
    "NoRouteError",
    "RouteDecision",
    "RouteEvent",
    "RouteOutcome",
    "RouteRequest",
    "RouteSink",
    "RoutingPolicy",
    "SuccessHistory",
    "canary_bucket",
    "classify",
    "complexity",
    "compute_confidence",
    "describe",
    "is_financial",
    "load_matrix",
]
