"""Confidence engine (spec §34): computed from evidence, never from the model's self-report.

Signals: schema validity and validator outcome (gate), tool/connector existence in the declared sets, benchmark
category score for the serving model, and the route's recent success rate. Each signal is a 0–1 factor with a
weight; the result is the weighted mean of the signals that apply."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, Field

from protea.schemas.generation import GenerationRequest, GenerationResponse


class ConfidenceSignal(BaseModel):
    name: str
    value: float = Field(ge=0.0, le=1.0)
    weight: float = Field(gt=0.0)
    detail: str = ""


class Confidence(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    signals: list[ConfidenceSignal] = Field(default_factory=list)


class SuccessHistory:
    """Sliding window of outcomes per route and task type; feeds the historical signal and the observability event."""

    def __init__(self, window: int = 200):
        self.window = window
        self._outcomes: dict[str, deque[bool]] = {}

    def record(self, route: str, task_type: str, ok: bool) -> None:
        self._outcomes.setdefault(f"{route}:{task_type}", deque(maxlen=self.window)).append(ok)

    def rate(self, route: str, task_type: str) -> tuple[float | None, int]:
        window = self._outcomes.get(f"{route}:{task_type}")
        if not window:
            return None, 0
        return sum(window) / len(window), len(window)


def _tool_existence(request: GenerationRequest, response: GenerationResponse) -> ConfidenceSignal | None:
    if not response.tool_calls:
        return None
    declared = {t.name for t in request.tools or []}
    known = sum(1 for c in response.tool_calls if c.name in declared)
    value = known / len(response.tool_calls)
    return ConfidenceSignal(
        name="tools_exist", value=value, weight=2.0, detail=f"{known}/{len(response.tool_calls)} declared"
    )


def compute_confidence(
    request: GenerationRequest,
    response: GenerationResponse,
    *,
    gate_valid: bool | None,
    gate_repairs: int = 0,
    benchmark_score: float | None,
    history_rate: float | None,
    history_n: int = 0,
) -> Confidence:
    signals: list[ConfidenceSignal] = []
    if gate_valid is not None:
        value = 0.0 if not gate_valid else (1.0 if gate_repairs == 0 else 0.7)
        signals.append(ConfidenceSignal(name="schema_valid", value=value, weight=3.0, detail=f"repairs={gate_repairs}"))
    elif response.finish_reason in ("length", "error", "refusal"):
        signals.append(ConfidenceSignal(name="finish_reason", value=0.2, weight=2.0, detail=response.finish_reason))
    else:
        signals.append(
            ConfidenceSignal(
                name="finish_reason", value=1.0 if (response.content or response.tool_calls) else 0.0, weight=1.0
            )
        )
    tools = _tool_existence(request, response)
    if tools is not None:
        signals.append(tools)
    if benchmark_score is not None:
        signals.append(ConfidenceSignal(name="benchmark", value=max(0.0, min(1.0, benchmark_score)), weight=2.0))
    if history_rate is not None and history_n >= 5:
        signals.append(ConfidenceSignal(name="history", value=history_rate, weight=1.0, detail=f"n={history_n}"))
    total = sum(s.weight for s in signals)
    score = sum(s.value * s.weight for s in signals) / total if total else 0.0
    return Confidence(score=round(score, 4), signals=signals)


def signals_dict(conf: Confidence) -> dict[str, Any]:
    return {s.name: s.value for s in conf.signals}
