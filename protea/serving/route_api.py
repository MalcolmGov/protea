"""Routed endpoints on the facade (`/v1/route/*`): the caller sends a generation request and a routing hint;
the router picks the model, executes with validated fallback and returns the decision alongside the answer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from protea.router import ModelRouter, NoRouteError, RouteOutcome, describe
from protea.schemas.generation import GenerationRequest
from protea.serving.app import ERROR_RESPONSES, FacadeState, _authorised, _state, _tenant_ref
from protea.serving.gate import MAX_REPAIR_ROUNDS


class RoutedRequest(BaseModel):
    request: GenerationRequest
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    task_type: str | None = None  # explicit hint; otherwise classified from the request shape
    privacy: str = "standard"  # standard | strict (self-hosted only)
    cost_policy: str = "balanced"  # cheapest | balanced | best
    connector_count: int = Field(default=0, ge=0)
    pinned_route: str | None = None
    max_repairs: int | None = Field(default=None, ge=0, le=MAX_REPAIR_ROUNDS)

    model_config = {"populate_by_name": True}


route_api = APIRouter(
    prefix="/v1/route",
    dependencies=[Depends(_authorised)],
    responses={**ERROR_RESPONSES, 409: {"description": "no route may serve the request (privacy / task policy)"}},
)


def _router(state: FacadeState = Depends(_state)) -> ModelRouter:
    router = getattr(state, "router", None)
    if router is None:
        raise HTTPException(501, "routing is not configured on this facade (serve.routing_policy)")
    return router


def _payload(outcome: RouteOutcome) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": outcome.ok,
        "route": outcome.decision.route,
        "served_route": outcome.served_route,
        "served_model": outcome.event.served_model,
        "reason": outcome.decision.reason,
        "attempts": outcome.event.attempts,
        "fallbacks": [f.model_dump() for f in outcome.event.fallbacks],
        "confidence": outcome.confidence.model_dump() if outcome.confidence else None,
        "response": outcome.response.model_dump(mode="json") if outcome.response else None,
        "error": outcome.error,
    }
    if outcome.gate is not None:
        out.update(
            valid=outcome.gate.valid,
            output=outcome.gate.output,
            errors=outcome.gate.errors,
            repairs=outcome.gate.repairs,
        )
    return out


def _responses(outcome: RouteOutcome) -> list:
    if outcome.gate is not None:
        return list(outcome.gate.responses)
    return [outcome.response] if outcome.response is not None else []


async def _run(body: RoutedRequest, request: Request, state: FacadeState, router: ModelRouter, schema: dict | None):
    gen = body.request
    gen.metadata.tenant_ref = gen.metadata.tenant_ref or _tenant_ref(request)
    gen.metadata.channel = "facade"
    route_req = describe(
        gen,
        hint=body.task_type,
        privacy=body.privacy,
        cost_policy=body.cost_policy,
        connector_count=body.connector_count,
        pinned_route=body.pinned_route,
    )
    repairs = body.max_repairs if body.max_repairs is not None else state.cfg.max_repairs
    try:
        outcome = await router.execute(gen, route_request=route_req, schema=schema, max_repairs=repairs)
    except NoRouteError as exc:  # policy refusal: the documented 409, as a response rather than an exception
        return JSONResponse({"detail": str(exc)}, status_code=409)
    state.metrics.inc("protea_route_total", {"route": outcome.served_route or "none", "ok": str(outcome.ok).lower()})
    if outcome.event.fallbacks:
        state.metrics.inc("protea_route_fallbacks_total", value=len(outcome.event.fallbacks))
    for r in _responses(outcome):
        state.account(r)
    payload = _payload(outcome)
    if outcome.ok:
        return payload
    if outcome.response is None:
        return JSONResponse(payload, status_code=502)
    return JSONResponse(payload, status_code=422)


_NO_ROUTE = {409: {"description": "no route may serve the request (privacy / task policy)"}}


@route_api.post(
    "/generate", responses={**_NO_ROUTE, 422: {"description": "every route failed confidence or finish checks"}}
)
async def route_generate(
    body: RoutedRequest,
    request: Request,
    state: FacadeState = Depends(_state),
    router: ModelRouter = Depends(_router),
):
    """Route a free-form generation; the response carries the decision, fallbacks and confidence."""
    return await _run(body, request, state, router, None)


@route_api.post(
    "/structured",
    responses={
        **_NO_ROUTE,
        400: {"description": "schema is required"},
        422: {"description": "no route produced schema-valid output"},
    },
)
async def route_structured(
    body: RoutedRequest,
    request: Request,
    state: FacadeState = Depends(_state),
    router: ModelRouter = Depends(_router),
):
    """Route a structured generation through the validation gate with fallback on invalid output."""
    if body.schema_ is None:
        raise HTTPException(400, "schema is required for /v1/route/structured")
    return await _run(body, request, state, router, body.schema_)
