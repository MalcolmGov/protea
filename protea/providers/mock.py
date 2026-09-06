"""Deterministic provider for tests, dry runs and ZaraBench harness checks. Never used in production paths."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from protea.providers.base import ModelProvider
from protea.schemas.generation import GenerationRequest, GenerationResponse, ModelHealth, ToolCall, Usage

Scripted = str | dict[str, Any] | ToolCall | list[ToolCall] | GenerationResponse
Responder = Callable[[GenerationRequest], Scripted]


class MockProvider(ModelProvider):
    name = "mock"
    supports_native_json_schema = True

    def __init__(self, responses: list[Scripted] | Responder | None = None, model: str = "mock-1", **kw: Any):
        super().__init__(model=model, **kw)
        self._responder = responses if callable(responses) else None
        self._queue = list(responses) if isinstance(responses, list) else []
        self.requests: list[GenerationRequest] = []

    def _next(self, request: GenerationRequest) -> Scripted:
        if self._responder:
            return self._responder(request)
        if self._queue:
            return self._queue.pop(0)
        return "OK"

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        scripted = self._next(request)
        if isinstance(scripted, GenerationResponse):
            return scripted
        prompt_chars = sum(len(m.content or "") for m in request.messages)
        usage = Usage(input_tokens=max(1, prompt_chars // 4), output_tokens=8)
        if isinstance(scripted, ToolCall):
            return GenerationResponse(
                tool_calls=[scripted], finish_reason="tool_calls", usage=usage, provider=self.name, model=self.model
            )
        if isinstance(scripted, list):
            return GenerationResponse(
                tool_calls=scripted, finish_reason="tool_calls", usage=usage, provider=self.name, model=self.model
            )
        if isinstance(scripted, dict):
            return GenerationResponse(content=json.dumps(scripted), usage=usage, provider=self.name, model=self.model)
        return GenerationResponse(content=str(scripted), usage=usage, provider=self.name, model=self.model)

    async def health(self) -> ModelHealth:
        """Always healthy, and never consumes a scripted response (readiness probes must not eat test scripts)."""
        return ModelHealth(provider=self.name, model=self.model, ok=True, latency_ms=0)
