"""OpenAI chat-completions wire format ⇄ Protea generation contract (the shape aria's runtime gateway speaks)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from protea.schemas.generation import GenerationRequest, GenerationResponse, Message, RequestMeta, ToolCall, ToolSchema


class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel, extra="allow"):
    model: str
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    response_format: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stop: str | list[str] | None = None
    stream: bool = False
    user: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _text(content: str | list[dict[str, Any]] | None) -> str | None:
    if content is None or isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")


def _parse_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    calls = []
    for i, tc in enumerate(raw or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(args) if isinstance(args, str) else dict(args)
        except json.JSONDecodeError:
            parsed = {"_raw": args}
        calls.append(ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=parsed))
    return calls


def to_generation_request(
    req: ChatCompletionRequest, *, task_type: str | None, tenant_ref: str | None
) -> GenerationRequest:
    messages = [
        Message(
            role=m.role,  # type: ignore[arg-type]
            content=_text(m.content),
            tool_calls=_parse_calls(m.tool_calls),
            tool_call_id=m.tool_call_id,
            name=m.name,
        )
        for m in req.messages
    ]
    tools = [
        ToolSchema(
            name=t["function"]["name"],
            description=t["function"].get("description", ""),
            parameters=t["function"].get("parameters") or {"type": "object", "properties": {}},
            strict=bool(t["function"].get("strict", False)),
        )
        for t in req.tools or []
        if t.get("type", "function") == "function" and "function" in t
    ]
    schema = None
    schema_name = "response"
    if req.response_format and req.response_format.get("type") == "json_schema":
        js = req.response_format.get("json_schema") or {}
        schema = js.get("schema")
        schema_name = js.get("name") or schema_name
    elif req.response_format and req.response_format.get("type") == "json_object":
        schema = {"type": "object"}
    stop = [req.stop] if isinstance(req.stop, str) else req.stop
    return GenerationRequest(
        messages=messages,
        tools=tools or None,
        response_schema=schema,
        response_schema_name=schema_name,
        max_tokens=req.max_completion_tokens or req.max_tokens or 1024,
        temperature=req.temperature if req.temperature is not None else 0.2,
        stop=stop,
        metadata=RequestMeta(task_type=task_type, tenant_ref=tenant_ref, channel="facade"),
    )


def _message_dict(resp: GenerationResponse) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": resp.content}
    if resp.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in resp.tool_calls
        ]
    return msg


def to_chat_completion(resp: GenerationResponse, served_model: str, request_id: str | None = None) -> dict[str, Any]:
    finish = (
        "tool_calls" if resp.tool_calls else {"refusal": "content_filter"}.get(resp.finish_reason, resp.finish_reason)
    )
    return {
        "id": request_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": served_model,
        "choices": [{"index": 0, "message": _message_dict(resp), "finish_reason": finish}],
        "usage": {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.total_tokens,
        },
        "protea": {"provider": resp.provider, "model": resp.model, "latency_ms": resp.latency_ms},
    }


def sse_chunk(chunk_id: str, served_model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": served_model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def sse_final(chunk_id: str, served_model: str, resp: GenerationResponse) -> str:
    delta: dict[str, Any] = {}
    if resp.tool_calls:
        delta["tool_calls"] = [
            {
                "index": i,
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for i, c in enumerate(resp.tool_calls)
        ]
    finish = "tool_calls" if resp.tool_calls else resp.finish_reason
    usage = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": served_model,
        "choices": [],
        "usage": {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.total_tokens,
        },
    }
    return sse_chunk(chunk_id, served_model, delta, finish) + f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n"
