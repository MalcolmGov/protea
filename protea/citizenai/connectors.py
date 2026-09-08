"""CitizenAI government connectors (Pillar 11, ADR-012 · build-spec Phase 1.3).

The agent calls tools (`get_grant_schedule`, …); these connectors answer them. Every factual connector is backed
by the retrieval layer, so an answer is grounded in a retrieved fact with a source and an as-of date — never
invented. Each connector carries a status:

- `sample`  — answered from the seeded sample knowledge base (`StubRetrieval`); correct shape, illustrative data.
- `blocked` — needs a real data bridge or a verified record that does not exist yet; returns a safe, non-fabricated
  result (a hand-off or a request for ID), never a guessed value.
- `live`    — backed by a verified real source (none of the domain facts are `live` yet; see
  `docs/citizenai/sources.md`).

A deployment gate can read the registry's statuses to refuse serving a domain that is not yet `live`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from protea.citizenai.retrieval import RetrievalService, StubRetrieval
from protea.schemas.generation import Message, ToolCall

ConnectorStatus = Literal["sample", "blocked", "live"]


@dataclass(frozen=True)
class ConnectorResult:
    data: dict
    status: ConnectorStatus

    def to_json(self) -> str:
        # `_status` travels with the result so the serving layer / gate can see how grounded the answer is.
        return json.dumps({**self.data, "_status": self.status}, sort_keys=True)


@dataclass(frozen=True)
class Connector:
    name: str
    status: ConnectorStatus
    run: Callable[[dict], dict]

    def call(self, arguments: dict | None) -> ConnectorResult:
        return ConnectorResult(self.run(arguments or {}), self.status)


def _retrieval_connector(intent: str, retrieval: RetrievalService) -> Connector:
    def run(_args: dict) -> dict:
        fact = retrieval.retrieve(intent)
        if fact is None:  # pragma: no cover - registry only wires known intents
            return {"error": f"no data for {intent}"}
        return {**fact.data, "source": fact.source, "as_of": fact.as_of.isoformat(), "citation": fact.citation()}

    return Connector(intent, "sample", run)


def _lookup_my_payment(args: dict) -> dict:
    # A personal record needs a verified 13-digit ID, and there is no live records bridge yet — so this never
    # returns an amount. It asks for the ID or hands off; it never invents a payment.
    idn = str(args.get("id_number", "")).strip()
    if not (idn.isdigit() and len(idn) == 13):
        return {"outcome": "need_id", "message": "A verified 13-digit ID number is required to look up a payment."}
    return {"outcome": "handoff", "message": "Personal payment lookup is not connected yet; hand off to an official."}


def _handoff_to_official(args: dict) -> dict:
    return {"outcome": "handoff", "reason": str(args.get("reason", "")).strip() or "citizen requested a person"}


def build_registry(retrieval: RetrievalService | None = None) -> dict[str, Connector]:
    """The CitizenAI connector registry: factual domains (retrieval-backed) plus the personal and hand-off tools."""
    r = retrieval or StubRetrieval()
    registry: dict[str, Connector] = {intent: _retrieval_connector(intent, r) for intent in StubRetrieval().intents()}
    registry["lookup_my_payment"] = Connector("lookup_my_payment", "blocked", _lookup_my_payment)
    registry["handoff_to_official"] = Connector("handoff_to_official", "live", _handoff_to_official)
    return registry


def dispatch(registry: dict[str, Connector], call: ToolCall) -> Message:
    """Resolve a tool call to a `role="tool"` message. An unknown tool is a blocked error, never a guess."""
    conn = registry.get(call.name)
    result = conn.call(call.arguments) if conn else ConnectorResult({"error": f"unknown tool {call.name}"}, "blocked")
    return Message(role="tool", tool_call_id=call.id, name=call.name, content=result.to_json())


def statuses(registry: dict[str, Connector]) -> dict[str, ConnectorStatus]:
    """Per-connector status, for a deployment gate that must refuse a domain not yet backed by a live source."""
    return {name: c.status for name, c in registry.items()}
