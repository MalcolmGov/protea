"""Provider-neutral generation contract (the shape every ModelProvider speaks)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length", "refusal", "error", "other"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # role == "tool": which call this answers
    name: str | None = None  # role == "tool": tool name (informational)


class ToolSchema(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    strict: bool = False


class RequestMeta(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_ref: str | None = None  # hashed / anonymised tenant reference, never raw
    agent_id: str | None = None
    task_type: str | None = None
    channel: str = "protea"


class GenerationRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSchema] | None = None
    response_schema: dict[str, Any] | None = None  # JSON Schema for constrained output
    response_schema_name: str = "response"
    max_tokens: int = 1024
    temperature: float = 0.2
    stop: list[str] | None = None
    metadata: RequestMeta = Field(default_factory=RequestMeta)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class GenerationResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: FinishReason = "stop"
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str
    latency_ms: int = 0
    raw: dict[str, Any] | None = None


class ModelChunk(BaseModel):
    type: Literal["delta", "done"]
    text: str = ""
    response: GenerationResponse | None = None


class ModelHealth(BaseModel):
    provider: str
    model: str
    ok: bool
    latency_ms: int = 0
    detail: str = ""


class UsageEvent(BaseModel):
    """What the platform records per call. Never contains prompt or completion text."""

    request_id: str
    provider: str
    model: str
    channel: str
    task_type: str | None = None
    tenant_ref: str | None = None
    agent_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = "stop"
    fallback: bool = False
    error: str | None = None
