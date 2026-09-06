import json

import pytest
from fastapi.testclient import TestClient

from protea.providers.base import ProviderError
from protea.providers.mock import MockProvider
from protea.schemas.generation import GenerationRequest, ToolCall
from protea.serving.app import create_app
from protea.serving.config import ServeConfig
from protea.serving.gate import parse_and_validate

SCHEMA = {"type": "object", "properties": {"lane": {"type": "string"}}, "required": ["lane"]}
AUTH = {"authorization": "Bearer secret"}


def _client(mock: MockProvider | None = None, **cfg) -> tuple[TestClient, MockProvider]:
    mock = mock or MockProvider(["hello from protea"])
    app = create_app(ServeConfig(backend="mock", aliases=["gpt-4o-mini"], **cfg), mock, token="secret")
    return TestClient(app), mock


def test_token_required_and_ops_endpoints():
    client, _ = _client()
    with client:
        assert client.get("/healthz").json()["status"] == "ok"
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["backend"] == "mock:mock-1"
        assert client.get("/v1/models").status_code == 401
        models = client.get("/v1/models", headers=AUTH).json()
        assert {m["id"] for m in models["data"]} == {"protea-agent", "gpt-4o-mini"}
        text = client.get("/metrics").text
        assert "protea_requests_total" in text
        assert "protea_backend_ready 1" in text
    with pytest.raises(ValueError, match="PROTEA_FACADE_TOKEN"):
        create_app(ServeConfig(), MockProvider(), token=None)


def test_chat_completions_text_tools_and_schema_passthrough():
    mock = MockProvider(
        [
            "Order 7 is on its way.",
            ToolCall(id="c1", name="get_order", arguments={"order_id": "7"}),
            {"lane": "orders"},
        ]
    )
    client, mock = _client(mock)
    with client:
        body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Where is order 7?"}]}
        r = client.post("/v1/chat/completions", json=body, headers=AUTH)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["model"] == "protea-agent"
        assert data["choices"][0]["message"]["content"] == "Order 7 is on its way."
        assert data["usage"]["total_tokens"] > 0

        tools = [{"type": "function", "function": {"name": "get_order", "parameters": {"type": "object"}}}]
        r = client.post(
            "/v1/chat/completions", json={**body, "tools": tools}, headers=AUTH | {"x-protea-tenant": "acme"}
        )
        call = r.json()["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "get_order"
        assert json.loads(call["function"]["arguments"]) == {"order_id": "7"}
        assert r.json()["choices"][0]["finish_reason"] == "tool_calls"
        assert mock.requests[-1].tools[0].name == "get_order"
        assert mock.requests[-1].metadata.tenant_ref and mock.requests[-1].metadata.tenant_ref != "acme"

        fmt = {"type": "json_schema", "json_schema": {"name": "Lane", "schema": SCHEMA}}
        r = client.post("/v1/chat/completions", json={**body, "response_format": fmt}, headers=AUTH)
        assert json.loads(r.json()["choices"][0]["message"]["content"]) == {"lane": "orders"}
        assert mock.requests[-1].response_schema == SCHEMA
        assert mock.requests[-1].response_schema_name == "Lane"

        assert client.post("/v1/chat/completions", json={**body, "model": "nope"}, headers=AUTH).status_code == 404


def test_streaming_sse():
    client, _ = _client(MockProvider(["streamed text"]))
    with client:
        body = {"model": "protea-agent", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        with client.stream("POST", "/v1/chat/completions", json=body, headers=AUTH) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            raw = "".join(r.iter_text())
    events = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:") and "[DONE]" not in line]
    deltas = "".join(e["choices"][0]["delta"].get("content", "") for e in events if e["choices"])
    assert deltas == "streamed text"
    assert events[-1]["usage"]["completion_tokens"] > 0
    assert raw.strip().endswith("data: [DONE]")


def test_native_generate_and_validation_gate_with_repair():
    mock = MockProvider(["plain", "not json at all", {"lane": "stokvel"}, {"wrong": 1}, {"wrong": 2}])
    client, mock = _client(mock)
    gen = GenerationRequest(messages=[{"role": "user", "content": "split R450 between 4"}]).model_dump(mode="json")
    with client:
        r = client.post("/v1/generate", json=gen, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["content"] == "plain"
        assert mock.requests[-1].metadata.channel == "facade"

        r = client.post("/v1/generate/structured", json={"request": gen, "schema": SCHEMA}, headers=AUTH)
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "output": {"lane": "stokvel"}, "errors": [], "repairs": 1}
        repair_turn = mock.requests[-1].messages[-1]
        assert repair_turn.role == "user"
        assert "did not validate" in (repair_turn.content or "")

        r = client.post("/v1/generate/structured", json={"request": gen, "schema": SCHEMA}, headers=AUTH)
        assert r.status_code == 422
        assert r.json()["valid"] is False
        assert any("lane" in e for e in r.json()["errors"])
        assert r.json()["raw"]
        metrics = client.get("/metrics").text
        assert 'protea_gate_total{outcome="invalid"} 1' in metrics
        assert 'protea_gate_total{outcome="valid"} 1' in metrics


def test_provider_errors_and_draining():
    def boom(request):
        raise ProviderError("mock", "overloaded", retryable=True, status=529)

    client, _ = _client(MockProvider(boom))
    body = {"model": "protea-agent", "messages": [{"role": "user", "content": "hi"}]}
    with client:
        r = client.post("/v1/chat/completions", json=body, headers=AUTH)
        assert r.status_code == 502
        assert "protea_backend_errors_total" in client.get("/metrics").text
        client.app.state.facade.draining = True
        assert client.get("/healthz").status_code == 503


def test_parse_and_validate_reports_paths():
    obj, errors = parse_and_validate(SCHEMA, '```json\n{"lane": 3}\n```')
    assert obj is None
    assert errors == ["lane: 3 is not of type 'string'"]
    obj, errors = parse_and_validate(SCHEMA, "nonsense")
    assert obj is None
    assert errors[0].startswith("$: not valid JSON")
