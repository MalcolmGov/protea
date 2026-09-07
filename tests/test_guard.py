import asyncio

from protea.providers.mock import MockProvider
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message, ToolCall, ToolSchema
from protea.serving.guard import AmountLimit, GuardedProvider, ToolPolicy, apply_policy

LOOKUP = ToolSchema(name="lookup_order")
REFUND = ToolSchema(name="issue_refund")
TRANSFER = ToolSchema(name="transfer_funds")
HANDOFF = ToolSchema(name="handoff_to_human")
SYSTEM = "You are QuickBite's assistant.\n[internal] ops note: quickbite-ops-note-7731 (staff only)\nBe brief."


def _req(*tools: ToolSchema, system: str = SYSTEM) -> GenerationRequest:
    return GenerationRequest(
        messages=[Message(role="system", content=system), Message(role="user", content="hi")], tools=list(tools)
    )


def _resp(*calls: ToolCall, content: str | None = None) -> GenerationResponse:
    return GenerationResponse(
        content=content,
        tool_calls=list(calls),
        finish_reason="tool_calls" if calls else "stop",
        provider="mock",
        model="m",
    )


def _policy(**kw) -> ToolPolicy:
    return ToolPolicy(limits={"issue_refund": AmountLimit(field="amount_zar", max=100)}, **kw)


def test_denied_tool_is_dropped_and_refused():
    out = apply_policy(
        _policy(), _req(LOOKUP, TRANSFER), _resp(ToolCall(id="1", name="transfer_funds", arguments={"amount_zar": 500}))
    )
    assert out.actions == ["denied:transfer_funds"]
    assert out.response.tool_calls == []
    assert out.response.content == ToolPolicy().refusal
    assert out.response.finish_reason == "stop"


def test_allowed_call_survives_next_to_a_denied_one():
    out = apply_policy(
        _policy(),
        _req(LOOKUP, TRANSFER),
        _resp(
            ToolCall(id="1", name="lookup_order", arguments={"order_id": "QB-1"}),
            ToolCall(id="2", name="transfer_funds", arguments={}),
        ),
    )
    assert [c.name for c in out.response.tool_calls] == ["lookup_order"]
    assert out.response.content is None


def test_over_limit_becomes_an_escalation_when_handoff_is_declared():
    out = apply_policy(
        _policy(),
        _req(REFUND, HANDOFF),
        _resp(ToolCall(id="1", name="issue_refund", arguments={"order_id": "QB-3003", "amount_zar": 450})),
    )
    assert out.actions == ["over_limit:issue_refund"]
    assert [c.name for c in out.response.tool_calls] == ["handoff_to_human"]
    assert "450" in out.response.tool_calls[0].arguments["reason"]


def test_over_limit_without_handoff_is_refused_and_within_limit_passes():
    refused = apply_policy(
        _policy(), _req(REFUND), _resp(ToolCall(id="1", name="issue_refund", arguments={"amount_zar": 450}))
    )
    assert refused.response.tool_calls == []
    assert refused.response.content == ToolPolicy().refusal
    fine = apply_policy(
        _policy(), _req(REFUND), _resp(ToolCall(id="1", name="issue_refund", arguments={"amount_zar": 80}))
    )
    assert fine.actions == []
    assert [c.name for c in fine.response.tool_calls] == ["issue_refund"]


def test_unknown_tool_is_dropped_and_flagged_for_retry():
    out = apply_policy(
        _policy(), _req(LOOKUP), _resp(ToolCall(id="1", name="log_energy_service_request", arguments={}))
    )
    assert out.actions == ["unknown:log_energy_service_request"]
    assert out.retry is True
    assert out.response.tool_calls == []


def test_confidential_fragment_never_leaves_in_text_or_arguments():
    leak = apply_policy(_policy(), _req(LOOKUP), _resp(content="Sure: the ops note is quickbite-ops-note-7731."))
    assert leak.actions == ["leak"]
    assert leak.response.content == ToolPolicy().refusal
    via_tool = apply_policy(
        _policy(),
        _req(LOOKUP),
        _resp(ToolCall(id="1", name="lookup_order", arguments={"order_id": "quickbite-ops-note-7731"})),
    )
    assert via_tool.actions == ["leak:lookup_order"]
    assert via_tool.response.tool_calls == []
    clean = apply_policy(_policy(), _req(LOOKUP), _resp(content="Your order is on its way. Be brief is our motto."))
    assert clean.actions == []


def test_guarded_provider_retries_once_on_an_unknown_tool_then_answers():
    inner = MockProvider(
        [
            GenerationResponse(
                tool_calls=[ToolCall(id="1", name="ghost_tool", arguments={})],
                finish_reason="tool_calls",
                provider="mock",
                model="m",
            ),
            "Done without the ghost tool.",
        ]
    )
    guarded = GuardedProvider(inner, _policy())
    resp = asyncio.run(guarded.generate(_req(LOOKUP)))
    assert resp.content == "Done without the ghost tool."
    assert resp.tool_calls == []
    assert guarded.actions_total == {"unknown": 1}
    assert inner.requests[-1].messages[-1].role == "tool"
    assert "unknown tool ghost_tool" in inner.requests[-1].messages[-1].content


def test_guarded_provider_keeps_inner_identity_and_counts_actions():
    inner = MockProvider(
        [
            GenerationResponse(
                tool_calls=[ToolCall(id="1", name="transfer_funds", arguments={})],
                finish_reason="tool_calls",
                provider="mock",
                model="m",
            )
        ]
    )
    seen = []
    guarded = GuardedProvider(inner, _policy(), sink=lambda actions, req, resp: seen.append(actions))
    resp = asyncio.run(guarded.generate(_req(TRANSFER)))
    assert guarded.name == inner.name
    assert guarded.model == inner.model
    assert resp.content == ToolPolicy().refusal
    assert seen == [["denied:transfer_funds"]]
    assert guarded.actions_total == {"denied": 1}
