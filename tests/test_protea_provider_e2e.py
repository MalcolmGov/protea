"""End-to-end: the `protea` provider and the facade against a mock vLLM server that speaks the OpenAI wire format."""

import json
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

from protea.config.settings import ProteaSettings
from protea.providers import build_provider
from protea.schemas.generation import GenerationRequest, Message, ToolSchema
from protea.serving.app import create_app
from protea.serving.config import ServeConfig


class Lane(BaseModel):
    lane: str


def mock_vllm() -> FastAPI:
    """Just enough of vLLM's OpenAI server: bearer auth, tool calls, json_schema response_format, streaming."""
    app = FastAPI()

    @app.get("/v1/models")
    async def models(authorization: str = Header(default="")):
        return {"data": [{"id": "protea-agent"}]}

    def _answer(body: dict[str, Any]) -> dict[str, Any]:
        last = body["messages"][-1]["content"] or ""
        if body.get("tools") and "order" in last and body["messages"][-1]["role"] == "user":
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_order", "arguments": '{"order_id": "7"}'},
                    }
                ],
            }
        fmt = body.get("response_format") or {}
        if fmt.get("type") == "json_schema":
            return {"role": "assistant", "content": json.dumps({"lane": "stokvel"})}
        return {"role": "assistant", "content": f"echo: {last}"}

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str = Header(default="")):
        if authorization != "Bearer engine-token":
            raise HTTPException(401, "bad token")
        body = await request.json()
        msg = _answer(body)
        finish = "tool_calls" if msg.get("tool_calls") else "stop"
        usage = {"prompt_tokens": 20, "completion_tokens": 6}
        if body.get("stream"):

            def events():
                text = msg.get("content") or ""
                for i in range(0, len(text), 4):
                    yield f"data: {json.dumps({'model': 'protea-agent', 'choices': [{'delta': {'content': text[i : i + 4]}}]})}\n\n"
                yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': finish}]})}\n\n"
                yield f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")
        return {"model": "protea-agent", "choices": [{"message": msg, "finish_reason": finish}], "usage": usage}

    return app


def _provider():
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_vllm()), base_url="http://vllm")
    settings = ProteaSettings(
        protea_inference_url="http://vllm/v1",
        protea_inference_token="engine-token",
        protea_inference_model="protea-agent",
    )
    return build_provider("protea", settings=settings, http_client=client)


async def test_protea_provider_against_mock_vllm():
    p = _provider()
    assert p.name == "protea"
    health = await p.health()
    assert health.ok

    tool = ToolSchema(name="get_order", parameters={"type": "object", "properties": {"order_id": {"type": "string"}}})
    resp = await p.generate(
        GenerationRequest(messages=[Message(role="user", content="where is my order?")], tools=[tool])
    )
    assert resp.tool_calls[0].arguments == {"order_id": "7"}
    assert resp.finish_reason == "tool_calls"

    lane = await p.generate_structured(GenerationRequest(messages=[Message(role="user", content="split R450")]), Lane)
    assert lane.lane == "stokvel"

    chunks = [c async for c in p.stream(GenerationRequest(messages=[Message(role="user", content="hello")]))]
    assert "".join(c.text for c in chunks if c.type == "delta") == "echo: hello"
    assert chunks[-1].response.usage.output_tokens == 6


def test_facade_in_front_of_mock_vllm():
    app = create_app(ServeConfig(backend="protea", require_token=False), _provider())
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
        body = {
            "model": "protea-agent",
            "messages": [{"role": "user", "content": "where is my order?"}],
            "tools": [{"type": "function", "function": {"name": "get_order", "parameters": {"type": "object"}}}],
        }
        r = client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_order"

        gen = GenerationRequest(messages=[{"role": "user", "content": "split R450"}]).model_dump(mode="json")
        schema = {"type": "object", "properties": {"lane": {"type": "string"}}, "required": ["lane"]}
        r = client.post("/v1/generate/structured", json={"request": gen, "schema": schema})
        assert r.json() == {"valid": True, "output": {"lane": "stokvel"}, "errors": [], "repairs": 0}

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "protea-agent", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as s:
            raw = "".join(s.iter_text())
        events = [
            json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:") and "[DONE]" not in line
        ]
        assert "".join(e["choices"][0]["delta"].get("content", "") for e in events if e["choices"]) == "echo: hi"
        assert raw.strip().endswith("data: [DONE]")
