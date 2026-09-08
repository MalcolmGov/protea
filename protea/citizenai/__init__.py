"""CitizenAI government domain (Pillar 11, ADR-012). The retrieval layer that grounds every answer."""

from __future__ import annotations

from protea.citizenai.connectors import Connector, build_registry, dispatch, statuses
from protea.citizenai.retrieval import Fact, RetrievalService, StubRetrieval, seed_facts

__all__ = [
    "Connector",
    "Fact",
    "RetrievalService",
    "StubRetrieval",
    "build_registry",
    "dispatch",
    "seed_facts",
    "statuses",
]
