"""The model router (spec §55–§57): decide, execute with validated fallback, score confidence, record events.

Decision order for a request:
1. a pinned route (per-agent version pin) wins if it satisfies the hard constraints;
2. hard constraints drop candidates: privacy=strict → self-hosted only, declared task types, complexity ceiling;
3. benchmark-gated candidates (Protea) need a capability-matrix score ≥ the category threshold and the caller
   must be inside the canary share;
4. the survivors are ordered by the cost policy; nothing eligible → the policy's default route.

Execution tries the chosen route, then the fallback chain, up to `max_attempts`. A structured call is only
accepted when the validation gate passes and the evidence-based confidence clears `min_confidence`.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from protea.providers.base import ModelProvider, ProviderError
from protea.router.capability import CapabilityMatrix, load_matrix
from protea.router.classifier import complexity as _complexity
from protea.router.classifier import describe
from protea.router.confidence import Confidence, SuccessHistory, compute_confidence
from protea.router.events import FallbackEvent, LoggingRouteSink, RouteEvent, RouteSink
from protea.router.policy import TASK_CATEGORY, Candidate, RouteRequest, RoutingPolicy
from protea.schemas.generation import GenerationRequest, GenerationResponse
from protea.serving.gate import GateResult, generate_validated

logger = logging.getLogger("protea.router")
_RANK = {"low": 0, "medium": 1, "high": 2}
_BAD_FINISH = ("error", "refusal", "length")


class NoRouteError(Exception):
    """No candidate may serve the request (typically privacy=strict without an eligible self-hosted route)."""


class CandidateVerdict(BaseModel):
    route: str
    eligible: bool
    reason: str
    benchmark_score: float | None = None


class RouteDecision(BaseModel):
    route: str
    provider: str
    model: str | None = None
    version: str | None = None
    reason: str
    task_type: str
    category: str
    complexity: str
    threshold: float
    canary: bool = False
    pinned: bool = False
    fallbacks: list[str] = Field(default_factory=list)
    considered: list[CandidateVerdict] = Field(default_factory=list)


class RouteOutcome(BaseModel):
    decision: RouteDecision
    ok: bool
    served_route: str | None = None
    response: GenerationResponse | None = None
    gate: GateResult | None = None
    confidence: Confidence | None = None
    event: RouteEvent
    error: str | None = None


def canary_bucket(tenant_ref: str) -> float:
    """Stable 0–100 bucket per tenant so the same tenant always lands on the same side of the canary line."""
    digest = hashlib.sha256(tenant_ref.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 10_000 / 100.0


def _order_key(cost_policy: str, preference: list[str]) -> Callable[[Candidate], tuple[int, ...]]:
    """cheapest: cost then quality; best: quality then cost; balanced: benchmark-proven self-hosted routes first,
    then the operator's declared order (fallback_order), then quality."""
    if cost_policy == "cheapest":
        return lambda c: (c.cost_tier, -c.quality_tier)
    if cost_policy == "best":
        return lambda c: (-c.quality_tier, c.cost_tier)

    def key(c: Candidate) -> tuple[int, ...]:
        rank = preference.index(c.name) if c.name in preference else len(preference)
        return (0 if c.requires_benchmark else 1, rank, -c.quality_tier, c.cost_tier)

    return key


