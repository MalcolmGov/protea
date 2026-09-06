"""ModelProvider — the one interface Protea, Zara and any other consumer speak (spec §3.2)."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from protea.schemas.generation import (
    GenerationRequest,
    GenerationResponse,
    Message,
    ModelChunk,
    ModelHealth,
    UsageEvent,
)

logger = logging.getLogger("protea.providers")
T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable
        self.status = status


class ProviderNotConfigured(ProviderError):
    def __init__(self, provider: str, missing: str):
        super().__init__(provider, f"not configured ({missing} is not set)", retryable=False)


class StructuredOutputError(ProviderError):
    def __init__(self, provider: str, raw_text: str | None, errors: str):
        super().__init__(provider, f"structured output failed validation: {errors}", retryable=True)
        self.raw_text = raw_text


class UsageSink(Protocol):
    def record(self, event: UsageEvent) -> None: ...


class LoggingUsageSink:
    def record(self, event: UsageEvent) -> None:
        logger.info("usage %s", event.model_dump_json())


class InMemoryUsageSink:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    def record(self, event: UsageEvent) -> None:
        self.events.append(event)


def _strip_fence(text: str) -> str:
    """Return the body of the first ```-fenced block (optionally tagged json), else the text unchanged.
    Plain string searches: linear time, no regex backtracking on adversarial output."""
    start = text.find("```")
    if start == -1:
        return text
    body_start = start + 3
    if text.startswith("json", body_start):
        body_start += 4
    end = text.find("```", body_start)
    if end == -1:
        return text
    return text[body_start:end].strip()


def extract_json(text: str | None) -> Any:
    """Parse JSON from model text, tolerating code fences and leading prose."""
    if not text:
        raise ValueError("empty response")
    candidate = _strip_fence(text.strip())
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            return json.loads(candidate[start : end + 1])
        raise


class ModelProvider(ABC):
    """Async provider contract. Subclasses implement `_generate`; streaming and structured
    generation have working defaults that adapters may override with native support."""

    name: str = "abstract"
    supports_native_json_schema: bool = False

    def __init__(self, model: str, usage_sink: UsageSink | None = None):
        self.model = model
        self.usage_sink = usage_sink or LoggingUsageSink()

    # ---- required -------------------------------------------------------------------------
    @abstractmethod
    async def _generate(self, request: GenerationRequest) -> GenerationResponse: ...

    # ---- public surface ----------------------------------------------------------------------
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        started = time.perf_counter()
        try:
            response = await self._generate(request)
        except ProviderError as exc:
            self._emit(request, None, error=str(exc), latency_ms=int((time.perf_counter() - started) * 1000))
            raise
        response.latency_ms = response.latency_ms or int((time.perf_counter() - started) * 1000)
        response.provider = response.provider or self.name
        response.model = response.model or self.model
        self._emit(request, response)
        return response

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ModelChunk]:
        """Default: no token streaming; yields the whole text once, then `done`."""
        response = await self.generate(request)
        if response.content:
            yield ModelChunk(type="delta", text=response.content)
        yield ModelChunk(type="done", response=response)

    async def generate_structured(self, request: GenerationRequest, schema: type[T]) -> T:
        req = request.model_copy(
            update={
                "response_schema": schema.model_json_schema(),
                "response_schema_name": schema.__name__,
            }
        )
        if not self.supports_native_json_schema:
            req = self._with_schema_instruction(req)
        response = await self.generate(req)
        try:
            return schema.model_validate(extract_json(response.content))
        except (ValueError, ValidationError) as exc:
            raise StructuredOutputError(self.name, response.content, str(exc)) from exc

    async def health(self) -> ModelHealth:
        started = time.perf_counter()
        try:
            await self._generate(
                GenerationRequest(
                    messages=[Message(role="user", content="Reply with the single word OK.")], max_tokens=8
                )
            )
            return ModelHealth(
                provider=self.name, model=self.model, ok=True, latency_ms=int((time.perf_counter() - started) * 1000)
            )
        except ProviderError as exc:
            return ModelHealth(provider=self.name, model=self.model, ok=False, detail=str(exc))

    # ---- helpers ------------------------------------------------------------------------------
    @staticmethod
    def _with_schema_instruction(req: GenerationRequest) -> GenerationRequest:
        instruction = (
            "Respond with a single JSON object and nothing else. It must validate against this JSON Schema:\n"
            + json.dumps(req.response_schema)
        )
        msgs = list(req.messages)
        if msgs and msgs[0].role == "system":
            msgs[0] = msgs[0].model_copy(update={"content": (msgs[0].content or "") + "\n\n" + instruction})
        else:
            msgs.insert(0, Message(role="system", content=instruction))
        return req.model_copy(update={"messages": msgs})

    def _emit(
        self,
        request: GenerationRequest,
        response: GenerationResponse | None,
        *,
        error: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        meta = request.metadata
        event = UsageEvent(
            request_id=meta.request_id,
            provider=self.name,
            model=response.model if response else self.model,
            channel=meta.channel,
            task_type=meta.task_type,
            tenant_ref=meta.tenant_ref,
            agent_id=meta.agent_id,
            input_tokens=response.usage.input_tokens if response else 0,
            output_tokens=response.usage.output_tokens if response else 0,
            cache_read_tokens=response.usage.cache_read_tokens if response else 0,
            latency_ms=response.latency_ms if response else latency_ms,
            finish_reason=response.finish_reason if response else "error",
            fallback=meta.fallback,
            error=error,
        )
        try:
            self.usage_sink.record(event)
        except Exception:  # telemetry must never break a request
            logger.exception("usage sink failed")
