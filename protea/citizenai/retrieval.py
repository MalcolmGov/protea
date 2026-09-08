"""CitizenAI government retrieval layer (Pillar 11, ADR-012 · build-spec Phase 1).

The correctness story of CitizenAI is retrieval-grounded: the model *phrases*, but every fact — a grant amount, a
filing deadline, a loadshedding stage — is *retrieved* and *cited*, never memorised (ADR-012 decision 2). This
module is that retrieval interface plus a stub over the sample knowledge base: the same illustrative facts
CitizenBench grades against, enriched with the provenance an answer must carry (source + as-of date).

Real government connectors (SASSA, SARS eFiling, Home Affairs/eNaTIS, Eskom, municipal) replace the stub behind
the same `RetrievalService` interface; they are `blocked (needs data)` until access is granted. Nothing here is
live government data — every seeded fact is labelled `sample` and must never be served as live (see
`docs/citizenai/sources.md`).

Separation of duties: this layer *provides* the fact, its source and its as-of date; the eval (CitizenBench) and
the serving guard *enforce* that the answer cites them. That keeps the source of truth in retrieval, not weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Fact:
    """A retrieved government fact and the provenance an answer must cite."""

    domain: str  # e.g. "sassa"
    intent: str  # the retrieval intent / tool name the agent calls, e.g. "get_grant_schedule"
    data: dict[str, str]  # the structured fact the model must ground its answer on
    source: str  # authoritative source, e.g. "SASSA payment schedule"
    as_of: date  # the date this fact was verified / valid from
    sample: bool = True  # illustrative sample data until a real connector is wired in

    def citation(self) -> str:
        """The provenance string an answer carries — labelled `sample` until a real source backs it."""
        return f"({'sample' if self.sample else 'source'}: {self.source}, as of {self.as_of.isoformat()})"

    def is_stale(self, today: date, max_age_days: int) -> bool:
        """A fact older than its refresh window must not be served as current (build-spec staleness check)."""
        return (today - self.as_of).days > max_age_days


class RetrievalService(Protocol):
    """Given a retrieval intent, return the grounding `Fact`, or `None` if the intent is unknown."""

    def retrieve(self, intent: str) -> Fact | None: ...


# Illustrative as-of date for the seeded sample facts; a real connector supplies the true as-of per fact.
_AS_OF = date(2026, 9, 1)

# Seed knowledge base — the CitizenBench sample facts, enriched with source + as-of. Keys are the retrieval
# intents (tool names) the agent calls. Swapping in a live connector means replacing these behind the interface.
_SEED: tuple[Fact, ...] = (
    Fact(
        "sassa",
        "get_grant_schedule",
        {
            "srd_amount": "R370",
            "order": "older persons, then disability, then child support",
            "days": "3rd to 5th business day",
        },
        "SASSA payment schedule",
        _AS_OF,
    ),
    Fact(
        "sars",
        "get_filing_deadline",
        {"season": "7 July to 20 October", "provisional": "20 January", "channel": "eFiling"},
        "SARS filing season",
        _AS_OF,
    ),
    Fact(
        "uif",
        "get_uif_claim_steps",
        {"register": "uFiling", "forms": "UI-19 and UI-2.8", "bring": "ID and bank details"},
        "Department of Employment & Labour (UIF)",
        _AS_OF,
    ),
    Fact(
        "dha",
        "get_id_requirements",
        {
            "apply": "eHomeAffairs or a branch",
            "first_issue": "free",
            "reissue": "R140",
            "bring": "birth certificate and proof of address",
        },
        "Department of Home Affairs",
        _AS_OF,
    ),
    Fact(
        "eskom",
        "get_loadshedding_stage",
        {"stage": "Stage 2", "note": "block schedule depends on your suburb"},
        "Eskom / municipal loadshedding schedule",
        _AS_OF,
    ),
    Fact(
        "municipal",
        "get_rates_info",
        {
            "basis": "property valuation and the current tariff",
            "extra": "water and refuse charges",
            "query": "municipal office with your account number",
        },
        "Municipal rates & tariffs",
        _AS_OF,
    ),
)


class StubRetrieval:
    """In-memory retrieval over the seeded sample facts. Sample data is labelled and never served as live."""

    def __init__(self, facts: tuple[Fact, ...] = _SEED) -> None:
        self._by_intent = {f.intent: f for f in facts}

    def retrieve(self, intent: str) -> Fact | None:
        return self._by_intent.get(intent)

    def intents(self) -> list[str]:
        return sorted(self._by_intent)


def seed_facts() -> tuple[Fact, ...]:
    """The seeded sample facts, in registration order (see `docs/citizenai/sources.md`)."""
    return _SEED
