from pathlib import Path

from fastapi.testclient import TestClient

from protea.providers.mock import MockProvider
from protea.router import InMemoryRouteSink, ModelRouter, RoutingPolicy, load_matrix
from protea.serving.app import create_app
from protea.serving.config import ServeConfig

REPORTS = Path(__file__).parent / "fixtures" / "reports"
AUTH = {"authorization": "Bearer secret"}
SCHEMA = {"type": "object", "properties": {"lane": {"type": "string"}}, "required": ["lane"]}
POLICY = RoutingPolicy.model_validate(
    {
        "name": "t",
        "version": "0",
        "candidates": [
            {
                "name": "protea-agent",
                "provider": "protea",
                "model": "protea-agent",
                "self_hosted": True,
                "requires_benchmark": True,
                "cost_tier": 0,
            },
            {"name": "frontier", "provider": "anthropic", "model": "a", "cost_tier": 3, "quality_tier": 4},
        ],
        "default_route": "frontier",
        "fallback_order": ["frontier"],
        "thresholds": {"structured_output": 0.9},
        "canary_percent": 100,
    }
)


def _client(**providers) -> tuple[TestClient, InMemoryRouteSink]:
    sink = InMemoryRouteSink()
    router = ModelRouter(POLICY, matrix=load_matrix(REPORTS), providers=providers, sink=sink)
    app = create_app(ServeConfig(backend="mock"), MockProvider(), token="secret", router=router)
    return TestClient(app), sink


def _body(**extra) -> dict:
    return {"request": {"messages": [{"role": "user", "content": "Classify into exactly one lane"}]}, **extra}


def test_route_endpoints_absent_without_router():
    app = create_app(ServeConfig(backend="mock"), MockProvider(), token="secret")
    with TestClient(app) as client:
        assert client.post("/v1/route/generate", json=_body(), headers=AUTH).status_code == 404


def test_routed_structured_call_falls_back_and_reports_decision():
    protea = MockProvider(["nope", "nope"], model="protea-agent")
    protea.name = "protea"
    client, sink = _client(**{"protea-agent": protea, "frontier": MockProvider([{"lane": "support"}])})
    with client:
        assert client.post("/v1/route/structured", json=_body(schema=SCHEMA)).status_code == 401
        r = client.post("/v1/route/structured", json=_body(schema=SCHEMA), headers={**AUTH, "x-protea-tenant": "acme"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["route"] == "protea-agent"
        assert data["served_route"] == "frontier"
        assert data["output"] == {"lane": "support"}
        assert data["attempts"] == 2
        assert data["fallbacks"][0]["from_route"] == "protea-agent"
        assert data["confidence"]["score"] > 0.5
        assert sink.events[0].tenant_ref is not None
        assert "protea_route_fallbacks_total 1" in client.get("/metrics").text
        assert client.post("/v1/route/structured", json=_body(), headers=AUTH).status_code == 400


def test_routed_generate_and_policy_refusals():
    client, _ = _client(frontier=MockProvider(["hello"]))
    with client:
        r = client.post("/v1/route/generate", json=_body(task_type="chat", cost_policy="cheapest"), headers=AUTH)
        assert r.status_code == 200
        assert r.json()["response"]["content"] == "hello"
        assert r.json()["served_route"] == "frontier"
        strict = client.post("/v1/route/generate", json=_body(task_type="chat", privacy="strict"), headers=AUTH)
        assert strict.status_code == 409
        invalid = client.post("/v1/route/structured", json=_body(schema=SCHEMA, task_type="chat"), headers=AUTH)
        assert invalid.status_code == 422
        assert invalid.json()["valid"] is False
