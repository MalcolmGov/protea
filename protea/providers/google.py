"""Google Gemini adapter (generateContent REST). Native JSON-schema output via responseSchema."""

from __future__ import annotations

import json
from typing import Any

import httpx

from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import (
    GenerationRequest,
    GenerationResponse,
    Message,
    ToolCall,
    ToolSchema,
    Usage,
)

_RETRYABLE = {408, 429, 500, 502, 503, 504}


def _model_parts(m: Message) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if m.content:
        parts.append({"text": m.content})
    parts.extend({"functionCall": {"name": tc.name, "args": tc.arguments}} for tc in m.tool_calls)
    return parts or [{"text": ""}]


def _function_response_part(m: Message) -> dict[str, Any]:
    try:
        payload: Any = json.loads(m.content or "")
    except json.JSONDecodeError:
        payload = {"result": m.content}
    if not isinstance(payload, dict):
        payload = {"result": payload}
    return {"functionResponse": {"name": m.name or "tool", "response": payload}}


def to_gemini_contents(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content or "")
        elif m.role == "assistant":
            contents.append({"role": "model", "parts": _model_parts(m)})
        elif m.role == "tool":
            contents.append({"role": "user", "parts": [_function_response_part(m)]})
        else:
            contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
    return ("\n\n".join(system_parts) or None), contents


def _strip_unsupported(schema: Any) -> Any:
    """Gemini's responseSchema rejects a few JSON Schema keywords; drop them recursively."""
    if isinstance(schema, dict):
        return {
            k: _strip_unsupported(v)
            for k, v in schema.items()
            if k not in {"additionalProperties", "$schema", "title", "$defs", "default"}
        }
    if isinstance(schema, list):
        return [_strip_unsupported(x) for x in schema]
    return schema


class GoogleProvider(ModelProvider):
    name = "google"
    supports_native_json_schema = True

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_s: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
        **kw: Any,
    ):
        super().__init__(model=model, **kw)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = http_client or httpx.AsyncClient(timeout=timeout_s)

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        system, contents = to_gemini_contents(request.messages)
        gen: dict[str, Any] = {"maxOutputTokens": request.max_tokens, "temperature": request.temperature}
        if request.stop:
            gen["stopSequences"] = request.stop
        if request.response_schema:
            gen["responseMimeType"] = "application/json"
            gen["responseSchema"] = _strip_unsupported(request.response_schema)
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if request.tools:
            payload["tools"] = [{"functionDeclarations": [self._decl(t) for t in request.tools]}]
        return payload

    @staticmethod
    def _decl(t: ToolSchema) -> dict[str, Any]:
        return {"name": t.name, "description": t.description, "parameters": _strip_unsupported(t.parameters)}

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            resp = await self._client.post(
                url,
                json=self._payload(request),
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"connection failed: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=resp.status_code in _RETRYABLE,
                status=resp.status_code,
            )
        data = resp.json()
        cand = (data.get("candidates") or [{}])[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if "text" in p) or None
        calls = [
            ToolCall(
                id=f"call_{i}", name=p["functionCall"]["name"], arguments=dict(p["functionCall"].get("args") or {})
            )
            for i, p in enumerate(parts)
            if "functionCall" in p
        ]
        reason = cand.get("finishReason", "STOP")
        finish = (
            "tool_calls"
            if calls
            else {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "refusal", "PROHIBITED_CONTENT": "refusal"}.get(
                reason, "other"
            )
        )
        u = data.get("usageMetadata") or {}
        return GenerationResponse(
            content=text,
            tool_calls=calls,
            finish_reason=finish,  # type: ignore[arg-type]
            usage=Usage(
                input_tokens=u.get("promptTokenCount", 0),
                output_tokens=u.get("candidatesTokenCount", 0),
                cache_read_tokens=u.get("cachedContentTokenCount", 0),
            ),
            provider=self.name,
            model=data.get("modelVersion") or self.model,
        )
