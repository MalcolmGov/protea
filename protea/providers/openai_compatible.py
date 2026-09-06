"""OpenAI-compatible chat completions: OpenAI, vLLM (Protea), Ollama, gateways, Azure OpenAI."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import (
    GenerationRequest,
    GenerationResponse,
    Message,
    ModelChunk,
    ToolCall,
    ToolSchema,
    Usage,
)

_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def to_openai_tools(tools: list[ToolSchema] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters, "strict": t.strict},
        }
        for t in tools
    ]


def parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for tc in raw or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except json.JSONDecodeError:
            parsed = {"_raw": args}
        calls.append(ToolCall(id=tc.get("id") or f"call_{len(calls)}", name=fn.get("name", ""), arguments=parsed))
    return calls


_DONE: dict[str, Any] = {}


def _finish_reason(reason: str | None) -> str:
    return {"stop": "stop", "tool_calls": "tool_calls", "length": "length", "content_filter": "refusal"}.get(
        reason or "", "other"
    )


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """Return the JSON event for a `data:` line, the _DONE sentinel for `[DONE]`, or None for other lines."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return _DONE
    return json.loads(data)


class _StreamState:
    """Accumulates text, tool-call fragments, usage and finish reason across SSE events."""

    def __init__(self, model: str):
        self.model = model
        self.content: list[str] = []
        self.tools: dict[int, dict[str, Any]] = {}
        self.usage = Usage()
        self.finish = "stop"

    def apply(self, event: dict[str, Any]) -> list[str]:
        self.model = event.get("model") or self.model
        if event.get("usage"):
            u = event["usage"]
            self.usage = Usage(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0))
        deltas: list[str] = []
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.content.append(delta["content"])
                deltas.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                self._merge_tool_call(tc)
            if choice.get("finish_reason"):
                self.finish = _finish_reason(choice["finish_reason"])
        return deltas

    def _merge_tool_call(self, tc: dict[str, Any]) -> None:
        slot = self.tools.setdefault(tc.get("index", 0), {"id": None, "function": {"name": "", "arguments": ""}})
        slot["id"] = tc.get("id") or slot["id"]
        fn = tc.get("function") or {}
        slot["function"]["name"] += fn.get("name") or ""
        slot["function"]["arguments"] += fn.get("arguments") or ""

    def response(self, provider: str) -> GenerationResponse:
        return GenerationResponse(
            content="".join(self.content) or None,
            tool_calls=parse_tool_calls([self.tools[k] for k in sorted(self.tools)]),
            finish_reason=self.finish,  # type: ignore[arg-type]
            usage=self.usage,
            provider=provider,
            model=self.model,
        )


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"
    supports_native_json_schema = True

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None,
        *,
        name: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout_s: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
        **kw: Any,
    ):
        super().__init__(model=model, **kw)
        if name:
            self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra_headers = extra_headers or {}
        self._client = http_client or httpx.AsyncClient(timeout=timeout_s)

    # ---- request building -----------------------------------------------------------------
    def _headers(self, streaming: bool = False) -> dict[str, str]:
        h = {"content-type": "application/json", **self.extra_headers}
        if self.api_key:
            h.setdefault("authorization", f"Bearer {self.api_key}")
        if streaming:
            h["accept"] = "text/event-stream"
        return h

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _payload(self, request: GenerationRequest, stream: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(request.messages),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.stop:
            payload["stop"] = request.stop
        tools = to_openai_tools(request.tools)
        if tools:
            payload["tools"] = tools
        if request.response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.response_schema_name,
                    "schema": request.response_schema,
                    "strict": True,
                },
            }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    # ---- calls ---------------------------------------------------------------------------------
    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(self._url(), json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"connection failed: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=resp.status_code in _RETRYABLE,
                status=resp.status_code,
            )
        return resp.json()

    def _parse(self, data: dict[str, Any]) -> GenerationResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        return GenerationResponse(
            content=msg.get("content"),
            tool_calls=parse_tool_calls(msg.get("tool_calls")),
            finish_reason=_finish_reason(choice.get("finish_reason")),  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                cache_read_tokens=((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
            ),
            provider=self.name,
            model=data.get("model") or self.model,
        )

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        return self._parse(await self._post(self._payload(request)))

    async def stream(self, request: GenerationRequest) -> AsyncIterator[ModelChunk]:
        payload = self._payload(request, stream=True)
        state = _StreamState(self.model)
        try:
            async with self._client.stream(
                "POST", self._url(), json=payload, headers=self._headers(streaming=True)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise ProviderError(
                        self.name,
                        f"HTTP {resp.status_code}: {body[:300]}",
                        retryable=resp.status_code in _RETRYABLE,
                        status=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    event = _parse_sse_line(line)
                    if event is None:
                        continue
                    if event is _DONE:
                        break
                    for text in state.apply(event):
                        yield ModelChunk(type="delta", text=text)
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"stream failed: {exc}", retryable=True) from exc
        response = state.response(self.name)
        self._emit(request, response)
        yield ModelChunk(type="done", response=response)


class AzureOpenAIProvider(OpenAICompatibleProvider):
    """Azure OpenAI: deployment-scoped URL, api-version query, `api-key` header instead of Bearer."""

    name = "azure_openai"

    def __init__(self, deployment: str, endpoint: str, api_key: str, api_version: str = "2024-10-21", **kw: Any):
        super().__init__(
            model=deployment, base_url=endpoint.rstrip("/"), api_key=None, extra_headers={"api-key": api_key}, **kw
        )
        self.deployment = deployment
        self.api_version = api_version

    def _url(self) -> str:
        return f"{self.base_url}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"
