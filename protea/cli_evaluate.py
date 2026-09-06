"""`protea evaluate` — ZaraBench authoring, sealing, running, comparing (Phase 3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

evaluate_app = typer.Typer(help="Evaluation framework and the ZaraBench suite.", no_args_is_help=True)

DEFAULT_CONFIG = Path("configs/evaluation/zarabench-0.1.yaml")
FREE_PROVIDERS = {"mock", "reference", "local"}  # local runs in-process: no tokens leave the machine


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


def _load(config: Path, root: Path):
    from protea.config import config_hash, load_config
    from protea.evaluation.tasks import load_tasks, task_set_hash

    cfg = load_config(config, "evaluation")
    if not cfg.tasks_path:
        _fail("evaluation config has no tasks_path")
    tasks_path = root / cfg.tasks_path
    if not tasks_path.exists():
        _fail(f"task set not found: {tasks_path} (run `protea evaluate author` or check tasks_path)")
    return cfg, config_hash(cfg), load_tasks(tasks_path), task_set_hash(tasks_path)


def _select(tasks, categories: str | None, limit: int | None, language: str | None):
    if categories:
        wanted = {c.strip() for c in categories.split(",")}
        tasks = [t for t in tasks if t.category.value in wanted]
    if language:
        tasks = [t for t in tasks if t.language == language]
    return tasks[:limit] if limit else tasks


def _build(name: str, model: str | None, tasks):
    from protea.evaluation.reference import ReferenceProvider
    from protea.providers import ProviderNotConfigured, build_provider

    if name == "reference":
        return ReferenceProvider(tasks)
    try:
        return build_provider(name, model=model)
    except ProviderNotConfigured as exc:
        _fail(str(exc))
        return None


def _generator_models(root: Path) -> set[str]:
    from protea.config import get_settings
    from protea.registry import DatasetRegistry

    path = Path(get_settings().registry_dir) / "datasets.json"
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return set()
    return {m for e in DatasetRegistry(path).list() for m in e.generator_models}


@evaluate_app.command("tasks")
def evaluate_tasks(
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path("."), help="Repository root."),
) -> None:
    """Show the task set: counts per category and language, judge-dependent tasks, held-out families."""
    from protea.evaluation.tasks import task_stats

    _, _, tasks, digest = _load(config, root)
    st = task_stats(tasks)
    typer.echo(f"tasks      {st['total']}  sha256 {digest[:12]}")
    typer.echo("category   " + ", ".join(f"{k}={v}" for k, v in st["by_category"].items()))
    typer.echo("language   " + ", ".join(f"{k}={v}" for k, v in st["by_language"].items()))
    typer.echo(f"judge      {st['needs_judge']} tasks need a judge; {st['with_reference']} carry a reference answer")
    typer.echo(f"families   {len(st['families'])} held out")


@evaluate_app.command("author")
def evaluate_author(
    build_dir: Path = typer.Argument(..., exists=True, help="A `dataset build` output directory (golden/test/seeds)."),
    out: Path = typer.Option(Path("evaluation/zarabench/0.1/tasks.jsonl")),
    seed: int = typer.Option(7),
) -> None:
    """Generate the ZaraBench task set from held-out dataset splits and seeds, then prove every reference answer passes."""
    from protea.evaluation.authoring import AuthoringSpec, author_tasks
    from protea.evaluation.tasks import task_stats, write_tasks

    tasks = author_tasks(build_dir, AuthoringSpec(seed=seed))
    failures = _reference_failures(tasks)
    if failures:
        _fail("reference answers that do not pass their own checks:\n  " + "\n  ".join(failures[:20]))
    write_tasks(tasks, out)
    st = task_stats(tasks)
    typer.echo(f"wrote {st['total']} tasks to {out}: " + ", ".join(f"{k}={v}" for k, v in st["by_category"].items()))


def _reference_failures(tasks) -> list[str]:
    from protea.config.models import CategoryWeight, EvaluationConfig
    from protea.evaluation.reference import ReferenceProvider
    from protea.evaluation.runner import run_benchmark
    from protea.evaluation.tasks import Category

    cfg = EvaluationConfig(
        suite="check",
        version="0",
        concurrency=16,
        categories=[CategoryWeight(name=c.value, weight=0.1) for c in Category],
    )
    report = asyncio.run(run_benchmark(cfg, tasks, ReferenceProvider(tasks)))
    return [f"{r.task_id}: {', '.join(r.failure_modes)}" for r in report.results if not r.passed]


@evaluate_app.command("seal")
def evaluate_seal(
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True), root: Path = typer.Option(Path("."))
) -> None:
    """Hash-pin the task set and record its families as held out (spec §25). Re-sealing is a reviewed decision."""
    from protea.config import load_config
    from protea.evaluation.golden import seal

    cfg = load_config(config, "evaluation")
    if not (cfg.tasks_path and cfg.lock_path):
        _fail("evaluation config needs tasks_path and lock_path")
    lock = seal(cfg.suite, cfg.version, root / cfg.tasks_path, root / cfg.lock_path, repo_root=root)
    typer.echo(
        f"sealed {lock.count} tasks, sha256 {lock.sha256[:12]}, {len(lock.held_out_families)} families -> {cfg.lock_path}"
    )


@evaluate_app.command("verify")
def evaluate_verify(
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
    min_tasks: int = typer.Option(150, help="Roadmap Phase 3 requires at least 150 golden tasks."),
) -> None:
    """Fail if the sealed task set drifted, shrank below the minimum, or lost a category (runs in CI)."""
    from protea.config import load_config
    from protea.evaluation.golden import verify

    cfg = load_config(config, "evaluation")
    if not cfg.lock_path:
        _fail("evaluation config has no lock_path")
    problems = verify(root / cfg.lock_path, repo_root=root, min_tasks=min_tasks)
    if problems:
        _fail("golden set: " + "; ".join(problems))
    typer.echo("ok: sealed task set verified")


def _paid_gate(cfg, tasks, provider: str, model: str | None, judge_provider: str | None, confirm: bool) -> None:
    """Print the §52 pre-flight and stop unless --confirm was given, for any provider that spends tokens."""
    from protea.evaluation.runner import preflight

    paid = provider not in FREE_PROVIDERS or (judge_provider is not None and judge_provider not in FREE_PROVIDERS)
    if not paid:
        return
    typer.echo("execution boundary — this run spends tokens on a third-party service:")
    for k, v in preflight(cfg, tasks, model or provider).items():
        typer.echo(f"  {k:<24} {v}")
    if not confirm:
        _fail("re-run with --confirm to proceed")


def progress_printer(quiet: bool = False):
    """Build an ``on_result`` callback that prints one progress line per finished task to stderr."""
    from time import monotonic

    from protea.evaluation.runner import progress_line

    if quiet:
        return None
    started = monotonic()

    def _print(done: int, total: int, result) -> None:
        typer.echo(progress_line(done, total, result, started=started), err=True)

    return _print


def _print_summary(report, json_path: Path, md_path: Path) -> None:
    typer.echo(
        f"ZaraScore {report.zarascore:.4f} (strict {report.zarascore_strict:.4f})"
        f"{' (partial)' if report.partial else ''}  tasks {report.tasks_run}"
    )
    for c in report.categories:
        typer.echo(
            f"  {c.name:<22} n={c.n:<4} score={c.score:.3f} pass={c.pass_rate:.3f} judge_skipped={c.judge_skipped}"
        )
    if report.failed_gates:
        typer.secho(f"failed gates: {report.failed_gates}", fg=typer.colors.YELLOW)
    typer.echo(f"reports: {json_path}  {md_path}")


@evaluate_app.command("run")
def evaluate_run(
    provider: str = typer.Option("mock", help="mock | reference | any name from `protea providers list`."),
    model: str | None = typer.Option(None),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
    out: Path = typer.Option(Path("evaluation/reports")),
    categories: str | None = typer.Option(None, help="Comma-separated category filter."),
    language: str | None = typer.Option(None),
    limit: int | None = typer.Option(None),
    judge_provider: str | None = typer.Option(None, help="Overrides the config's judge provider."),
    judge_model: str | None = typer.Option(None),
    confirm: bool = typer.Option(False, "--confirm", help="Required for any provider that spends tokens."),
    label: str | None = typer.Option(None, help="Run id; defaults to a UTC timestamp."),
    max_tokens: int | None = typer.Option(None, help="Override the config's max_tokens (recorded in the config hash)."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the per-task progress lines on stderr."),
) -> None:
    """Run the suite against a provider and write JSON + Markdown reports. Paid providers need --confirm."""
    from protea.config import config_hash
    from protea.evaluation.judge import check_independence
    from protea.evaluation.report import write_report
    from protea.evaluation.runner import run_benchmark

    cfg, cfg_hash, tasks, digest = _load(config, root)
    if max_tokens is not None:
        cfg = cfg.model_copy(update={"max_tokens": max_tokens})
        cfg_hash = config_hash(cfg)
    tasks = _select(tasks, categories, limit, language)
    if not tasks:
        _fail("no tasks selected")
    jp = judge_provider or cfg.judge_provider
    _paid_gate(cfg, tasks, provider, model, jp, confirm)
    prov = _build(provider, model, tasks)
    judge = _build(jp, judge_model or cfg.judge_model, tasks) if jp else None
    if judge is not None:
        problems = check_independence(judge, prov, _generator_models(root), cfg.judge_must_differ_from_generator)
        if problems:
            _fail("; ".join(problems))
    report = asyncio.run(
        run_benchmark(
            cfg,
            tasks,
            prov,
            judge=judge,
            run_id=label,
            config_hash=cfg_hash,
            task_set_hash=digest,
            on_result=progress_printer(quiet),
        )
    )
    json_path, md_path = write_report(report, out, cfg)
    _print_summary(report, json_path, md_path)


@evaluate_app.command("compare")
def evaluate_compare(
    candidate: Path = typer.Argument(..., exists=True, help="Candidate report JSON."),
    base: Path | None = typer.Option(None, exists=True, help="Unmodified base-model report."),
    frontier: Path | None = typer.Option(None, exists=True, help="Frontier-model report."),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Side-by-side category table, the release-gate decision and the kill criterion (roadmap §4, strategy A1)."""
    from protea.config import load_config
    from protea.evaluation.report import load_report, release_decision, render_comparison

    cfg = load_config(config, "evaluation")
    reports = {"candidate": load_report(candidate)}
    if base:
        reports["base"] = load_report(base)
    if frontier:
        reports["frontier"] = load_report(frontier)
    decision = release_decision(cfg, reports["candidate"], reports.get("base"), reports.get("frontier"))
    if as_json:
        typer.echo(
            json.dumps(
                {"decision": decision.model_dump(), "zarascore": {k: r.zarascore for k, r in reports.items()}}, indent=2
            )
        )
    else:
        typer.echo(render_comparison(cfg, reports, decision))
    if not decision.release:
        raise typer.Exit(2)
