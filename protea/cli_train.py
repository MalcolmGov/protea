"""`protea train` — local runs, remote job plans, model cards and registry entries (Phase 4)."""

from __future__ import annotations

from pathlib import Path

import typer

train_app = typer.Typer(
    help="Training: local runs, remote GPU jobs (dry-run first), model cards, registration.", no_args_is_help=True
)

GPU_CATALOGUE = Path("configs/pricing/gpu.yaml")


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


def _load_training(config: Path):
    from protea.config import config_hash, load_config

    cfg = load_config(config, "training")
    return cfg, config_hash(cfg)


def _records(cfg, root: Path):
    from protea.training.data import GoldenLeakError, load_records

    try:
        train, train_stats = load_records(root / cfg.dataset.train, max_examples=cfg.dataset.max_examples)
        val, val_stats = load_records(root / cfg.dataset.validation, max_examples=cfg.dataset.max_examples)
    except GoldenLeakError as exc:
        _fail(str(exc))
        raise
    return train, train_stats, val, val_stats


def _registry_entries(root: Path):
    from protea.config import get_settings
    from protea.registry import DatasetRegistry

    path = Path(get_settings().registry_dir) / "datasets.json"
    if not path.is_absolute():
        path = root / path
    return DatasetRegistry(path).list() if path.exists() else []


def _pin_or_fail(cfg, train_stats, root: Path, allow_unregistered: bool) -> str | None:
    from protea.training.data import check_dataset_pin

    entries = _registry_entries(root)
    problems = check_dataset_pin(cfg.dataset.dataset_key, train_stats, entries)
    if problems and not allow_unregistered:
        _fail("; ".join(problems))
    if problems:
        typer.secho("warning: " + "; ".join(problems), fg=typer.colors.YELLOW)
        return None
    return next(e.sha256 for e in entries if e.key == cfg.dataset.dataset_key)


def _echo_stats(label: str, st) -> None:
    typer.echo(
        f"{label:<12} {st.examples} examples, ~{st.approx_tokens:,} tokens, sha256 {st.sha256[:12]}, tasks {st.by_task_type}"
    )


@train_app.command("local")
def train_local(
    config: Path = typer.Option(..., exists=True, help="Training config YAML."),
    root: Path = typer.Option(Path("."), help="Repository root; dataset and output paths are relative to it."),
    run_id: str | None = typer.Option(None, help="Run id (defaults to a UTC timestamp)."),
    resume: bool = typer.Option(
        False, "--resume", help="Resume the latest run of this experiment from its last checkpoint."
    ),
    max_steps: int | None = typer.Option(None, help="Override training.max_steps (smoke runs)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Load and check the dataset, create nothing, train nothing."),
    allow_unregistered: bool = typer.Option(False, help="Proceed when the dataset key is not in the registry."),
) -> None:
    """Fine-tune on this machine. The config is frozen into the run directory; resuming with a changed config is refused."""
    from protea.config import get_settings
    from protea.training.card import model_card
    from protea.training.run import ImmutableConfigError, create_run, find_latest_run, open_run
    from protea.training.trainer import TrainingUnavailable, TrainOptions, adapter_sha256, train

    cfg, cfg_hash = _load_training(config)
    train_recs, train_stats, val_recs, val_stats = _records(cfg, root)
    _pin_or_fail(cfg, train_stats, root, allow_unregistered)
    _echo_stats("train", train_stats)
    _echo_stats("validation", val_stats)
    typer.echo(f"base model   {cfg.model.base_model}  method {cfg.training.method}  config {cfg_hash[:12]}")
    if dry_run:
        typer.echo("dry run: nothing created")
        return
    try:
        if resume:
            latest = find_latest_run(cfg, root)
            if latest is None:
                _fail("nothing to resume: no run directory for this experiment")
            run, manifest = open_run(latest, cfg_hash)  # type: ignore[arg-type]
        else:
            run, manifest = create_run(cfg, cfg_hash, root, run_id=run_id)
    except (ImmutableConfigError, FileExistsError) as exc:
        _fail(str(exc))
        return
    manifest.train, manifest.validation = train_stats, val_stats
    run.save(manifest)
    typer.echo(f"run          {run.path}")
    try:
        manifest = train(
            cfg,
            run,
            manifest,
            train_recs,
            val_recs,
            TrainOptions(resume=resume, max_steps=max_steps, mlflow_uri=get_settings().mlflow_tracking_uri),
        )
    except TrainingUnavailable as exc:
        _fail(str(exc))
        return
    run.card_path.write_text(model_card(manifest, cfg), encoding="utf-8")
    sha = adapter_sha256(run.adapter)
    typer.echo(
        f"{manifest.status}: {manifest.steps} steps in {manifest.duration_s:.0f}s; "
        + ", ".join(f"{k}={v:.4f}" for k, v in sorted(manifest.final_metrics.items()) if k.endswith("loss"))
    )
    typer.echo(f"adapter      {run.adapter} ({(sha or 'no weights file')[:12]})\ncard         {run.card_path}")


