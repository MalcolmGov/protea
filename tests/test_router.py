from pathlib import Path

import pytest

from protea.providers.base import InMemoryUsageSink, ProviderError
from protea.providers.mock import MockProvider
from protea.router import (
    InMemoryRouteSink,
    ModelRouter,
    NoRouteError,
    RouteRequest,
    RoutingPolicy,
    SuccessHistory,
    canary_bucket,
    classify,
    complexity,
    compute_confidence,
    describe,
    load_matrix,
)
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message, ToolCall, ToolSchema

REPORTS = Path(__file__).parent / "fixtures" / "reports"
SCHEMA = {"type": "object", "properties": {"lane": {"type": "string"}}, "required": ["lane"]}


def _policy(**overrides) -> RoutingPolicy:
    base = {
        "name": "test",
        "version": "0",
        "candidates": [
            {
                "name": "protea-agent",
                "provider": "protea",
                "model": "protea-agent",
                "self_hosted": True,
                "cost_tier": 0,
                "quality_tier": 3,
                "requires_benchmark": True,
                "task_types": ["tool_calling", "structured_output", "agent_generation", "routing"],
                "version": "0.1",
            },
            {"name": "frontier-a", "provider": "anthropic", "model": "a", "cost_tier": 3, "quality_tier": 4},
            {
                "name": "frontier-b",
                "provider": "openai",
                "model": "b",
                "cost_tier": 2,
                "quality_tier": 3,
                "max_complexity": "medium",
            },
        ],
        "default_route": "frontier-a",
        "fallback_order": ["frontier-a", "frontier-b"],
        "thresholds": {"tool_calling": 0.8, "agent_generation": 0.75},
        "canary_percent": 100,
    }
    base.update(overrides)
    return RoutingPolicy.model_validate(base)


def _router(policy: RoutingPolicy | None = None, **providers) -> tuple[ModelRouter, InMemoryRouteSink]:
    sink = InMemoryRouteSink()
    router = ModelRouter(policy or _policy(), matrix=load_matrix(REPORTS), providers=providers, sink=sink)
    return router, sink


def _req(text: str = "hello", **kw) -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content=text)], **kw)


# ---- policy + matrix ------------------------------------------------------------------------------------
def test_policy_rejects_inconsistent_definitions():
    with pytest.raises(ValueError, match="default_route must not require a benchmark"):
        _policy(default_route="protea-agent")
    with pytest.raises(ValueError, match="unknown candidates"):
        _policy(fallback_order=["nope"])


def test_matrix_ignores_mock_and_reads_latest_report():
    matrix = load_matrix(REPORTS)
    assert set(matrix.models) == {"protea:protea-agent"}
    assert matrix.score("protea:protea-agent", "tool_calling") == 0.9
    ok, reason = matrix.eligible("protea:protea-agent", "agent_generation", 0.75)
    assert not ok
    assert "0.60 < threshold 0.75" in reason
    assert matrix.eligible("mock:mock-1", "tool_calling", 0.1) == (False, "no benchmark report for mock:mock-1")


# ---- classification ----------------------------------------------------------------------------------------
def test_classifier_uses_metadata_then_shape_then_phrases():
    req = _req("Design an agent for a bakery")
    assert classify(req) == "agent_generation"
    req.metadata.task_type = "repair"
    assert classify(req) == "repair"
    assert classify(_req(tools=[ToolSchema(name="lookup_order")])) == "tool_calling"
    assert classify(_req(response_schema=SCHEMA)) == "structured_output"
    assert classify(_req("hi")) == "chat"
    assert complexity(100, 0, 0, False) == "low"
    assert complexity(5000, 5, 0, False) == "medium"
    assert complexity(20000, 10, 3, True) == "high"
    view = describe(_req("Please process the refund", tools=[ToolSchema(name="issue_refund")]), connector_count=1)
    assert view.financial is True
    assert view.tool_count == 1
    assert view.connector_count == 1


# ---- decisions ---------------------------------------------------------------------------------------------
def test_benchmark_gate_and_canary_decide_protea_eligibility():
    router, _ = _router()
    d = router.route(RouteRequest(task_type="tool_calling", tenant_ref="t1"))
    assert d.route == "protea-agent"
    assert d.version == "0.1"
    assert d.canary is True
    assert d.fallbacks == ["frontier-a", "frontier-b"]
    weak = router.route(RouteRequest(task_type="agent_generation", tenant_ref="t1"))
    assert weak.route == "frontier-a"
    assert "0.60 < threshold 0.75" in next(v.reason for v in weak.considered if v.route == "protea-agent")
    unknown = router.route(RouteRequest(task_type="business_reasoning"))
    assert unknown.route == "frontier-a"
    assert "does not serve business_reasoning" in unknown.considered[0].reason
    off, _ = _router(_policy(canary_percent=0))
    assert off.route(RouteRequest(task_type="tool_calling", tenant_ref="t1")).route == "frontier-a"
    assert (
        "outside canary share"
        in off.route(RouteRequest(task_type="tool_calling", tenant_ref="t1")).considered[0].reason
    )


def test_canary_bucket_is_stable_and_partitions_tenants():
    assert canary_bucket("tenant-1") == canary_bucket("tenant-1")
    buckets = [canary_bucket(f"tenant-{i}") for i in range(200)]
    assert all(0 <= b < 100 for b in buckets)
    assert 5 < sum(1 for b in buckets if b < 25) < 95
    half, _ = _router(_policy(canary_percent=50))
    routes = {half.route(RouteRequest(task_type="tool_calling", tenant_ref=f"t{i}")).route for i in range(40)}
    assert routes == {"protea-agent", "frontier-a"}


