"""Tool-permission guard (defence in depth for spec §33 and the security suite).

A model, small or frontier, can be talked into calling a tool it was told not to use, into calling a tool
that does not exist, or into repeating a line of its own instructions. None of that has to reach a
caller: the guard sits between the platform and the model, applies a per-deployment policy to every
response, and records what it changed. It never widens what the model may do, only narrows it.

Actions, in the order they are applied to a response:

* ``unknown`` — a call to a tool the request did not declare is dropped; the model is asked once more
  with the error as a tool result (``unknown_tool_retries``), then the call is simply removed.
* ``denied`` — a call matching a ``deny`` pattern is dropped and the reply becomes ``refusal`` when
  nothing else remains.
* ``over_limit`` — a call whose amount argument exceeds its limit is replaced by a call to the
  ``escalation_tool`` when the request declares one, otherwise dropped with the refusal text.
* ``leak`` — content (or a tool argument) that repeats a confidential fragment of the system prompt is
  replaced by the refusal. A line of the system prompt is confidential when it carries one of the
  ``confidential_markers``.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from pydantic import BaseModel, Field

from protea.providers.base import ModelProvider
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message, ToolCall

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./@]{6,}")
DEFAULT_REFUSAL = "I can't do that from here. A colleague can help with that request."


class AmountLimit(BaseModel):
    field: str  # argument name carrying the amount
    max: float  # inclusive ceiling the agent may act on alone


class ToolPolicy(BaseModel):
    deny: list[str] = Field(default_factory=lambda: ["delete_*", "*_delete", "transfer_*", "admin_*", "*_admin"])
    limits: dict[str, AmountLimit] = Field(default_factory=dict)  # tool name -> limit
    escalation_tool: str = "handoff_to_human"
    refusal: str = DEFAULT_REFUSAL
    unknown_tool_retries: int = Field(default=1, ge=0, le=3)
    confidential_markers: list[str] = Field(default_factory=lambda: ["[internal]", "[confidential]", "staff only"])
    min_fragment_chars: int = Field(default=7, ge=4)

    def denied(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.deny)

    def confidential_fragments(self, request: GenerationRequest) -> set[str]:
        """Words (7+ chars by default) from the system prompt's confidential lines."""
        frags: set[str] = set()
        markers = [m.lower() for m in self.confidential_markers]
        for msg in request.messages:
            if msg.role != "system" or not msg.content:
                continue
            for line in msg.content.splitlines():
                low = line.lower()
                if any(m in low for m in markers):
                    frags.update(w for w in _WORD.findall(line) if len(w) >= self.min_fragment_chars)
        return {f for f in frags if f.lower() not in {m.strip("[]").lower() for m in self.confidential_markers}}


class GuardOutcome(BaseModel):
    response: GenerationResponse
    actions: list[str] = Field(default_factory=list)  # e.g. "denied:transfer_funds", "leak"
    retry: bool = False  # an unknown tool was called and a retry is worth one more round


def _leaks(text: str | None, fragments: set[str]) -> bool:
    if not text or not fragments:
        return False
    return any(f in text for f in fragments)


def _amount(call: ToolCall, limit: AmountLimit) -> float | None:
    raw = call.arguments.get(limit.field)
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class _Context(BaseModel):
    declared: set[str]
    fragments: set[str]


def _verdict(policy: ToolPolicy, ctx: _Context, call: ToolCall) -> tuple[str, str | None]:
    """One of unknown | denied | over_limit | leak | ok, plus the escalation reason for over_limit."""
    if call.name not in ctx.declared:
        return "unknown", None
    if policy.denied(call.name):
        return "denied", None
    limit = policy.limits.get(call.name)
    amount = _amount(call, limit) if limit else None
    if limit and amount is not None and amount > limit.max:
        return "over_limit", f"{call.name} for {amount:g} exceeds the limit of {limit.max:g}"
    if _leaks(str(call.arguments), ctx.fragments):
        return "leak", None
    return "ok", None


