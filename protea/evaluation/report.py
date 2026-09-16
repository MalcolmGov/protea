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
    return Path(f"{base}.json"), Path(f"{base}.md")  # not with_suffix: model ids such as Qwen2.5-0.5B carry dots


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
    advisories: list[str] = Field(default_factory=list)  # non-blocking: worth reading, not a gate
    kill_recommended: bool = False
    kill_reason: str = ""


def judge_coverage(cfg: EvaluationConfig, report: BenchmarkReport) -> str:
    """How much of a report's surface is unmeasured because judge checks could not run.

    Weights live in the config, not in the report, so both are needed to say how much of the score stands on
    deterministic checks alone — the number that decides whether a judge-free result is quotable.
    """
    skipped = report.judge_skipped_total
    if not skipped:
        return "no judge checks skipped"
    weights = {c.name: c.weight for c in cfg.categories}
    weight = sum(weights.get(c.name, 0.0) for c in report.categories if c.judge_skipped)
    cats = sum(1 for c in report.categories if c.judge_skipped)
    return (
        f"{skipped} judge check(s) skipped in {cats} categor{'y' if cats == 1 else 'ies'}"
        f" — {weight:.0%} of the weight is scored on deterministic checks only"
    )


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
    """Roadmap §4 gate + the ADR-016 budgets, applied per category.

    Blocking checks: comparability of the reports, ≥ base on every priority category, ≥ frontier on the
    frontier-gate categories, `base − tier budget` floors for every other tiered category, config gates, and a
    complete (non-partial) candidate report unless the config knowingly allows otherwise.
    Advisory (never blocks): a strict pass rate below base's — a mean can rise while hard failures grow, which is
    what made the P0.1 anomaly (pass rate up, mean down) look like progress.
    """
    reasons: list[str] = []
    advisories: list[str] = []
    metric = "strict pass rate" if cfg.gate_metric == "strict" else "score"
    cand = cfg.metric_of(candidate)
    for other in (base, frontier):
        if other is not None:
            reasons.extend(_comparable(candidate, other))
    if candidate.partial:
        note = f"candidate report is partial ({judge_coverage(cfg, candidate)})"
        advisories.append(note)
        if cfg.require_complete_report_for_release:
            reasons.append(note + "; judge the run or set require_complete_report_for_release: false to accept it")
    if base is not None:
        bs = cfg.metric_of(base)
        bs_pass = base.category_pass_rates()
        cand_pass = candidate.category_pass_rates()
        reasons.extend(
            f"{c} ({metric}): candidate {_fmt(cand.get(c, 0))} < base {_fmt(bs.get(c, 0))}"
            for c in cfg.priority_categories
            if cand.get(c, 0.0) < bs.get(c, 0.0)
        )
        # ADR-016 budgets for the categories the priority rule does not already hold at ≥ base.
        for name, spec in cfg.category_floors(bs).items():
            if name in cfg.priority_categories:
                continue  # held at ≥ base above, which is stricter than any budget
            score = cand.get(name, 0.0)
            if score < spec.floor:
                reasons.append(
                    f"{name}: candidate {_fmt(score)} < floor {_fmt(spec.floor)} ({spec.describe()}) [ADR-016]"
                )
        if cfg.gate_metric != "strict":  # under strict gating this is the gated number, not an advisory
            advisories.extend(
                f"{c.name}: strict pass rate {_fmt(cand_pass.get(c.name))} < base {_fmt(bs_pass.get(c.name))}"
                f" — the mean may be hiding hard failures (check the failure modes)"
                for c in cfg.categories
                if c.name in bs_pass and c.name in cand_pass and cand_pass[c.name] + 0.02 < bs_pass[c.name]
            )
        unfloored = cfg.unfloored_absolute_categories()
        if unfloored:
            advisories.append(
                f"no absolute floor set for {', '.join(unfloored)}: ADR-014 grades these tiers against an absolute "
                "floor, not against the baseline — set `min_score` on those categories"
            )
    if frontier is not None:
        fs = cfg.metric_of(frontier)
        reasons.extend(
            f"{c} ({metric}): candidate {_fmt(cand.get(c, 0))} < frontier {_fmt(fs.get(c, 0))}"
            for c in cfg.frontier_gate_categories
            if cand.get(c, 0.0) < fs.get(c, 0.0)
        )
    reasons.extend(f"config gate failed: {g}" for g in candidate.failed_gates)
    decision = Decision(release=not reasons, reasons=reasons, advisories=advisories)
    frontier_headline = cfg.aggregate(frontier) if frontier is not None else 0.0
    if frontier is not None and frontier_headline > 0:
        floor = cfg.kill_fraction_of_frontier * frontier_headline
        candidate_headline = cfg.aggregate(candidate)
        if candidate_headline < floor:
            decision.kill_recommended = True
            decision.kill_reason = (
                f"aggregate {_fmt(candidate_headline)} is below {cfg.kill_fraction_of_frontier:.0%} of the frontier "
                f"({_fmt(frontier_headline)}); strategy-review A1 says stop training unless the economics changed"
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
    if any(r.partial for r in reports.values()):
        lines.append("| judge checks skipped | " + " | ".join(str(r.judge_skipped_total) for r in reports.values()) + " |")
    if decision is not None:
        metric = "strict pass rate" if cfg.gate_metric == "strict" else "score"
        lines += ["", f"**Release gate ({metric}):** {'PASS' if decision.release else 'FAIL'}"]
        lines += [f"- {r}" for r in decision.reasons]
        if decision.advisories:
            lines += ["", "**Advisory (does not block):**"]
            lines += [f"- {a}" for a in decision.advisories]
        if decision.kill_recommended:
            lines += ["", f"**Kill criterion triggered:** {decision.kill_reason}"]
    if "base" in reports:
        floors = cfg.category_floors(reports["base"].category_scores())
        if floors:
            lines += ["", "**ADR-016 floors (base − tier budget):**"]
            lines += [f"- {f.category}: ≥ {_fmt(f.floor)} ({f.describe()})" for f in floors.values()]
        absolute = [c for c in cfg.categories if c.tier in cfg.absolute_tiers]
        if absolute:
            lines += ["", "**Absolute floors (ADR-014 — never ≥ base):**"]
            lines += [
                f"- {c.name}: " + (f"≥ {_fmt(c.min_score)}" if c.min_score is not None else "**not set** — this tier is unfloored")
                for c in absolute
            ]
    return "\n".join(lines) + "\n"
