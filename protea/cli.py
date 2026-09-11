"""Protea command line. Only commands that do real work are registered; the rest are listed by `roadmap`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from protea import __version__
from protea import doctor as _doctor
from protea.cli_economics import economics_app
from protea.cli_evaluate import evaluate_app
from protea.cli_release import release_app
from protea.cli_route import route_app
from protea.cli_security import security_app
from protea.cli_serve import serve_app
from protea.cli_train import train_app

app = typer.Typer(help="Protea — Moove Digital's proprietary model platform.", no_args_is_help=True)
config_app = typer.Typer(help="Validate and inspect YAML configuration.", no_args_is_help=True)
providers_app = typer.Typer(help="Model providers.", no_args_is_help=True)
dataset_app = typer.Typer(help="Datasets (JSONL in the ADR-003 format).", no_args_is_help=True)
registry_app = typer.Typer(help="Dataset and model registries.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(dataset_app, name="dataset")
app.add_typer(registry_app, name="registry")
app.add_typer(evaluate_app, name="evaluate")
app.add_typer(train_app, name="train")
app.add_typer(serve_app, name="serve")
app.add_typer(route_app, name="route")
app.add_typer(security_app, name="security")
app.add_typer(release_app, name="release")
app.add_typer(economics_app, name="economics")

PLANNED = {
    "evaluate run --provider <base model>": "Phase 3 — needs a GPU host or a hosted inference endpoint (execution boundary)",
    "train remote --confirm": "Phase 4 — launching the first QLoRA run rents a GPU (execution boundary)",
    "serve vllm --run": "Phase 5 — starting the engine needs a GPU host (execution boundary)",
}


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


@app.command()
def version() -> None:
    """Print the Protea package version."""
    typer.echo(__version__)


@app.command()
def doctor(as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON.")) -> None:
    """Report Python, GPU, CUDA, memory, disk and credential availability on this machine."""
    report = _doctor.run()
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return
    for c in report.checks:
        mark = "ok  " if c.ok else "warn"
        typer.echo(f"[{mark}] {c.name:<22} {c.detail}")


@app.command()
def roadmap() -> None:
    """List commands that are planned but not implemented yet."""
    for cmd, phase in PLANNED.items():
        typer.echo(f"planned  protea {cmd:<30} {phase}")


# ---- config -----------------------------------------------------------------------------------
@config_app.command("validate")
def config_validate(
    path: Path = typer.Argument(..., exists=True, help="A YAML file or a directory such as configs/."),
    kind: str | None = typer.Option(
        None,
        help=(
            "model | training | inference | evaluation | dataset | remote | pricing | serve | routing | release | "
            "economics (inferred from the parent directory)."
        ),
    ),
) -> None:
    """Validate configuration files against their schemas and print each content hash."""
    from protea.config import config_hash, kind_for_dir, load_config

    files = sorted(path.rglob("*.yaml")) if path.is_dir() else [path]
    failures = 0
    for f in files:
        k = kind or kind_for_dir(f.parent.name)
        if k not in (
            "model",
            "training",
            "inference",
            "evaluation",
            "dataset",
            "remote",
            "pricing",
            "serve",
            "routing",
        ):
            typer.echo(f"skip  {f} (unknown kind {k!r})")
            continue
        try:
            cfg = load_config(f, k)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — surface any validation problem
            failures += 1
            typer.secho(f"FAIL  {f}: {str(exc).splitlines()[0]}", fg=typer.colors.RED)
            continue
        typer.echo(f"ok    {f}  {k}  {config_hash(cfg)[:12]}")
    if failures:
        _fail(f"{failures} invalid config file(s)")


@config_app.command("show")
def config_show(path: Path = typer.Argument(..., exists=True), kind: str = typer.Option(...)) -> None:
    """Print the validated, normalised form of a config (all defaults filled in)."""
    from protea.config import config_hash, load_config

    cfg = load_config(path, kind)  # type: ignore[arg-type]
    typer.echo(json.dumps({"hash": config_hash(cfg), "config": cfg.model_dump(mode="json")}, indent=2))


# ---- providers ------------------------------------------------------------------------------
@providers_app.command("list")
def providers_list() -> None:
    """Show which providers are configured in this environment."""
    from protea.providers import provider_status

    for name, st in provider_status().items():
        mark = "ok  " if st["configured"] else "warn"
        typer.echo(f"[{mark}] {name:<18} model={st['model']:<24} needs={st['needs']}")


@providers_app.command("health")
def providers_health(
    name: str = typer.Argument(..., help="Provider name from `protea providers list`."),
    model: str | None = typer.Option(None, help="Override the configured model id."),
) -> None:
    """Send one tiny request to a provider. This spends real tokens on paid providers."""
    from protea.providers import ProviderNotConfigured, build_provider

    try:
        provider = build_provider(name, model=model)
    except ProviderNotConfigured as exc:
        _fail(str(exc))
        return
    health = asyncio.run(provider.health())
    typer.echo(health.model_dump_json(indent=2))
    if not health.ok:
        raise typer.Exit(1)


# ---- dataset ----------------------------------------------------------------------------------
@dataset_app.command("validate")
def dataset_validate(
    path: Path = typer.Argument(..., exists=True), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Validate a JSONL dataset line by line. Exit 1 on any invalid or duplicate example."""
    from protea.schemas.examples import validate_jsonl

    report = validate_jsonl(path)
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        typer.echo(f"{path}: {report.valid}/{report.total} valid, {report.duplicate_ids} duplicate ids")
        for err in report.errors[:20]:
            typer.secho(f"  line {err['line']}: {err['error']}", fg=typer.colors.RED)
    if not report.ok:
        raise typer.Exit(1)