def _filter_calls(
    policy: ToolPolicy, ctx: _Context, calls: list[ToolCall]
) -> tuple[list[ToolCall], list[str], list[str], bool]:
    kept: list[ToolCall] = []
    actions: list[str] = []
    escalate: list[str] = []
    refuse = False
    for call in calls:
        verdict, reason = _verdict(policy, ctx, call)
        if verdict == "ok":
            kept.append(call)
            continue
        actions.append(f"{verdict}:{call.name}")
        refuse = refuse or verdict in {"denied", "leak"}
        if reason:
            escalate.append(reason)
    return kept, actions, escalate, refuse


def _escalate(policy: ToolPolicy, ctx: _Context, kept: list[ToolCall], reasons: list[str]) -> bool:
    """Append the escalation call when the request declares one; otherwise the response must refuse."""
    if not reasons:
        return False
    if policy.escalation_tool in ctx.declared and policy.escalation_tool not in {c.name for c in kept}:
        kept.append(
            ToolCall(id="guard_escalation", name=policy.escalation_tool, arguments={"reason": "; ".join(reasons)})
        )
        return False
    return True


def apply_policy(policy: ToolPolicy, request: GenerationRequest, response: GenerationResponse) -> GuardOutcome:
    """Pure function: the guarded response and the actions taken. Does not call the model."""
    ctx = _Context(declared={t.name for t in request.tools or []}, fragments=policy.confidential_fragments(request))
    kept, actions, escalate, refuse = _filter_calls(policy, ctx, response.tool_calls)
    content = response.content
    leaked = _leaks(content, ctx.fragments)
    if leaked:
        actions.append("leak")
        content = policy.refusal
    refuse = _escalate(policy, ctx, kept, escalate) or refuse
    if refuse and not kept and not leaked:
        content = policy.refusal
    unknown_only = bool(actions) and all(a.startswith("unknown:") for a in actions) and not kept
    out = response.model_copy(update={"tool_calls": kept, "content": content})
    if kept:
        out.finish_reason = "tool_calls"
    elif out.finish_reason == "tool_calls":
        out.finish_reason = "stop"
    return GuardOutcome(response=out, actions=actions, retry=unknown_only)


def retry_request(request: GenerationRequest, response: GenerationResponse, unknown: list[str]) -> GenerationRequest:
    """Feed the unknown-tool error back as tool results so the model can answer with what it has."""
    msgs = list(request.messages)
    msgs.append(Message(role="assistant", content=response.content, tool_calls=list(response.tool_calls)))
    for call in response.tool_calls:
        if call.name in unknown:
            msgs.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    name=call.name,
                    content=f'{{"error": "unknown tool {call.name}; only the declared tools exist"}}',
                )
            )
    return request.model_copy(update={"messages": msgs})


class GuardedProvider(ModelProvider):
    """Wraps any provider; the inner provider keeps its own usage accounting and name."""

    supports_native_json_schema = False

    def __init__(self, inner: ModelProvider, policy: ToolPolicy | None = None, *, sink: Any | None = None):
        super().__init__(model=inner.model, usage_sink=inner.usage_sink)
        self.inner = inner
        self.policy = policy or ToolPolicy()
        self.name = inner.name
        self.supports_native_json_schema = inner.supports_native_json_schema
        self.sink = sink  # callable(actions, request, response) for metrics/logging
        self.actions_total: dict[str, int] = {}

    def _with_schema_instruction(self, request: GenerationRequest) -> GenerationRequest:
        return self.inner._with_schema_instruction(request)

    async def health(self):  # pragma: no cover - passthrough
        return await self.inner.health()

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        req = request
        retries = self.policy.unknown_tool_retries
        actions: list[str] = []
        while True:
            resp = await self.inner.generate(req)
            outcome = apply_policy(self.policy, req, resp)
            actions.extend(outcome.actions)
            if outcome.retry and retries > 0:
                retries -= 1
                unknown = [a.split(":", 1)[1] for a in outcome.actions if a.startswith("unknown:")]
                req = retry_request(req, resp, unknown)
                continue
            outcome.actions = actions
            self._record(outcome, req)
            return outcome.response

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        # The inner provider already emitted usage; the guard only post-processes.
        return await self._generate(request)

    def _record(self, outcome: GuardOutcome, request: GenerationRequest) -> None:
        for a in outcome.actions:
            key = a.split(":", 1)[0]
            self.actions_total[key] = self.actions_total.get(key, 0) + 1
        if self.sink is not None and outcome.actions:
            self.sink(outcome.actions, request, outcome.response)
