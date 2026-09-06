"""Conversation driver: runs user turns against a provider, executing tool calls with canned results.

Shared by ZaraBench (tasks) and the eval-seeded synthetic pipeline (seeds); neither has a live tool backend.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message, RequestMeta, ToolCall, ToolSchema

MAX_TOOL_ROUNDS = 3


class DriveOptions(BaseModel):
    max_rounds: int = MAX_TOOL_ROUNDS
    max_tokens: int = 800
    temperature: float = 0.0
    metadata: RequestMeta | None = None

    def request_kw(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"max_tokens": self.max_tokens, "temperature": self.temperature}
        if self.metadata is not None:
            kw["metadata"] = self.metadata
        return kw


class Transcript(BaseModel):
    messages: list[Message]
    tool_calls: list[ToolCall] = Field(default_factory=list)  # every call, in order
    final_text: str = ""
    rounds: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    truncated: bool = False  # hit MAX_TOOL_ROUNDS while still calling tools

    @property
    def called(self) -> list[str]:
        return [c.name for c in self.tool_calls]


def stub_tool_result(call: ToolCall) -> str:
    """Generic canned result so a model cannot learn fabricated facts from the harness."""
    return json.dumps({"status": "ok", "tool": call.name, "echo": call.arguments})


class CannedResults:
    """Per-tool result sequences; a tool without a sequence gets the generic stub."""

    def __init__(self, results: dict[str, list[str]] | None = None):
        self._seq = {k: list(v) for k, v in (results or {}).items() if v}
        self._pos: dict[str, int] = {}

    def result_for(self, call: ToolCall) -> str:
        seq = self._seq.get(call.name)
        if not seq:
            return stub_tool_result(call)
        i = self._pos.get(call.name, 0)
        self._pos[call.name] = i + 1
        return seq[min(i, len(seq) - 1)]


def _account(t: Transcript, resp: GenerationResponse) -> None:
    t.rounds += 1
    t.latency_ms += resp.latency_ms
    t.input_tokens += resp.usage.input_tokens
    t.output_tokens += resp.usage.output_tokens


async def _complete_turn(
    t: Transcript,
    provider: ModelProvider,
    tools: list[ToolSchema],
    canned: CannedResults,
    max_rounds: int,
    request_kw: dict[str, Any],
) -> None:
    """Drive one user turn to a final assistant text (or to the round limit)."""
    for _ in range(max_rounds + 1):
        resp = await provider.generate(GenerationRequest(messages=t.messages, tools=tools or None, **request_kw))
        _account(t, resp)
        if not resp.tool_calls:
            t.final_text = resp.content or ""
            t.messages.append(Message(role="assistant", content=t.final_text))
            return
        t.messages.append(Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            t.tool_calls.append(call)
            t.messages.append(
                Message(role="tool", tool_call_id=call.id, name=call.name, content=canned.result_for(call))
            )
    t.truncated = True


async def drive_conversation(
    provider: ModelProvider,
    messages: list[Message],
    turns: list[str],
    tools: list[ToolSchema],
    *,
    tool_results: dict[str, list[str]] | None = None,
    options: DriveOptions | None = None,
) -> Transcript:
    """`messages` is the prefix (system, optionally earlier turns); `turns` are the user messages to play in order."""
    opts = options or DriveOptions()
    t = Transcript(messages=list(messages))
    canned = CannedResults(tool_results)
    try:
        for turn in turns:
            t.messages.append(Message(role="user", content=turn))
            await _complete_turn(t, provider, tools, canned, opts.max_rounds, opts.request_kw())
    except ProviderError as exc:
        t.error = str(exc)
    return t
