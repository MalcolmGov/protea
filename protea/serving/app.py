"""The Protea inference facade (spec §28, §31): OpenAI-compatible gateway + native contract + validation gate.

Backends are any `ModelProvider` (vLLM via the `protea` provider in production, `mock` in tests). aria's runtime
points MIAI_MODEL_GATEWAY_URL at this service; the Zara-specific routes (agent generate / repair / optimise) live
in aria and call the native endpoints here (ADR-001).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import GenerationRequest, GenerationResponse
from protea.serving.config import ServeConfig
from protea.serving.gate import MAX_REPAIR_ROUNDS, GateResult, generate_validated
from protea.serving.metrics import Metrics
from protea.serving.openai_compat import (
    ChatCompletionRequest,
    sse_chunk,
    sse_final,
    to_chat_completion,
    to_generation_request,
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "missing or invalid bearer token"},
    404: {"description": "unknown model name"},
    422: {"description": "structured output still invalid after repair"},
    500: {"description": "backend failed"},
    502: {"description": "backend unavailable (retryable)"},
    503: {"description": "draining or backend not ready"},
}


class StructuredRequest(BaseModel):
    request: GenerationRequest
    schema_: dict[str, Any] = Field(alias="schema")
    max_repairs: int | None = Field(default=None, ge=0, le=MAX_REPAIR_ROUNDS)

    model_config = {"populate_by_name": True}


class FacadeState:
    def __init__(self, cfg: ServeConfig, backend: ModelProvider, token: str | None):
        self.cfg = cfg
        self.backend = backend
        self.token = token
        self.metrics = Metrics()
        self.ready = False
        self.ready_detail = "not checked"
        self.ready_checked_at = 0.0
        self.draining = False
        self.in_flight = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.started_at = time.time()

    async def check_ready(self, force: bool = False) -> bool:
        if not force and time.time() - self.ready_checked_at < self.cfg.ready_ttl_s:
            return self.ready
        try:
            health = await self.backend.health()
            self.ready, self.ready_detail = health.ok, health.detail or "ok"
        except ProviderError as exc:
            self.ready, self.ready_detail = False, str(exc)
        self.ready_checked_at = time.time()
        self.metrics.set("protea_backend_ready", 1.0 if self.ready else 0.0)
        return self.ready

    def enter(self) -> None:
        self.in_flight += 1
        self.idle.clear()
        self.metrics.set("protea_in_flight_requests", self.in_flight)

    def leave(self) -> None:
        self.in_flight -= 1
        if self.in_flight == 0:
            self.idle.set()
        self.metrics.set("protea_in_flight_requests", self.in_flight)

    async def drain(self) -> None:
        self.draining = True
        try:
            await asyncio.wait_for(self.idle.wait(), timeout=self.cfg.drain_timeout_s)
        except TimeoutError:
            pass

    def served_model(self, name: str) -> str:
        if name in self.cfg.model_aliases():
            return self.cfg.served_model
        raise HTTPException(404, f"unknown model {name!r}; served: {sorted(self.cfg.model_aliases())}")

    def account(self, resp: GenerationResponse) -> None:
        self.metrics.inc("protea_tokens_total", {"direction": "input"}, resp.usage.input_tokens)
        self.metrics.inc("protea_tokens_total", {"direction": "output"}, resp.usage.output_tokens)

    def provider_error(self, exc: ProviderError) -> HTTPException:
        self.metrics.inc("protea_backend_errors_total", {"provider": exc.provider})
        return HTTPException(502 if exc.retryable else 500, str(exc)[:300])


def _state(request: Request) -> FacadeState:
    return request.app.state.facade


def _authorised(request: Request, state: FacadeState = Depends(_state)) -> None:
    if not state.cfg.require_token:
        return
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not (supplied and hmac.compare_digest(supplied, state.token or "")):
        raise HTTPException(401, "missing or invalid bearer token")


def _tenant_ref(request: Request) -> str | None:
    raw = request.headers.get("x-protea-tenant")
    return hashlib.sha256(raw.encode()).hexdigest()[:16] if raw else None


ops = APIRouter()
api = APIRouter(prefix="/v1", dependencies=[Depends(_authorised)], responses=ERROR_RESPONSES)


# ---- operations -------------------------------------------------------------------------------
@ops.get("/healthz")
async def healthz(state: FacadeState = Depends(_state)) -> dict[str, Any]:
    return {"status": "ok", "uptime_s": round(time.time() - state.started_at, 1)}


@ops.get("/readyz")
async def readyz(state: FacadeState = Depends(_state)) -> JSONResponse:
    ok = await state.check_ready()
    backend = f"{state.backend.name}:{state.backend.model}"
    return JSONResponse({"ready": ok, "backend": backend, "detail": state.ready_detail}, status_code=200 if ok else 503)


@ops.get("/metrics")
async def metrics(state: FacadeState = Depends(_state)) -> PlainTextResponse:
    return PlainTextResponse(state.metrics.render(), media_type="text/plain; version=0.0.4")


# ---- OpenAI-compatible --------------------------------------------------------------------------
@api.get("/models")
async def models(state: FacadeState = Depends(_state)) -> dict[str, Any]:
    backend = f"{state.backend.name}:{state.backend.model}"
    return {
        "object": "list",
        "data": [
            {"id": alias, "object": "model", "owned_by": "protea", "backend": backend}
            for alias in sorted(state.cfg.model_aliases())
        ],
    }


async def _stream_events(state: FacadeState, gen: GenerationRequest, model: str, chunk_id: str) -> AsyncIterator[str]:
    yield sse_chunk(chunk_id, model, {"role": "assistant", "content": ""})
    try:
        async for chunk in state.backend.stream(gen):
            if chunk.type == "delta" and chunk.text:
                yield sse_chunk(chunk_id, model, {"content": chunk.text})
            elif chunk.type == "done" and chunk.response is not None:
                state.account(chunk.response)
                yield sse_final(chunk_id, model, chunk.response)
    except ProviderError as exc:
        state.metrics.inc("protea_backend_errors_total", {"provider": exc.provider})
        yield sse_chunk(chunk_id, model, {"content": ""}, finish="error") + "data: [DONE]\n\n"


@api.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request, state: FacadeState = Depends(_state)):
    model = state.served_model(body.model)
    gen = to_generation_request(body, task_type=body.metadata.get("task_type"), tenant_ref=_tenant_ref(request))
    gen.metadata.request_id = request.headers.get("x-request-id") or gen.metadata.request_id
    chunk_id = f"chatcmpl-{gen.metadata.request_id[:24]}"
    if body.stream:
        return StreamingResponse(
            _stream_events(state, gen, model, chunk_id),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache"},
        )
    try:
        resp = await state.backend.generate(gen)
    except ProviderError as exc:
        raise state.provider_error(exc) from exc
    state.account(resp)
    return to_chat_completion(resp, model, chunk_id)


# ---- native contract ------------------------------------------------------------------------------
@api.post("/generate")
async def generate(
    gen: GenerationRequest, request: Request, state: FacadeState = Depends(_state)
) -> GenerationResponse:
    gen.metadata.tenant_ref = gen.metadata.tenant_ref or _tenant_ref(request)
    gen.metadata.channel = "facade"
    try:
        resp = await state.backend.generate(gen)
    except ProviderError as exc:
        raise state.provider_error(exc) from exc
    state.account(resp)
    return resp


@api.post("/generate/structured")
async def generate_structured(body: StructuredRequest, request: Request, state: FacadeState = Depends(_state)):
    gen = body.request
    gen.metadata.tenant_ref = gen.metadata.tenant_ref or _tenant_ref(request)
    gen.metadata.channel = "facade"
    repairs = body.max_repairs if body.max_repairs is not None else state.cfg.max_repairs
    try:
        result: GateResult = await generate_validated(state.backend, gen, body.schema_, max_repairs=repairs)
    except ProviderError as exc:
        raise state.provider_error(exc) from exc
    for r in result.responses:
        state.account(r)
    state.metrics.inc("protea_gate_total", {"outcome": "valid" if result.valid else "invalid"})
    if result.repairs:
        state.metrics.inc("protea_gate_repairs_total", value=result.repairs)
    payload = {"valid": result.valid, "output": result.output, "errors": result.errors, "repairs": result.repairs}
    if not result.valid:
        payload["raw"] = result.raw
        return JSONResponse(payload, status_code=422)
    return payload


# ---- app factory ------------------------------------------------------------------------------
async def _observe(request: Request, call_next):
    state: FacadeState = request.app.state.facade
    if state.draining:
        return JSONResponse({"error": "shutting down"}, status_code=503, headers={"connection": "close"})
    route = request.url.path
    started = time.perf_counter()
    state.enter()
    try:
        response = await call_next(request)
    finally:
        state.leave()
    state.metrics.inc("protea_requests_total", {"route": route, "status": str(response.status_code)})
    if route.startswith("/v1/"):
        state.metrics.observe(time.perf_counter() - started, {"route": route})
    return response


def create_app(cfg: ServeConfig, backend: ModelProvider, *, token: str | None = None) -> FastAPI:
    if cfg.require_token and not token:
        raise ValueError("require_token is set but no token was provided (PROTEA_FACADE_TOKEN)")
    state = FacadeState(cfg, backend, token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await state.check_ready(force=True)
        yield
        await state.drain()

    app = FastAPI(title="Protea inference facade", version="0.1", lifespan=lifespan)
    app.state.facade = state
    app.middleware("http")(_observe)
    app.include_router(ops)
    app.include_router(api)
    return app