@dataset_app.command("stats")
def dataset_stats(path: Path = typer.Argument(..., exists=True)) -> None:
    """Print the §66 statistics for a dataset file."""
    from protea.schemas.examples import validate_jsonl

    r = validate_jsonl(path)
    typer.echo(
        f"examples        {r.total} (valid {r.valid}, invalid {r.total - r.valid}, duplicate ids {r.duplicate_ids})"
    )
    typer.echo(f"synthetic       {r.synthetic}")
    typer.echo(f"approx tokens   {r.approx_tokens}")
    typer.echo(f"golden ids      {len(r.golden_ids)}")
    for label, counts in (
        ("task_type", r.by_task_type),
        ("split", r.by_split),
        ("language", r.by_language),
        ("source_type", r.by_source_type),
    ):
        typer.echo(f"{label:<15} " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


@dataset_app.command("author-citizen-train")
def dataset_author_citizen_train(
    out_dir: Path = typer.Option(
        Path("protea_data/citizenai/0.1"), help="Where the train/validation split files are written."
    ),
) -> None:
    """Generate the deterministic CitizenAI government-adapter training seed (build-spec Phase 2.1, en-ZA v0)."""
    from protea.citizenai.dataset import citizen_training_examples, families
    from protea.schemas.examples import Split

    examples = citizen_training_examples()
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for split, name in ((Split.TRAIN, "train.jsonl"), (Split.VALIDATION, "validation.jsonl")):
        rows = [e for e in examples if e.metadata.split == split]
        (out_dir / name).write_text("".join(e.model_dump_json() + "\n" for e in rows), encoding="utf-8")
        counts[split.value] = len(rows)
    typer.echo(
        f"wrote {len(examples)} examples to {out_dir}: "
        + ", ".join(f"{k}={v}" for k, v in counts.items())
        + " | families "
        + ", ".join(f"{k}={v}" for k, v in families(examples).items())
    )


@dataset_app.command("build")
def dataset_build(
    config: Path = typer.Option(
        Path("configs/datasets/agent-training-0.1.yaml"), exists=True, help="Dataset build config."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Discover, classify and count without writing anything."),
    root: Path = typer.Option(Path("."), help="Protea repository root (output and registry are relative to it)."),
) -> None:
    """Build a dataset from pinned local sources: discover → classify → scan → extract → normalise → dedup → split."""
    import yaml

    from protea.data_pipeline.build import build_dataset
    from protea.data_pipeline.sources import DatasetBuildConfig

    cfg = DatasetBuildConfig.model_validate(yaml.safe_load(config.read_text(encoding="utf-8")))
    report = build_dataset(cfg, root.resolve(), dry_run=dry_run)
    for line in report.summary_lines():
        typer.echo(line)
    missing = [s["name"] for s in report.sources if not s["exists"]]
    if missing:
        typer.secho(f"sources not found locally: {missing} (set PROTEA_SOURCE_<NAME>)", fg=typer.colors.YELLOW)
    if report.output_dir:
        typer.echo(f"written to     {report.output_dir}")
    if report.rejected:
        for r in report.rejected[:10]:
            typer.secho(f"  rejected {r['artifact']}: {r['reason']}", fg=typer.colors.YELLOW)


@dataset_app.command("golden-check")
def dataset_golden_check(
    train: Path = typer.Argument(..., exists=True), golden: Path = typer.Argument(..., exists=True)
) -> None:
    """Fail if any golden example id or family appears in a training file (spec §25)."""
    from protea.data_pipeline.splits import golden_leak
    from protea.schemas.examples import TrainingExample, iter_examples

    def load(p: Path) -> tuple[set[str], set[str]]:
        ids, fams = set(), set()
        for _, line in iter_examples(p):
            ex = TrainingExample.model_validate_json(line)
            ids.add(ex.metadata.id)
            if ex.metadata.family:
                fams.add(f"{ex.metadata.task_type.value}:{ex.metadata.family}")
        return ids, fams

    t_ids, t_fams = load(train)
    g_ids, g_fams = load(golden)
    problems = golden_leak(t_ids, g_ids, t_fams, g_fams)
    if problems:
        _fail("golden leak: " + "; ".join(problems))
    typer.echo(f"ok: {len(g_ids)} golden examples, none present in {train}")


@dataset_app.command("synthesize")
def dataset_synthesize(
    seeds: Path = typer.Argument(..., exists=True, help="tool_calling_seeds.jsonl from `dataset build`"),
    provider: str = typer.Option("mock", help="Provider name; anything other than mock spends tokens."),
    model: str | None = typer.Option(None),
    limit: int = typer.Option(20, help="Maximum seeds to process."),
    out: Path = typer.Option(Path("synthetic_tool_calling.jsonl")),
    confirm: bool = typer.Option(False, "--confirm", help="Required for non-mock providers."),
    include_contaminated: bool = typer.Option(False, help="Also process seeds flagged by the contamination check."),
    target_tool: list[str] = typer.Option(
        [], "--target-tool", help="Keep only seeds whose expect.tool is one of these (repeatable). Targets under-covered tools."
    ),
    scenario: list[str] = typer.Option(
        [], "--scenario", help="Keep only seeds whose seed_id contains one of these substrings, e.g. write-after-confirm (repeatable)."
    ),
    balance: bool = typer.Option(
        False, "--balance/--no-balance", help="Fill the limit round-robin across target tools instead of file order, so no tool dominates."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the selection breakdown (by tool and scenario) and exit. Builds no provider, spends nothing."
    ),
    golden_lock: Path = typer.Option(
        Path("evaluation/zarabench/0.1/golden.lock"), help="Seeds from families sealed in this lock are skipped."
    ),
) -> None:
    """Complete eval-seeded tool-calling turns with a teacher model and keep only completions that satisfy the eval expectations."""
    from protea.data_pipeline.normalize.packages import ToolCallingSeed
    from protea.data_pipeline.synthetic import select_seeds, selection_summary, synthesize
    from protea.evaluation.golden import held_out_families
    from protea.providers import ProviderNotConfigured, build_provider
    from protea.schemas.examples import iter_examples

    held_out = held_out_families(golden_lock)
    all_seeds = (ToolCallingSeed.model_validate_json(line) for _, line in iter_examples(seeds))
    items, skipped_held_out = select_seeds(
        all_seeds,
        limit=limit,
        held_out=held_out,
        include_contaminated=include_contaminated,
        target_tools=frozenset(target_tool) or None,
        scenarios=tuple(scenario),
        balance=balance,
    )

    # A dry run is the spend-free preview: show exactly what would be synthesized, build no provider, call nothing.
    if dry_run:
        summary = selection_summary(items)
        typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
        if skipped_held_out:
            typer.echo(f"skipped {skipped_held_out} seed(s) from families held out by {golden_lock}")
        return

    if provider != "mock" and not confirm:
        _fail(
            "Refusing to call a paid provider without --confirm. Note: outputs of Anthropic/OpenAI/Google models are subject to "
            "terms that restrict training competing models (strategy-review C3); prefer an open-weight teacher and record the policy decision."
        )
    try:
        prov = build_provider(provider, model=model)
    except ProviderNotConfigured as exc:
        _fail(str(exc))
        return

    # Process seeds one at a time, WRITING each accepted completion to disk immediately and flushing. An
    # unattended GPU run can be SIGKILLed (OOM or a community-GPU host reclaim) with no traceback; writing
    # incrementally means a death at seed 300 keeps 300 completions instead of losing everything, and the
    # progress line every 20 seeds shows how far it got (a consistent stall point points at OOM; a random one
    # at a reclaim). The pod's entrypoint syncs this file to storage periodically, so partial work survives.
    # Synchronous loop (file I/O stays out of any async function): drive each seed's async synthesis with
    # asyncio.run one at a time — the per-seed loop overhead is nothing next to a multi-second GPU generation,
    # and the provider keeps the model loaded across calls.
    accepted = 0
    failures: list[tuple[str, list[str]]] = []
    with out.open("w", encoding="utf-8") as fh:
        for i, s in enumerate(items, 1):
            r = asyncio.run(synthesize(s, prov, dataset_version="0.1.0-synthetic"))
            if r.ok and r.example is not None:
                fh.write(r.example.model_dump_json() + "\n")
                fh.flush()
                accepted += 1
            else:
                failures.append((r.seed_id, r.problems))
            if i % 20 == 0 or i == len(items):
                typer.echo(f"synth progress: {i}/{len(items)} processed, {accepted} accepted", err=True)
    typer.echo(f"seeds {len(items)}  accepted {accepted}  rejected {len(failures)}  -> {out}")
    if skipped_held_out:
        typer.echo(f"skipped {skipped_held_out} seed(s) from families held out by {golden_lock}")
    for seed_id, problems in failures[:10]:
        typer.secho(f"  {seed_id}: {'; '.join(problems)}", fg=typer.colors.YELLOW)


def _held_out_families(lock: Path) -> frozenset[str]:
    """Read the sealed families straight from the lock JSON — no eval stack needed just to review data."""
    if not lock.exists():
        return frozenset()
    return frozenset(json.loads(lock.read_text(encoding="utf-8")).get("held_out_families", []))


@dataset_app.command("review")
def dataset_review(
    inputs: list[Path] = typer.Argument(..., exists=True, help="Synthesised JSONL file(s) to review."),
    reference: Path | None = typer.Option(
        None, exists=True, help="An existing training split (e.g. train.jsonl) to cross-dedup against."
    ),
    golden_lock: Path = typer.Option(
        Path("evaluation/zarabench/0.1/golden.lock"), help="Sealed families here are eval leakage → blocked."
    ),
    out: Path | None = typer.Option(
        None, help="Write an annotated copy: review_status set (approved=clean, rejected=blocked, else pending)."
    ),
    approve_clean: bool = typer.Option(
        False, help="With --out, mark clean rows 'approved'. Off by default — a human still signs the lane off."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON instead of a table."),
) -> None:
    """Review kept synthetic rows before they train: dedup, PII/secret scan, quality heuristics, distribution.

    Correctness is already gated at synthesis (the seed's `expect`). This is the softer gate `dataset build`
    would apply — it never trains or approves on its own; it sorts rows into blocked / flagged / clean so the
    human review lane knows where to look.
    """
    from protea.data_pipeline.review import (
        LANE_BLOCKED,
        LANE_CLEAN,
        LANE_FLAGGED,
        RowReview,
        reference_shingles,
        review_examples,
    )
    from protea.schemas.examples import ReviewStatus, TrainingExample, iter_examples

    examples: list[TrainingExample] = []
    parse_errors: list[dict] = []
    for path in inputs:
        for lineno, line in iter_examples(path):
            try:
                examples.append(TrainingExample.model_validate_json(line))
            except (ValueError, TypeError) as exc:
                parse_errors.append({"file": str(path), "line": lineno, "error": str(exc).splitlines()[0][:200]})

    ref = None
    if reference is not None:
        ref_examples = [
            TrainingExample.model_validate_json(line) for _, line in iter_examples(reference)
        ]
        ref = reference_shingles(ref_examples)

    report = review_examples(examples, reference_targets=ref, held_out_families=_held_out_families(golden_lock))
    report.total = len(examples) + len(parse_errors)
    report.valid = len(examples)
    report.invalid = len(parse_errors)
    report.errors = parse_errors[:50]

    if out is not None:
        lane_status = {LANE_CLEAN: ReviewStatus.APPROVED if approve_clean else ReviewStatus.PENDING,
                       LANE_FLAGGED: ReviewStatus.PENDING, LANE_BLOCKED: ReviewStatus.REJECTED}
        by_id: dict[str, RowReview] = {r.id: r for r in report.rows}
        with out.open("w", encoding="utf-8") as fh:
            for ex in examples:
                rv = by_id.get(ex.metadata.id)
                if rv is not None:
                    ex.metadata.review_status = lane_status[rv.lane]
                fh.write(ex.model_dump_json() + "\n")

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print_review(report)

    if not report.ok:
        _fail(f"review found {report.blocked} blocked row(s) and {report.invalid} invalid row(s)")


def _print_review(report) -> None:
    typer.echo(f"reviewed {report.valid} row(s)" + (f" ({report.invalid} invalid)" if report.invalid else ""))
    typer.secho(f"  clean   {report.clean}", fg=typer.colors.GREEN)
    typer.secho(f"  flagged {report.flagged}", fg=typer.colors.YELLOW)
    typer.secho(f"  blocked {report.blocked}", fg=typer.colors.RED if report.blocked else None)
    typer.echo("blocking:")
    typer.echo(f"  secrets {report.secret_rows} · golden-lock {report.golden_lock_violations} · "
               f"bad provenance {report.invariant_violations}")
    typer.echo("flags:")
    typer.echo(f"  pii {report.pii_rows}{' ' + str(report.pii_by_kind) if report.pii_by_kind else ''} · "
               f"dup-in-batch {report.duplicates_within} · dup-vs-train {report.duplicates_vs_reference} · "
               f"degenerate {report.degenerate} · refusal-shaped {report.refusal_shaped}")
    top = report.top_families[:8]
    if top:
        typer.echo("top families: " + ", ".join(f"{f}×{n}" for f, n in top))
    typer.echo(f"languages: {report.by_language}")
    from protea.data_pipeline.review import LANE_BLOCKED, LANE_CLEAN

    for r in [r for r in report.rows if r.lane != LANE_CLEAN][:15]:
        color = typer.colors.RED if r.lane == LANE_BLOCKED else typer.colors.YELLOW
        typer.secho(f"  [{r.lane}] {r.family or '?'} {r.id[:8]}: {'; '.join(r.reasons)}", fg=color)


@dataset_app.command("blend")
def dataset_blend(
    mined: Path = typer.Argument(..., exists=True, help="A mined build's train split (train.jsonl)."),
    synthetic: list[Path] = typer.Argument(..., exists=True, help="Reviewed synthetic JSONL file(s) to blend in."),
    out: Path = typer.Option(..., help="Where to write the merged train split."),
    approved_only: bool = typer.Option(
        False, help="Take only rows already stamped review_status=approved; default keeps non-rejected rows."
    ),
    pii_mode: str = typer.Option("synthetic", help="PII scrub mode for synthetic rows: synthetic | placeholder."),
) -> None:
    """Merge reviewed synthetic rows into a mined train split: drop rejected, scrub PII, dedup, mark approved.

    Only the train split is touched — validation/test/golden stay the mined build's, so the eval hold-out is
    never diluted with synthetic data. Never trains; just assembles the file the training run points at.
    """
    from protea.data_pipeline.blend import blend
    from protea.schemas.examples import TrainingExample, iter_examples

    mined_rows = [TrainingExample.model_validate_json(line) for _, line in iter_examples(mined)]
    synth_rows: list[TrainingExample] = []
    for path in synthetic:
        synth_rows.extend(TrainingExample.model_validate_json(line) for _, line in iter_examples(path))

    merged, report = blend(mined_rows, synth_rows, keep_flagged=not approved_only, pii_mode=pii_mode)

    with out.open("w", encoding="utf-8") as fh:
        for ex in merged:
            fh.write(ex.model_dump_json() + "\n")

    typer.echo(f"mined {report.mined_rows} + synthetic {report.synthetic_in} "
               f"(dropped: rejected {report.dropped_rejected}, unapproved {report.dropped_unapproved}, "
               f"duplicate {report.dropped_duplicate})")
    if report.pii_redactions:
        typer.echo(f"pii scrubbed on {report.pii_scrubbed_rows} row(s): {report.pii_redactions}")
    typer.secho(f"merged {report.merged_total} rows ({report.synthetic_kept} synthetic) -> {out}",
                fg=typer.colors.GREEN)
    if not report.ok:
        _fail("blend accounting mismatch")


# ---- registry -----------------------------------------------------------------------------------
def _registries():
    from protea.config import get_settings
    from protea.registry import DatasetRegistry, ModelRegistry

    root = Path(get_settings().registry_dir)
    return DatasetRegistry(root / "datasets.json"), ModelRegistry(root / "models.json")


@registry_app.command("datasets")
def registry_datasets() -> None:
    """List registered datasets."""
    ds, _ = _registries()
    entries = ds.list()
    if not entries:
        typer.echo("no datasets registered")
    for e in entries:
        typer.echo(f"{e.key:<32} {e.status.value:<10} splits={e.splits} golden={e.golden} sha256={e.sha256[:12]}")


@registry_app.command("models")
def registry_models() -> None:
    """List registered models."""
    _, ms = _registries()
    entries = ms.list()
    if not entries:
        typer.echo("no models registered")
    for e in entries:
        typer.echo(f"{e.key:<32} {e.deployment_status.value:<12} base={e.base_model} dataset={e.training_dataset}")


@registry_app.command("promote")
def registry_promote(
    key: str = typer.Argument(..., help="Model key, e.g. protea-agent-0.1.0"),
    to: str = typer.Option(..., help="experimental | candidate | staging | production | deprecated | rejected"),
    reason: str = typer.Option("", help="Recorded in the entry's history."),
) -> None:
    """Move a model through the release lifecycle (validated transitions only)."""
    from protea.registry import RegistryError
    from protea.schemas.registry import ModelStatus

    _, ms = _registries()
    try:
        entry = ms.transition(key, ModelStatus(to), reason=reason)
    except (RegistryError, ValueError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"{entry.key} -> {entry.deployment_status.value}")


if __name__ == "__main__":
    app()
