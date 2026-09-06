from types import SimpleNamespace

import anthropic
import httpx
import pytest

from protea.providers import ProviderError, ProviderNotConfigured
from protea.providers.anthropic_provider import AnthropicProvider, to_anthropic_messages
from protea.schemas.generation import GenerationRequest, Message, ToolCall, ToolSchema


class FakeMessages:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def create(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return self.result


def _client(result=None, error=None):
    return SimpleNamespace(messages=FakeMessages(result, error))


def _msg(content, stop_reason="end_turn"):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-opus-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=4, cache_read_input_tokens=0),
    )


async def test_tool_use_block_and_params():
    block = SimpleNamespace(type="tool_use", id="toolu_1", name="get_order_status", input={"order_id": "4821"})
    client = _client(_msg([block], "tool_use"))
    p = AnthropicProvider(model="claude-opus-5", api_key=None, client=client)
    req = GenerationRequest(
        messages=[Message(role="system", content="Be brief."), Message(role="user", content="order 4821?")],
        tools=[ToolSchema(name="get_order_status", description="d", parameters={"type": "object"}, strict=True)],
    )
    resp = await p.generate(req)
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].id == "toolu_1"
    params = client.messages.calls[0]
    assert params["system"] == "Be brief."
    assert params["messages"][0]["role"] == "user"
    assert params["tools"][0]["input_schema"] == {"type": "object"}
    assert params["tools"][0]["strict"] is True
    assert "temperature" not in params  # rejected by Claude 5 models and absent from the 1.x SDK signature


async def test_structured_uses_output_config():
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool

    client = _client(_msg([SimpleNamespace(type="text", text='{"ok": true}')]))
    p = AnthropicProvider(model="claude-opus-5", api_key=None, client=client)
    out = await p.generate_structured(GenerationRequest(messages=[Message(role="user", content="x")]), Out)
    assert out.ok is True
    fmt = client.messages.calls[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["title"] == "Out"


async def test_error_mapping():
    req_obj = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    request = GenerationRequest(messages=[Message(role="user", content="x")])
    rate = anthropic.RateLimitError("slow", response=httpx.Response(429, request=req_obj), body=None)
    p = AnthropicProvider(model="claude-opus-5", api_key=None, client=_client(error=rate))
    with pytest.raises(ProviderError) as exc:
        await p.generate(request)
    assert exc.value.retryable
    assert exc.value.status == 429
    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=req_obj), body=None)
    p2 = AnthropicProvider(model="claude-opus-5", api_key=None, client=_client(error=bad))
    with pytest.raises(ProviderError) as exc2:
        await p2.generate(request)
    assert not exc2.value.retryable


def test_parallel_tool_results_share_one_user_message():
    msgs = [
        Message(role="user", content="go"),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="a", name="f", arguments={}), ToolCall(id="b", name="g", arguments={})],
        ),
        Message(role="tool", tool_call_id="a", content="1"),
        Message(role="tool", tool_call_id="b", content="2"),
    ]
    system, out = to_anthropic_messages(msgs)
    assert system is None
    assert [b["type"] for b in out[1]["content"]] == ["tool_use", "tool_use"]
    assert len(out) == 3
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["a", "b"]


def test_not_configured():
    with pytest.raises(ProviderNotConfigured):
        AnthropicProvider(model="claude-opus-5", api_key=None)


def test_strict_schema_adds_additional_properties_false_everywhere():
    from protea.providers.anthropic_provider import strict_schema

    schema = {
        "type": "object",
        "properties": {
            "tools": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}}}},
            "policy": {"properties": {"limit": {"type": "number", "minimum": 0}}},
        },
        "required": ["tools"],
    }
    out = strict_schema(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["tools"]["items"]["additionalProperties"] is False
    assert out["properties"]["policy"]["additionalProperties"] is False
    assert out["properties"]["policy"]["properties"]["limit"]["minimum"] == 0
    assert "additionalProperties" not in schema  # caller's schema untouched


class _RejectThenAccept:
    """First call: the API refuses the structured-output schema (400); second call: a plain answer."""

    def __init__(self, result):
        self.result, self.calls = result, []

    async def create(self, **params):
        self.calls.append(params)
        if len(self.calls) == 1:
            raise RuntimeError(
                "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
                "'message': \"output_config.format.schema: For 'object' type, 'additionalProperties' must be false\"}}"
            )
        return self.result


async def test_schema_rejection_falls_back_to_prompt_instruction():
    text = SimpleNamespace(type="text", text='{"lane": "orders"}')
    client = SimpleNamespace(messages=_RejectThenAccept(_msg([text])))
    p = AnthropicProvider(model="claude-opus-5", api_key=None, client=client)
    schema = {"type": "object", "properties": {"lane": {"type": "string"}}, "required": ["lane"]}
    req = GenerationRequest(messages=[Message(role="user", content="classify")], response_schema=schema)
    resp = await p.generate(req)
    assert resp.content == '{"lane": "orders"}'
    first, second = client.messages.calls
    assert first["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "output_config" not in second
    assert "JSON Schema" in second["system"]
    assert '"lane"' in second["system"]
