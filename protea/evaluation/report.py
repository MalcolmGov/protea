"""Report files (JSON + Markdown), comparison tables and the release / kill decisions (spec §23, §67, strategy A1)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from protea.config.models import EvaluationConfig
from protea.evaluation.runner import BenchmarkReport


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "model"


def report_paths(report: BenchmarkReport, out_dir: Path) -> tuple[Path, Path]:
    base = (
        out_dir / f"{report.suite}-{report.version}" / f"{_slug(report.provider)}-{_slug(report.model)}-{report.run_id}"
    )
    return base.with_suffix(".json"), base.with_suffix(".md")


def _fmt(x: float | None, pct: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.1f}%" if pct else f"{x:.4f}"


def render_markdown(report: BenchmarkReport, cfg: EvaluationConfig | None = None, *, top_failures: int = 12) -> str:
    weights = {c.name: c.weight for c in cfg.categories} if cfg else {}
    lines = [
        f"# {report.suite} {report.version} — {report.provider}:{report.model}",
        "",
        f"- Run: `{report.run_id}` at {report.created_at}",
        f"- Tasks: {report.tasks_run}/{report.tasks_total}; judge: {report.judge or 'none'}"
        + ("; **partial** (judge checks skipped or categories uncovered)" if report.partial else ""),
        f"- Task set sha256: `{report.task_set_hash[:12] or 'n/a'}`; config hash: `{report.config_hash[:12] or 'n/a'}`",
        f"- **ZaraScore: {_fmt(report.zarascore)}** (strict, all checks per task: {_fmt(report.zarascore_strict)})"
        + (f" — failed gates: {', '.join(report.failed_gates)}" if report.failed_gates else " — all gates passed"),
        f"- Latency p50/p95: {report.latency_ms_p50} / {report.latency_ms_p95} ms; tokens in/out: "
        f"{report.input_tokens:,} / {report.output_tokens:,}; estimated cost: "
        + (f"USD {report.estimated_cost_usd:.2f}" if report.estimated_cost_usd is not None else "unknown (no price)"),
        "",
        "| Category | Weight | n | Score | Pass rate | Judge skipped | Errors |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report.categories:
        w = f"{weights.get(c.name, 0):.2f}" if weights else "-"
        lines.append(
            f"| {c.name} | {w} | {c.n} | {_fmt(c.score)} | {_fmt(c.pass_rate)} | {c.judge_skipped} | {c.errors} |"
        )
    if report.by_language:
        lines += ["", "| Language | Score |", "|---|---|"]
        lines += [f"| {lang} | {_fmt(s)} |" for lang, s in report.by_language.items()]
    if report.failure_modes:
        lines += ["", "## Failure modes", "", "| Check | Failures |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in list(report.failure_modes.items())[:top_failures]]
    return "\n".join(lines) + "\n"


def write_report(report: BenchmarkReport, out_dir: Path, cfg: EvaluationConfig | None = None) -> tuple[Path, Path]:
    json_path, md_path = report_paths(report, out_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report, cfg), encoding="utf-8")
    return json_path, md_path


def load_report(path: Path) -> BenchmarkReport:
    return BenchmarkReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


class Decision(BaseModel):
    release: bool
    reasons: list[str] = Field(default_factory=list)
    kill_recommended: bool = False
    kill_reason: str = ""


def _comparable(a: BenchmarkReport, b: BenchmarkReport) -> list[str]:
    problems = []
    if a.task_set_hash and b.task_set_hash and a.task_set_hash != b.task_set_hash:
        problems.append("reports were produced on different task sets")
    if (a.suite, a.version) != (b.suite, b.version):
        problems.append("reports come from different suites/versions")
    return problems


def release_decision(
    cfg: EvaluationConfig, candidate: BenchmarkReport, base: BenchmarkReport | None, frontier: BenchmarkReport | None
) -> Decision:
    """Roadmap §4 gate: ≥ base on every priority category, ≥ frontier on the frontier-gate categories, config gates."""
    reasons: list[str] = []
    cand = candidate.category_scores()
    for other in (base, frontier):
        if other is not None:
            reasons.extend(_comparable(candidate, other))
    if candidate.partial:
        reasons.append("candidate report is partial (judge checks skipped)")
    if base is not None:
        bs = base.category_scores()
        reasons.extend(
            f"{c}: candidate {_fmt(cand.get(c, 0))} < base {_fmt(bs.get(c, 0))}"
            for c in cfg.priority_categories
            if cand.get(c, 0.0) < bs.get(c, 0.0)
        )
    if frontier is not None:
        fs = frontier.category_scores()
        reasons.extend(
            f"{c}: candidate {_fmt(cand.get(c, 0))} < frontier {_fmt(fs.get(c, 0))}"
            for c in cfg.frontier_gate_categories
            if cand.get(c, 0.0) < fs.get(c, 0.0)
        )
    reasons.extend(f"config gate failed: {g}" for g in candidate.failed_gates)
    decision = Decision(release=not reasons, reasons=reasons)
    if frontier is not None and frontier.zarascore > 0:
        floor = cfg.kill_fraction_of_frontier * frontier.zarascore
        if candidate.zarascore < floor:
            decision.kill_recommended = True
            decision.kill_reason = (
                f"ZaraScore {_fmt(candidate.zarascore)} is below {cfg.kill_fraction_of_frontier:.0%} of the frontier "
                f"({_fmt(frontier.zarascore)}); strategy-review A1 says stop training unless the economics changed"
            )
    return decision


def render_comparison(cfg: EvaluationConfig, reports: dict[str, BenchmarkReport], decision: Decision | None) -> str:
    names = list(reports)
    lines = ["| Category | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for c in cfg.categories:
        cells = [_fmt(reports[n].category_scores().get(c.name, 0.0)) for n in names]
        lines.append(f"| {c.name} | " + " | ".join(cells) + " |")
    lines.append("| **ZaraScore** | " + " | ".join(f"**{_fmt(reports[n].zarascore)}**" for n in names) + " |")
    lines.append("| ZaraScore (strict) | " + " | ".join(_fmt(reports[n].zarascore_strict) for n in names) + " |")
    lines.append("| latency p50 (ms) | " + " | ".join(str(reports[n].latency_ms_p50) for n in names) + " |")
    lines.append(
        "| est. cost (USD) | "
        + " | ".join(
            f"{reports[n].estimated_cost_usd:.2f}" if reports[n].estimated_cost_usd is not None else "n/a"
            for n in names
        )
        + " |"
    )
    if decision is not None:
        lines += ["", f"**Release gate:** {'PASS' if decision.release else 'FAIL'}"]
        lines += [f"- {r}" for r in decision.reasons]
        if decision.kill_recommended:
            lines += ["", f"**Kill criterion triggered:** {decision.kill_reason}"]
    return "\n".join(lines) + "\n"
