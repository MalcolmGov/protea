"""Route and fallback events (spec §56, §36): every decision and every fallback is recorded, without prompt text."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger("protea.router")


class FallbackEvent(BaseModel):
    from_route: str
    to_route: str
    reason: str
    attempt: int


class RouteEvent(BaseModel):
    """One routed call. Contains identifiers and outcomes only — never prompt or completion text."""

    request_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    task_type: str
    complexity: str
    privacy: str
    tenant_ref: str | None = None
    canary: bool = False
    pinned: bool = False
    chosen_route: str
    served_route: str | None = None
    served_model: str | None = None
    reason: str
    attempts: int = 0
    fallbacks: list[FallbackEvent] = Field(default_factory=list)
    confidence: float | None = None
    gate_valid: bool | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    ok: bool = False
    error: str | None = None


class RouteSink(Protocol):
    def record(self, event: RouteEvent) -> None: ...


class LoggingRouteSink:
    def record(self, event: RouteEvent) -> None:
        logger.info("route %s", event.model_dump_json())


class InMemoryRouteSink:
    def __init__(self) -> None:
        self.events: list[RouteEvent] = []

    def record(self, event: RouteEvent) -> None:
        self.events.append(event)


class JsonlRouteSink:
    """Append-only JSONL, one event per line; the Phase 9 dashboards read this or the platform's own table."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: RouteEvent) -> None:
        line = json.dumps(event.model_dump(mode="json")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
