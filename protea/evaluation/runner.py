"""Benchmark runner: any ModelProvider × a task set → BenchmarkReport (spec §22–§23, §52)."""

from __future__ import annotations

import asyncio
import statistics
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from protea.config.models import EvaluationConfig
from protea.evaluation.driver import DriveOptions, drive_conversation
from protea.evaluation.evaluators import TaskResult, evaluate
from protea.evaluation.judge import apply_judge
from protea.evaluation.tasks import EvalTask, approx_prompt_chars
from protea.providers.base import ModelProvider
from protea.schemas.generation import RequestMeta


class CategoryScore(BaseModel):
    name: str
    n: int = 0
    score: float = 0.0  # mean task score
    pass_rate: float = 0.0
    judge_skipped: int = 0
    errors: int = 0


class BenchmarkReport(BaseModel):
    suite: str
    version: str
    run_id: str
    created_at: str
    provider: str
    model: str
    judge: str | None = None
    config_hash: str = ""
    task_set_hash: str = ""
    tasks_total: int = 0
    tasks_run: int = 0
    categories: list[CategoryScore] = Field(default_factory=list)
    zarascore: float = 0.0  # weighted mean of category scores (partial credit per check)
    zarascore_strict: float = 0.0  # weighted mean of category pass rates (a task passes only if every check passes)
    partial: bool = False  # some judge checks skipped or categories uncovered
    failed_gates: list[str] = Field(default_factory=list)
    latency_ms_p50: int = 0
    latency_ms_p95: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None
    failure_modes: dict[str, int] = Field(default_factory=dict)
    by_language: dict[str, float] = Field(default_factory=dict)
    results: list[TaskResult] = Field(default_factory=list)

    def category_scores(self) -> dict[str, float]:
        return {c.name: c.score for c in self.categories}

    def category_pass_rates(self) -> dict[str, float]:
        return {c.name: c.pass_rate for c in self.categories}


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return ordered[idx]


def _category_rows(cfg: EvaluationConfig, results: list[TaskResult]) -> list[CategoryScore]:
    rows = []
    for c in cfg.categories:
        rs = [r for r in results if r.category == c.name]
        rows.append(
            CategoryScore(
                name=c.name,
                n=len(rs),
                score=statistics.fmean(r.score for r in rs) if rs else 0.0,
                pass_rate=sum(r.passed for r in rs) / len(rs) if rs else 0.0,
                judge_skipped=sum(bool(r.judge_skipped) for r in rs),
                errors=sum(bool(r.error) for r in rs),
            )
        )
    return rows


def _by_language(results: list[TaskResult]) -> dict[str, float]:
    langs: dict[str, list[float]] = {}
    for r in results:
        langs.setdefault(r.language, []).append(r.score)
    return {k: statistics.fmean(v) for k, v in sorted(langs.items())}


def estimate_cost(cfg: EvaluationConfig, model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = cfg.price_for(model)
    if price is None:
        return None
    return round(input_tokens / 1e6 * price.input + output_tokens / 1e6 * price.output, 4)


def preflight(cfg: EvaluationConfig, tasks: list[EvalTask], model: str) -> dict[str, Any]:
    """The §52 summary shown before a paid run: what will be sent, roughly how many tokens, roughly what it costs."""
    prompt_tokens = sum(approx_prompt_chars(t) // 4 for t in tasks)
    rounds = sum(1 + len(t.followups) for t in tasks) * 2  # each turn may take a tool round
    input_tokens = prompt_tokens * 2
    output_tokens = rounds * 200
    return {
        "tasks": len(tasks),
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_usd": estimate_cost(cfg, model, input_tokens, output_tokens),
        "families": len({t.family for t in tasks if t.family}),
        "data_exposure": "task prompts contain catalogue system prompts and business knowledge excerpts",
    }


def summarise(cfg: EvaluationConfig, report: BenchmarkReport, results: list[TaskResult]) -> BenchmarkReport:
    report.results = results
    report.tasks_run = len(results)
    report.categories = _category_rows(cfg, results)
    scores = report.category_scores()
    report.zarascore = round(cfg.zarascore(scores), 4)
    report.zarascore_strict = round(cfg.zarascore(report.category_pass_rates()), 4)
    report.failed_gates = cfg.failed_gates(scores)
    report.partial = any(r.judge_skipped for r in results) or any(c.n == 0 for c in report.categories)
    latencies = [r.latency_ms for r in results if not r.error]
    report.latency_ms_p50 = _percentile(latencies, 0.5)
    report.latency_ms_p95 = _percentile(latencies, 0.95)
    report.input_tokens = sum(r.input_tokens for r in results)
    report.output_tokens = sum(r.output_tokens for r in results)
    report.estimated_cost_usd = estimate_cost(cfg, report.model, report.input_tokens, report.output_tokens)
    report.failure_modes = dict(Counter(m for r in results for m in r.failure_modes).most_common())
    report.by_language = _by_language(results)
    return report


async def run_task(
    cfg: EvaluationConfig, task: EvalTask, provider: ModelProvider, judge: ModelProvider | None
) -> TaskResult:
    first, *rest = [m for m in task.messages if m.role == "user"]
    prefix = [m for m in task.messages if m.role != "user"]
    t = await drive_conversation(
        provider,
        prefix,
        [first.content or "", *(m.content or "" for m in rest), *task.followups],
        task.tools,
        tool_results=task.tool_results,
        options=DriveOptions(
            max_rounds=cfg.max_tool_rounds,
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
            metadata=RequestMeta(task_type=task.category.value, channel="zarabench", agent_id=task.id),
        ),
    )
    result = evaluate(task, t)
    if judge is not None:
        result = await apply_judge(judge, task, t, result)
    return result


async def run_benchmark(
    cfg: EvaluationConfig,
    tasks: list[EvalTask],
    provider: ModelProvider,
    *,
    judge: ModelProvider | None = None,
    run_id: str | None = None,
    config_hash: str = "",
    task_set_hash: str = "",
) -> BenchmarkReport:
    sem = asyncio.Semaphore(cfg.concurrency)

    async def one(task: EvalTask) -> TaskResult:
        async with sem:
            return await run_task(cfg, task, provider, judge)

    results = list(await asyncio.gather(*(one(t) for t in tasks)))
    now = datetime.now(UTC)
    report = BenchmarkReport(
        suite=cfg.suite,
        version=cfg.version,
        run_id=run_id or now.strftime("%Y%m%dT%H%M%SZ"),
        created_at=now.isoformat(),
        provider=provider.name,
        model=provider.model,
        judge=f"{judge.name}:{judge.model}" if judge else None,
        config_hash=config_hash,
        task_set_hash=task_set_hash,
        tasks_total=len(tasks),
    )
    return summarise(cfg, report, results)