@train_app.command("remote")
def train_remote(
    config: Path = typer.Option(..., exists=True, help="Training config YAML."),
    remote: Path = typer.Option(..., exists=True, help="Remote job config (configs/remote/*.yaml)."),
    root: Path = typer.Option(Path(".")),
    out: Path = typer.Option(Path("runs/remote"), help="Where the generated job artefacts are written."),
    confirm: bool = typer.Option(False, "--confirm", help="Actually submit the job. Without it this is a dry run."),
    allow_unregistered: bool = typer.Option(False),
    run_id: str | None = typer.Option(None),
) -> None:
    """Plan a GPU job: provider, GPU, duration, cost, dataset, base model, safeguards, generated artefacts. Launch only with --confirm."""
    from protea.config import get_settings, load_config
    from protea.training.remote import PlanRequest, build_adapter, estimate, load_gpu_catalogue, write_artifacts
    from protea.training.run import new_run_id

    cfg, cfg_hash = _load_training(config)
    remote_cfg = load_config(remote, "remote")
    catalogue = load_gpu_catalogue(root / GPU_CATALOGUE)
    if remote_cfg.gpu not in catalogue:
        _fail(f"unknown gpu {remote_cfg.gpu!r}; known: {sorted(catalogue)}")
    _, train_stats, _, _ = _records(cfg, root)
    dataset_sha = _pin_or_fail(cfg, train_stats, root, allow_unregistered)
    est = estimate(cfg, remote_cfg, catalogue[remote_cfg.gpu], train_stats.approx_tokens)
    adapter = build_adapter(remote_cfg, get_settings())
    plan = adapter.plan(
        PlanRequest(
            cfg=cfg,
            cfg_path=str(config),
            cfg_hash=cfg_hash,
            estimate=est,
            run_id=run_id or new_run_id(),
            dataset_sha256=dataset_sha,
        )
    )
    artefact_dir = out / f"{cfg.output.experiment_name}-{remote_cfg.provider}"
    written = write_artifacts(plan, artefact_dir)
    typer.echo("execution boundary — this job rents GPU time:")
    for line in plan.summary_lines():
        typer.echo(f"  {line}")
    typer.echo("artefacts:   " + ", ".join(str(p) for p in written))
    if not confirm:
        typer.echo("dry run: nothing launched (re-run with --confirm after reviewing the artefacts)")
        return
    if not (est.within_budget and est.fits_vram):
        _fail("refusing to launch: the estimate exceeds the budget or the GPU cannot fit the model")
    handle = adapter.launch(plan, artefact_dir)
    typer.echo(f"launched     {handle}")


@train_app.command("card")
def train_card(run_dir: Path = typer.Argument(..., exists=True), config: Path | None = typer.Option(None)) -> None:
    """Regenerate the model card for a run from its frozen config and manifest."""
    from protea.config import load_config
    from protea.training.card import model_card
    from protea.training.run import RunDir

    run = RunDir(run_dir)
    cfg = load_config(config or run.config_path, "training")
    run.card_path.write_text(model_card(run.manifest(), cfg), encoding="utf-8")
    typer.echo(str(run.card_path))


@train_app.command("runs")
def train_runs(root: Path = typer.Option(Path(".")), output_dir: str = typer.Option("checkpoints")) -> None:
    """List runs under the output directory with status, steps and final loss."""
    from protea.training.run import RunDir

    base = root / output_dir
    found = sorted(base.rglob("manifest.json")) if base.exists() else []
    if not found:
        typer.echo("no runs")
        return
    for m in found:
        r = RunDir(m.parent).manifest()
        loss = r.final_metrics.get("eval_loss", r.final_metrics.get("train_loss"))
        typer.echo(
            f"{r.experiment_name}/{r.run_id:<18} {r.status:<10} steps={r.steps:<6} "
            f"loss={loss if loss is None else f'{loss:.4f}'}  base={r.base_model}  cfg={r.config_hash[:12]}"
        )


@train_app.command("register")
def train_register(
    run_dir: Path = typer.Argument(..., exists=True),
    version: str = typer.Option(..., help="Model version, e.g. 0.1.0"),
    root: Path = typer.Option(Path(".")),
) -> None:
    """Create an `experimental` model-registry entry for a completed run (promotion needs a ZaraBench report)."""
    from protea.config import get_settings
    from protea.registry import ModelRegistry, RegistryError
    from protea.schemas.registry import ModelEntry
    from protea.training.run import RunDir
    from protea.training.trainer import adapter_sha256

    run = RunDir(run_dir)
    m = run.manifest()
    if m.status != "completed":
        _fail(f"run status is {m.status}; only completed runs are registered")
    path = Path(get_settings().registry_dir) / "models.json"
    if not path.is_absolute():
        path = root / path
    entry = ModelEntry(
        family=m.model_family,
        version=version,
        base_model=m.base_model,
        training_dataset=m.dataset_key,
        training_method=m.method,
        checkpoint_uri=str(run.adapter),
        artifact_sha256=adapter_sha256(run.adapter),
        git_commit=m.git_commit,
        experiment_id=f"{m.experiment_name}/{m.run_id}",
        model_card_path=str(run.card_path) if run.card_path.exists() else None,
        metrics=dict(m.final_metrics),
    )
    try:
        ModelRegistry(path).add(entry)
    except RegistryError as exc:
        _fail(str(exc))
        return
    typer.echo(f"registered {entry.key} (experimental) in {path}")
