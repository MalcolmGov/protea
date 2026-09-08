"""CitizenAI government domain (Pillar 11, ADR-012). The retrieval layer that grounds every answer."""

from __future__ import annotations

from protea.citizenai.retrieval import Fact, RetrievalService, StubRetrieval, seed_facts

__all__ = ["Fact", "RetrievalService", "StubRetrieval", "seed_facts"]