class ModelRouter:
    def __init__(
        self,
        policy: RoutingPolicy,
        *,
        matrix: CapabilityMatrix | None = None,
        providers: dict[str, ModelProvider] | None = None,
        provider_factory: Callable[[Candidate], ModelProvider] | None = None,
        sink: RouteSink | None = None,
        history: SuccessHistory | None = None,
    ):
        self.policy = policy
        self.matrix = matrix if matrix is not None else load_matrix(Path(policy.reports_dir))
        self._providers: dict[str, ModelProvider] = dict(providers or {})
        self._factory = provider_factory or _default_factory
        self.sink = sink or LoggingRouteSink()
        self.history = history or SuccessHistory()

    # ---- providers ---------------------------------------------------------------------------------
    def provider_for(self, route: str) -> ModelProvider:
        if route not in self._providers:
            self._providers[route] = self._factory(self.policy.candidate(route))
        return self._providers[route]

    # ---- decision ----------------------------------------------------------------------------------
    def _hard_constraints(self, c: Candidate, req: RouteRequest, level: str) -> str | None:
        if req.privacy == "strict" and not c.self_hosted:
            return "privacy=strict requires a self-hosted route"
        if c.task_types and req.task_type not in c.task_types:
            return f"does not serve {req.task_type}"
        if _RANK[level] > _RANK[c.max_complexity]:
            return f"complexity {level} exceeds ceiling {c.max_complexity}"
        return None

    def _verdict(self, c: Candidate, req: RouteRequest, level: str, category: str, canary: bool) -> CandidateVerdict:
        blocked = self._hard_constraints(c, req, level)
        if blocked:
            return CandidateVerdict(route=c.name, eligible=False, reason=blocked)
        if not c.requires_benchmark:
            return CandidateVerdict(route=c.name, eligible=True, reason="no benchmark gate")
        key = f"{c.provider}:{c.model}"
        score = self.matrix.score(key, category)
        ok, reason = self.matrix.eligible(key, category, self.policy.threshold_for(category))
        if ok and not canary:
            return CandidateVerdict(
                route=c.name, eligible=False, reason="tenant outside canary share", benchmark_score=score
            )
        return CandidateVerdict(route=c.name, eligible=ok, reason=reason, benchmark_score=score)

    def _is_canary(self, req: RouteRequest) -> bool:
        pct = self.policy.canary_percent
        if pct >= 100.0:
            return True
        if pct <= 0.0 or not req.tenant_ref:
            return False
        return canary_bucket(req.tenant_ref) < pct

    def _pinned(self, req: RouteRequest, by_name: dict[str, CandidateVerdict]) -> tuple[Candidate | None, str]:
        verdict = by_name.get(req.pinned_route or "")
        if verdict is None:
            return None, ""
        if verdict.eligible:
            return self.policy.candidate(verdict.route), "pinned route"
        return None, f"pinned route {verdict.route} rejected ({verdict.reason}); "

    def _default(self, req: RouteRequest, level: str) -> Candidate:
        default = self.policy.candidate(self.policy.default_route)
        blocked = self._hard_constraints(default, req, level)
        if blocked:
            raise NoRouteError(f"no eligible route for {req.task_type} ({blocked})")
        return default

    def _fallbacks(self, chosen: str, by_name: dict[str, CandidateVerdict]) -> list[str]:
        order = [*self.policy.fallback_order, self.policy.default_route]
        names = [n for n in order if n != chosen and n in by_name and by_name[n].eligible]
        return list(dict.fromkeys(names))[: self.policy.max_attempts - 1]

    def route(self, req: RouteRequest) -> RouteDecision:
        level = _complexity(req.prompt_chars, req.tool_count, req.connector_count, req.financial)
        category = TASK_CATEGORY.get(req.task_type, "business_reasoning")
        canary = self._is_canary(req)
        verdicts = [self._verdict(c, req, level, category, canary) for c in self.policy.candidates]
        by_name = {v.route: v for v in verdicts}
        eligible = [self.policy.candidate(v.route) for v in verdicts if v.eligible]
        chosen, reason = self._pinned(req, by_name)
        pinned = chosen is not None
        if chosen is None and eligible:
            chosen = sorted(eligible, key=_order_key(req.cost_policy, self.policy.fallback_order))[0]
            reason += f"{req.cost_policy} policy: {by_name[chosen.name].reason}"
        if chosen is None:
            chosen, reason = self._default(req, level), reason + "no candidate eligible; default route"
        return RouteDecision(
            route=chosen.name,
            provider=chosen.provider,
            model=chosen.model,
            version=chosen.version,
            reason=reason,
            task_type=req.task_type,
            category=category,
            complexity=level,
            threshold=self.policy.threshold_for(category),
            canary=canary,
            pinned=pinned,
            fallbacks=self._fallbacks(chosen.name, by_name),
            considered=verdicts,
        )

    def explain(self, req: RouteRequest) -> dict[str, Any]:
        try:
            return self.route(req).model_dump(mode="json")
        except NoRouteError as exc:
            return {"route": None, "reason": str(exc)}

    # ---- execution -------------------------------------------------------------------------------------
    async def _attempt(
        self, route: str, request: GenerationRequest, schema: dict[str, Any] | None, max_repairs: int
    ) -> tuple[GenerationResponse, GateResult | None]:
        provider = self.provider_for(route)
        if schema is None:
            return await provider.generate(request), None
        gate = await generate_validated(provider, request, schema, max_repairs=max_repairs)
        return gate.responses[-1], gate

    def _judge(
        self,
        request: GenerationRequest,
        resp: GenerationResponse,
        gate: GateResult | None,
        task_type: str,
        route: str,
        category: str,
    ) -> tuple[bool, Confidence, str]:
        rate, n = self.history.rate(route, task_type)
        conf = compute_confidence(
            request,
            resp,
            gate_valid=gate.valid if gate else None,
            gate_repairs=gate.repairs if gate else 0,
            benchmark_score=self.matrix.score(f"{resp.provider}:{resp.model}", category),
            history_rate=rate,
            history_n=n,
        )
        if gate is not None and not gate.valid:
            return False, conf, f"validation failed after {gate.repairs} repair(s)"
        if resp.finish_reason in _BAD_FINISH:
            return False, conf, f"finish_reason={resp.finish_reason}"
        if conf.score < self.policy.min_confidence:
            return False, conf, f"confidence {conf.score:.2f} < {self.policy.min_confidence:.2f}"
        return True, conf, "ok"

    async def execute(
        self,
        request: GenerationRequest,
        *,
        route_request: RouteRequest | None = None,
        schema: dict[str, Any] | None = None,
        max_repairs: int = 1,
    ) -> RouteOutcome:
        req = route_request or describe(request)
        decision = self.route(req)
        event = RouteEvent(
            request_id=request.metadata.request_id,
            task_type=req.task_type,
            complexity=decision.complexity,
            privacy=req.privacy,
            tenant_ref=req.tenant_ref,
            canary=decision.canary,
            pinned=decision.pinned,
            chosen_route=decision.route,
            reason=decision.reason,
        )
        request.metadata.task_type = request.metadata.task_type or req.task_type
        outcome = RouteOutcome(decision=decision, ok=False, event=event)
        started = time.perf_counter()
        chain = [decision.route, *decision.fallbacks][: self.policy.max_attempts]
        for attempt, route in enumerate(chain, start=1):
            event.attempts = attempt
            call = (
                request
                if attempt == 1
                else request.model_copy(update={"metadata": request.metadata.model_copy(update={"fallback": True})})
            )
            try:
                resp, gate = await self._attempt(route, call, schema, max_repairs)
            except ProviderError as exc:
                outcome.error = str(exc)[:300]
                self.history.record(route, req.task_type, False)
                _note_fallback(event, chain, attempt, outcome.error)
                continue
            ok, conf, why = self._judge(call, resp, gate, req.task_type, route, decision.category)
            self._account(event, resp, gate, conf)
            event.served_route = route
            outcome.response, outcome.gate, outcome.confidence, outcome.served_route = resp, gate, conf, route
            self.history.record(route, req.task_type, ok)
            if ok:
                outcome.ok, outcome.error = True, None
                break
            outcome.error = why
            _note_fallback(event, chain, attempt, why)
        event.ok = outcome.ok
        event.error = None if outcome.ok else outcome.error
        event.latency_ms = int((time.perf_counter() - started) * 1000)
        self._emit(event)
        return outcome

    @staticmethod
    def _account(event: RouteEvent, resp: GenerationResponse, gate: GateResult | None, conf: Confidence) -> None:
        responses = gate.responses if gate else [resp]
        event.input_tokens += sum(r.usage.input_tokens for r in responses)
        event.output_tokens += sum(r.usage.output_tokens for r in responses)
        event.served_model = f"{resp.provider}:{resp.model}"
        event.confidence = conf.score
        event.gate_valid = gate.valid if gate else None

    def _emit(self, event: RouteEvent) -> None:
        try:
            self.sink.record(event)
        except Exception:  # telemetry must never break a request
            logger.exception("route sink failed")


def _note_fallback(event: RouteEvent, chain: list[str], attempt: int, why: str) -> None:
    if attempt < len(chain):
        event.fallbacks.append(
            FallbackEvent(from_route=chain[attempt - 1], to_route=chain[attempt], reason=why, attempt=attempt)
        )


def _default_factory(c: Candidate) -> ModelProvider:
    from protea.providers import build_provider

    return build_provider(c.provider, model=c.model)
