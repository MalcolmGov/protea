import json

import httpx
import pytest
from pydantic import BaseModel

from protea.providers import ProviderError
from protea.providers.openai_compatible import (
    AzureOpenAIProvider,
    OpenAICompatibleProvider,
    to_openai_messages,
)
from protea.schemas.generation import GenerationRequest, Message, ToolCall, ToolSchema


class Out(BaseModel):
    ok: bool


def _provider(handler, **kw) -> OpenAICompatibleProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        model="qwen3-8b", base_url="http://vllm:8000/v1", api_key="tok", name="protea", http_client=client, **kw
    )


def _completion(message: dict, finish="stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen3-8b",
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        },
    )


async def test_tool_call_round_trip_and_payload_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _completion(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_order_status", "arguments": '{"order_id": "4821"}'},
                    }
                ],
            },
            "tool_calls",
        )

    p = _provider(handler)
    tool = ToolSchema(
        name="get_order_status", parameters={"type": "object", "properties": {"order_id": {"type": "string"}}}
    )
    resp = await p.generate(GenerationRequest(messages=[Message(role="user", content="order 4821?")], tools=[tool]))
    assert resp.finish_reason == "tool_calls" and resp.tool_calls[0].arguments == {"order_id": "4821"}
    assert resp.usage.input_tokens == 12
    assert seen["url"] == "http://vllm:8000/v1/chat/completions" and seen["auth"] == "Bearer tok"
    assert seen["body"]["tools"][0]["function"]["name"] == "get_order_status"


async def test_structured_uses_response_format():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _completion({"role": "assistant", "content": '{"ok": true}'})

    out = await _provider(handler).generate_structured(
        GenerationRequest(messages=[Message(role="user", content="x")]), Out
    )
    assert out.ok is True
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["name"] == "Out"


async def test_http_errors_map_to_retryable_flag():
    p = _provider(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(ProviderError) as exc:
        await p.generate(GenerationRequest(messages=[Message(role="user", content="x")]))
    assert exc.value.retryable and exc.value.status == 429
    p2 = _provider(lambda r: httpx.Response(400, text="bad"))
    with pytest.raises(ProviderError) as exc2:
        await p2.generate(GenerationRequest(messages=[Message(role="user", content="x")]))
    assert not exc2.value.retryable


async def test_streaming_assembles_text_and_tool_calls():
    events = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "id": "c9", "function": {"name": "look", "arguments": '{"a":'}}]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": " 1}"}}]},
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
    ]
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    p = _provider(lambda r: httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"}))
    chunks = [c async for c in p.stream(GenerationRequest(messages=[Message(role="user", content="x")]))]
    assert "".join(c.text for c in chunks if c.type == "delta") == "Hello"
    done = chunks[-1].response
    assert done.tool_calls[0].id == "c9" and done.tool_calls[0].arguments == {"a": 1}
    assert done.usage.output_tokens == 4 and done.finish_reason == "tool_calls"


def test_message_mapping_for_tool_turns():
    msgs = [
        Message(role="assistant", tool_calls=[ToolCall(id="c1", name="f", arguments={"k": "v"})]),
        Message(role="tool", tool_call_id="c1", content="{}"),
    ]
    out = to_openai_messages(msgs)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"k": "v"}'
    assert out[1] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


async def test_azure_url_and_header():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("api-key")
        seen["auth"] = request.headers.get("authorization")
        return _completion({"role": "assistant", "content": "hi"})

    p = AzureOpenAIProvider(
        deployment="gpt4o",
        endpoint="https://x.openai.azure.com",
        api_key="k",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await p.generate(GenerationRequest(messages=[Message(role="user", content="x")]))
    assert seen["url"] == "https://x.openai.azure.com/openai/deployments/gpt4o/chat/completions?api-version=2024-10-21"
    assert seen["key"] == "k" and seen["auth"] is None
