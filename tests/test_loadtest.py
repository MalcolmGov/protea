import asyncio

import httpx

from protea.providers.mock import MockProvider
from protea.serving.app import create_app
from protea.serving.config import ServeConfig
from protea.serving.loadtest import LoadOptions, run_load


def _app(provider=None):
    return create_app(ServeConfig(backend="mock"), provider or MockProvider(), token="secret")


def test_loadtest_reports_latency_and_throughput():
    transport = httpx.ASGITransport(app=_app())
    report = asyncio.run(
        run_load(
            "http://facade",
            token="secret",
            options=LoadOptions(concurrency=4, requests=20, latency_budget_ms=5000),
            transport=transport,
        )
    )
    assert report.requests == 20
    assert report.ok == 20
    assert report.errors == 0
    assert report.error_rate == 0.0
    assert report.rps > 0
    assert report.latency_ms_p50 <= report.latency_ms_p95 <= report.latency_ms_max
    assert report.output_tokens == 20 * 8
    assert report.within_budget is True
    assert "20/20 ok" in report.summary()


def test_loadtest_counts_auth_failures_and_budget_breach():
    transport = httpx.ASGITransport(app=_app())
    report = asyncio.run(
        run_load(
            "http://facade",
            token="wrong",
            options=LoadOptions(concurrency=2, requests=5, latency_budget_ms=0),
            transport=transport,
        )
    )
    assert report.ok == 0
    assert report.status_counts == {"401": 5}
    assert report.error_rate == 1.0
    assert report.within_budget is False
    assert report.sample_error.startswith("401")