def test_privacy_complexity_cost_and_pinning():
    router, _ = _router()
    strict = router.route(RouteRequest(task_type="tool_calling", privacy="strict", tenant_ref="t"))
    assert strict.route == "protea-agent"
    assert strict.fallbacks == []
    with pytest.raises(NoRouteError, match="self-hosted"):
        router.route(RouteRequest(task_type="chat", privacy="strict"))
    hard = router.route(RouteRequest(task_type="chat", prompt_chars=20000, tool_count=10, financial=True))
    assert hard.complexity == "high"
    assert hard.route == "frontier-a"
    assert "frontier-b" not in hard.fallbacks
    assert router.route(RouteRequest(task_type="chat", cost_policy="cheapest")).route == "frontier-b"
    assert router.route(RouteRequest(task_type="chat", cost_policy="best")).route == "frontier-a"
    pinned = router.route(RouteRequest(task_type="chat", pinned_route="frontier-b"))
    assert pinned.route == "frontier-b"
    assert pinned.pinned is True
    rejected = router.route(RouteRequest(task_type="chat", pinned_route="protea-agent"))
    assert rejected.pinned is False
    assert rejected.reason.startswith("pinned route protea-agent rejected")


# ---- execution ---------------------------------------------------------------------------------------------
async def test_execute_falls_back_on_invalid_output_and_records_events():
    usage = InMemoryUsageSink()
    protea = MockProvider(["not json", "still not json"], model="protea-agent", usage_sink=usage)
    protea.name = "protea"
    frontier = MockProvider([{"lane": "sales"}], model="a", usage_sink=usage)
    router, sink = _router(**{"protea-agent": protea, "frontier-a": frontier})
    req = _req("Classify the user's message into exactly one lane")
    req.metadata.tenant_ref = "t1"
    out = await router.execute(req, schema=SCHEMA, max_repairs=1)
    assert out.ok is True
    assert out.decision.route == "protea-agent"
    assert out.served_route == "frontier-a"
    assert out.gate is not None
    assert out.gate.output == {"lane": "sales"}
    event = sink.events[0]
    assert event.attempts == 2
    assert event.fallbacks[0].from_route == "protea-agent"
    assert event.fallbacks[0].to_route == "frontier-a"
    assert "validation failed after 1 repair" in event.fallbacks[0].reason
    assert event.served_model == "mock:a"
    assert event.input_tokens > 0
    assert event.confidence is not None
    assert [e.fallback for e in usage.events] == [False, False, True]
    assert router.history.rate("protea-agent", "routing") == (0.0, 1)
    assert router.history.rate("frontier-a", "routing") == (1.0, 1)


async def test_execute_falls_back_on_provider_error_and_reports_total_failure():
    class Broken(MockProvider):
        async def _generate(self, request):
            raise ProviderError(self.name, "boom", retryable=True)

    broken = Broken(model="protea-agent")
    broken.name = "protea"
    router, sink = _router(
        **{"protea-agent": broken, "frontier-a": MockProvider(["fine"]), "frontier-b": MockProvider()}
    )
    out = await router.execute(
        _req("hi", tools=[ToolSchema(name="lookup")]),
        route_request=RouteRequest(task_type="tool_calling", tenant_ref="t"),
    )
    assert out.ok is True
    assert out.served_route == "frontier-a"
    assert sink.events[0].fallbacks[0].reason == "protea: boom"
    all_broken, sink2 = _router(
        _policy(max_attempts=2), **{"frontier-a": Broken(model="a"), "frontier-b": Broken(model="b")}
    )
    out = await all_broken.execute(_req("hi"))
    assert out.ok is False
    assert out.response is None
    assert "boom" in (out.error or "")
    assert sink2.events[0].attempts == 2
    assert sink2.events[0].ok is False


async def test_low_confidence_triggers_fallback():
    length = GenerationResponse(content="truncated", finish_reason="length", provider="mock", model="a")
    router, sink = _router(**{"frontier-a": MockProvider([length]), "frontier-b": MockProvider(["complete"])})
    out = await router.execute(_req("hi"))
    assert out.ok is True
    assert out.served_route == "frontier-b"
    assert sink.events[0].fallbacks[0].reason == "finish_reason=length"


def test_confidence_is_evidence_based():
    req = _req("x", tools=[ToolSchema(name="known")])
    resp = GenerationResponse(
        tool_calls=[ToolCall(id="1", name="ghost")], finish_reason="tool_calls", provider="m", model="m"
    )
    conf = compute_confidence(req, resp, gate_valid=None, benchmark_score=0.9, history_rate=None)
    names = {s.name: s.value for s in conf.signals}
    assert names["tools_exist"] == 0.0
    assert names["benchmark"] == 0.9
    assert 0.0 < conf.score < 0.9
    ok_resp = GenerationResponse(content="{}", provider="m", model="m")
    valid = compute_confidence(
        req, ok_resp, gate_valid=True, gate_repairs=1, benchmark_score=None, history_rate=0.5, history_n=10
    )
    assert {s.name for s in valid.signals} == {"schema_valid", "history"}
    assert valid.score == pytest.approx((0.7 * 3 + 0.5 * 1) / 4)
    history = SuccessHistory(window=3)
    for ok in (True, False, False, True):
        history.record("r", "chat", ok)
    assert history.rate("r", "chat") == (pytest.approx(1 / 3), 3)
