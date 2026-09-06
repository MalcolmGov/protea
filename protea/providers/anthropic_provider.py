"""Anthropic adapter on the official SDK. Uses the Messages API's native JSON-schema output."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from protea.providers.base import ModelProvider, ProviderError, ProviderNotConfigured
from protea.schemas.generation import (
    GenerationRequest,
    GenerationResponse,
    Message,
    ModelChunk,
    ToolCall,
    ToolSchema,
    Usage,
)


def _assistant_blocks(m: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if m.content:
        blocks.append({"type": "text", "text": m.content})
    blocks.extend({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in m.tool_calls)
    return blocks or [{"type": "text", "text": ""}]


def _is_tool_result_message(msg: dict[str, Any] | None) -> bool:
    if not msg or msg.get("role") != "user" or not isinstance(msg.get("content"), list):
        return False
    content = msg["content"]
    return bool(content) and content[0].get("type") == "tool_result"


def _append_tool_result(out: list[dict[str, Any]], m: Message) -> None:
    """Parallel tool results must be returned in ONE user message."""
    block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or ""}
    if out and _is_tool_result_message(out[-1]):
        out[-1]["content"].append(block)
    else:
        out.append({"role": "user", "content": [block]})


def to_anthropic_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content or "")
        elif m.role == "assistant":
            out.append({"role": "assistant", "content": _assistant_blocks(m)})
        elif m.role == "tool":
            _append_tool_result(out, m)
        else:
            out.append({"role": "user", "content": m.content or ""})
    return ("\n\n".join(system_parts) or None), out


def to_anthropic_tools(tools: list[ToolSchema] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    result = []
    for t in tools:
        tool: dict[str, Any] = {"name": t.name, "description": t.description, "input_schema": t.parameters}
        if t.strict:
            tool["strict"] = True
        result.append(tool)
    return result


class AnthropicProvider(ModelProvider):
    name = "anthropic"
    supports_native_json_schema = True

    def __init__(self, model: str, api_key: str | None, *, client: Any = None, timeout_s: float = 60.0, **kw: Any):
        super().__init__(model=model, **kw)
        if client is not None:
            self._client = client
        else:
            if not api_key:
                raise ProviderNotConfigured(self.name, "ANTHROPIC_API_KEY")
            import anthropic

            self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s)

    def _params(self, request: GenerationRequest) -> dict[str, Any]:
        system, messages = to_anthropic_messages(request.messages)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": messages,
            "temperature": request.temperature,
        }
        if system:
            params["system"] = system
        if request.stop:
            params["stop_sequences"] = request.stop
        tools = to_anthropic_tools(request.tools)
        if tools:
            params["tools"] = tools
        if request.response_schema:
            params["output_config"] = {"format": {"type": "json_schema", "schema": request.response_schema}}
        return params

    def _parse(self, msg: Any) -> GenerationResponse:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        finish = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "refusal": "refusal",
            "stop_sequence": "stop",
        }.get(msg.stop_reason or "", "other")
        usage = getattr(msg, "usage", None)
        return GenerationResponse(
            content="".join(text_parts) or None,
            tool_calls=calls,
            finish_reason=finish,  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            ),
            provider=self.name,
            model=getattr(msg, "model", None) or self.model,
        )

    def _wrap(self, exc: Exception) -> ProviderError:
        import anthropic

        if isinstance(
            exc,
            (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
            ),
        ):
            return ProviderError(self.name, str(exc), retryable=True, status=getattr(exc, "status_code", None))
        if isinstance(exc, anthropic.APIStatusError):
            return ProviderError(self.name, str(exc), retryable=False, status=exc.status_code)
        return ProviderError(self.name, str(exc), retryable=False)

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        try:
            msg = await self._client.messages.create(**self._params(request))
        except Exception as exc:
            raise self._wrap(exc) from exc
        return self._parse(msg)

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ModelChunk]:
        try:
            async with self._client.messages.stream(**self._params(request)) as s:
                async for text in s.text_stream:
                    yield ModelChunk(type="delta", text=text)
                final = await s.get_final_message()
        except Exception as exc:
            raise self._wrap(exc) from exc
        response = self._parse(final)
        self._emit(request, response)
        yield ModelChunk(type="done", response=response)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
