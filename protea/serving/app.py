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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import GenerationRequest, GenerationResponse
from protea.serving.config import ServeConfig
from protea.serving.gate import GateResult, generate_validated
from protea.serving.metrics import Metrics
from protea.serving.openai_compat import (
    ChatCompletionRequest,
    sse_chunk,
    sse_final,
    to_chat_completion,
    to_generation_request,
)


class StructuredRequest(BaseModel):
    request: GenerationRequest
    schema_: dict[str, Any] = Field(alias="schema")
    max_repairs: int | None = None

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
        self.started_at = time.time()

    async def check_ready(self, force: bool = False) -> bool:
        if not force and time.time() - self.ready_checked_at < self.cfg.ready_ttl_s:
            return self.ready
        health = await self.backend.health()
        self.ready, self.ready_detail = health.ok, health.detail or "ok"
        self.ready_checked_at = time.time()
        self.metrics.set("protea_backend_ready", 1.0 if self.ready else 0.0)
        return self.ready


def _tenant_ref(request: Request) -> str | None:
    raw = request.headers.get("x-protea-tenant")
    return hashlib.sha256(raw.encode()).hexdigest()[:16] if raw else None


def create_app(cfg: ServeConfig, backend: ModelProvider, *, token: str | None = None) -> FastAPI:
    state = FacadeState(cfg, backend, token)
    if cfg.require_token and not token:
        raise ValueError("require_token is set but no token was provided (PROTEA_FACADE_TOKEN)")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await state.check_ready(force=True)
        except ProviderError as exc:
            state.ready, state.ready_detail = False, str(exc)
        yield
        state.draining = True
        deadline = time.time() + cfg.drain_timeout_s
        while state.in_flight and time.time() < deadline:
            await asyncio.sleep(0.05)

    app = FastAPI(title="Protea inference facade", version="0.1", lifespan=lifespan)
    app.state.facade = state

    async def authorised(request: Request) -> None:
        if not cfg.require_token:
            return
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not (supplied and hmac.compare_digest(supplied, state.token or "")):
            raise HTTPException(401, "missing or invalid bearer token")

    def served_model(name: str) -> str:
        if name in cfg.model_aliases():
            return cfg.served_model
        raise HTTPException(404, f"unknown model {name!r}; served: {sorted(cfg.model_aliases())}")

    @app.middleware("http")
    async def observe(request: Request, call_next):
        if state.draining:
            return JSONResponse({"error": "shutting down"}, status_code=503, headers={"connection": "close"})
        route = request.url.path
        started = time.perf_counter()
        state.in_flight += 1
        state.metrics.set("protea_in_flight_requests", state.in_flight)
        try:
            response = await call_next(request)
        finally:
            state.in_flight -= 1
            state.metrics.set("protea_in_flight_requests", state.in_flight)
        elapsed = time.perf_counter() - started
        state.metrics.inc("protea_requests_total", {"route": route, "status": str(response.status_code)})
        if route.startswith("/v1/"):
            state.metrics.observe(elapsed, {"route": route})
        return response

    # ---- operations ---------------------------------------------------------------------------
    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "uptime_s": round(time.time() - state.started_at, 1)}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            ok = await state.check_ready()
        except ProviderError as exc:
            ok, state.ready_detail = False, str(exc)
        body = {"ready": ok, "backend": f"{backend.name}:{backend.model}", "detail": state.ready_detail}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(state.metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/v1/models")
    async def models(_: None = Depends(authorised)) -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": alias, "object": "model", "owned_by": "protea", "backend": f"{backend.name}:{backend.model}"}
                for alias in sorted(cfg.model_aliases())
            ],
        }

    # ---- OpenAI-compatible ----------------------------------------------------------------------
    def _account(resp: GenerationResponse) -> None:
        state.metrics.inc("protea_tokens_total", {"direction": "input"}, resp.usage.input_tokens)
        state.metrics.inc("protea_tokens_total", {"direction": "output"}, resp.usage.output_tokens)

    def _provider_error(exc: ProviderError) -> HTTPException:
        state.metrics.inc("protea_backend_errors_total", {"provider": exc.provider})
        return HTTPException(502 if exc.retryable else 500, str(exc)[:300])

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest, request: Request, _: None = Depends(authorised)):
        model = served_model(body.model)
        gen = to_generation_request(body, task_type=body.metadata.get("task_type"), tenant_ref=_tenant_ref(request))
        gen.metadata.request_id = request.headers.get("x-request-id") or gen.metadata.request_id
        if not body.stream:
            try:
                resp = await backend.generate(gen)
            except ProviderError as exc:
                raise _provider_error(exc) from exc
            _account(resp)
            return to_chat_completion(resp, model, f"chatcmpl-{gen.metadata.request_id[:24]}")
        chunk_id = f"chatcmpl-{gen.metadata.request_id[:24]}"

        async def events() -> AsyncIterator[str]:
            yield sse_chunk(chunk_id, model, {"role": "assistant", "content": ""})
            try:
                async for chunk in backend.stream(gen):
                    if chunk.type == "delta" and chunk.text:
                        yield sse_chunk(chunk_id, model, {"content": chunk.text})
                    elif chunk.type == "done" and chunk.response is not None:
                        _account(chunk.response)
                        yield sse_final(chunk_id, model, chunk.response)
            except ProviderError as exc:
                state.metrics.inc("protea_backend_errors_total", {"provider": exc.provider})
                yield f'data: {{"error": {str(exc)[:200]!r}}}\n\ndata: [DONE]\n\n'.replace("'", '"')

        return StreamingResponse(events(), media_type="text/event-stream", headers={"cache-control": "no-cache"})

    # ---- native contract --------------------------------------------------------------------------
    @app.post("/v1/generate", response_model=GenerationResponse)
    async def generate(gen: GenerationRequest, request: Request, _: None = Depends(authorised)) -> GenerationResponse:
        gen.metadata.tenant_ref = gen.metadata.tenant_ref or _tenant_ref(request)
        gen.metadata.channel = "facade"
        try:
            resp = await backend.generate(gen)
        except ProviderError as exc:
            raise _provider_error(exc) from exc
        _account(resp)
        return resp

    @app.post("/v1/generate/structured")
    async def generate_structured(body: StructuredRequest, request: Request, _: None = Depends(authorised)):
        gen = body.request
        gen.metadata.tenant_ref = gen.metadata.tenant_ref or _tenant_ref(request)
        gen.metadata.channel = "facade"
        repairs = body.max_repairs if body.max_repairs is not None else cfg.max_repairs
        try:
            result: GateResult = await generate_validated(backend, gen, body.schema_, max_repairs=repairs)
        except ProviderError as exc:
            raise _provider_error(exc) from exc
        for r in result.responses:
            _account(r)
        state.metrics.inc("protea_gate_total", {"outcome": "valid" if result.valid else "invalid"})
        if result.repairs:
            state.metrics.inc("protea_gate_repairs_total", value=result.repairs)
        payload = {"valid": result.valid, "output": result.output, "errors": result.errors, "repairs": result.repairs}
        if not result.valid:
            payload["raw"] = result.raw
            return JSONResponse(payload, status_code=422)
        return payload

    return app
