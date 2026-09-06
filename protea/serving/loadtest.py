"""Load test for the inference facade (roadmap Phase 10): concurrent `/v1/generate` calls, latency percentiles,
throughput and error rate. Runs against any facade URL; in tests against the app in-process (ASGI transport)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field


class LoadOptions(BaseModel):
    concurrency: int = Field(default=8, ge=1, le=256)
    requests: int = Field(default=100, ge=1, le=100_000)
    max_tokens: int = Field(default=64, ge=1, le=4096)
    prompt: str = "Reply with one short sentence about order tracking."
    timeout_s: float = Field(default=60.0, gt=0)
    latency_budget_ms: int | None = None  # fail the run when p95 exceeds this


class LoadReport(BaseModel):
    url: str
    requests: int
    concurrency: int
    ok: int = 0
    errors: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    wall_s: float = 0.0
    rps: float = 0.0
    latency_ms_p50: int = 0
    latency_ms_p95: int = 0
    latency_ms_p99: int = 0
    latency_ms_max: int = 0
    output_tokens: int = 0
    output_tokens_per_s: float = 0.0
    error_rate: float = 0.0
    within_budget: bool | None = None
    sample_error: str | None = None

    def summary(self) -> str:
        budget = "" if self.within_budget is None else (" within budget" if self.within_budget else " OVER BUDGET")
        return (
            f"{self.ok}/{self.requests} ok, {self.errors} errors ({self.error_rate:.1%}), {self.rps:.1f} req/s, "
            f"p50 {self.latency_ms_p50} ms p95 {self.latency_ms_p95} ms p99 {self.latency_ms_p99} ms, "
            f"{self.output_tokens_per_s:.0f} output tok/s{budget}"
        )


def _pct(values: list[float], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return int(ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))])


async def run_load(
    url: str,
    *,
    token: str | None = None,
    options: LoadOptions | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LoadReport:
    opts = options or LoadOptions()
    base = url.rstrip("/")
    headers = {"authorization": f"Bearer {token}"} if token else {}
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": opts.prompt}],
        "max_tokens": opts.max_tokens,
        "metadata": {"channel": "loadtest", "task_type": "chat"},
    }
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    tokens = 0
    sample_error: str | None = None
    sem = asyncio.Semaphore(opts.concurrency)

    async def one(client: httpx.AsyncClient) -> None:
        nonlocal tokens, sample_error
        async with sem:
            started = time.perf_counter()
            try:
                resp = await client.post(f"{base}/v1/generate", json=body, headers=headers)
                key = str(resp.status_code)
                if resp.status_code == 200:
                    tokens += int((resp.json().get("usage") or {}).get("output_tokens") or 0)
                elif sample_error is None:
                    sample_error = f"{resp.status_code}: {resp.text[:120]}"
            except httpx.HTTPError as exc:
                key = "transport"
                if sample_error is None:
                    sample_error = f"{exc.__class__.__name__}: {str(exc)[:120]}"
            latencies.append((time.perf_counter() - started) * 1000)
            statuses[key] = statuses.get(key, 0) + 1

    wall_started = time.perf_counter()
    async with httpx.AsyncClient(timeout=opts.timeout_s, transport=transport) as client:
        await asyncio.gather(*(one(client) for _ in range(opts.requests)))
    wall = time.perf_counter() - wall_started
    ok = statuses.get("200", 0)
    report = LoadReport(
        url=base,
        requests=opts.requests,
        concurrency=opts.concurrency,
        ok=ok,
        errors=opts.requests - ok,
        status_counts=statuses,
        wall_s=round(wall, 3),
        rps=round(opts.requests / wall, 2) if wall > 0 else 0.0,
        latency_ms_p50=_pct(latencies, 0.5),
        latency_ms_p95=_pct(latencies, 0.95),
        latency_ms_p99=_pct(latencies, 0.99),
        latency_ms_max=int(max(latencies)) if latencies else 0,
        output_tokens=tokens,
        output_tokens_per_s=round(tokens / wall, 1) if wall > 0 else 0.0,
        error_rate=round((opts.requests - ok) / opts.requests, 4),
        sample_error=sample_error,
    )
    if opts.latency_budget_ms is not None:
        report.within_budget = report.latency_ms_p95 <= opts.latency_budget_ms
    return report
