"""Protea command line. Only commands that do real work are registered; the rest are listed by `roadmap`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from protea import __version__
from protea import doctor as _doctor

app = typer.Typer(help="Protea — Moove Digital's proprietary model platform.", no_args_is_help=True)
config_app = typer.Typer(help="Validate and inspect YAML configuration.", no_args_is_help=True)
providers_app = typer.Typer(help="Model providers.", no_args_is_help=True)
dataset_app = typer.Typer(help="Datasets (JSONL in the ADR-003 format).", no_args_is_help=True)
registry_app = typer.Typer(help="Dataset and model registries.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(dataset_app, name="dataset")
app.add_typer(registry_app, name="registry")

PLANNED = {
    "dataset build": "Phase 2 — extractors, classifiers, scanners, dedup, exporters",
    "evaluate / benchmark": "Phase 3 — evaluation framework and ZaraBench suite",
    "train local|remote --dry-run": "Phase 4 — training and remote GPU adapters",
    "serve": "Phase 5 — vLLM container and facade",
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
        None, help="model | training | inference | evaluation (inferred from the parent directory)."
    ),
) -> None:
    """Validate configuration files against their schemas and print each content hash."""
    from protea.config import config_hash, load_config

    files = sorted(path.rglob("*.yaml")) if path.is_dir() else [path]
    failures = 0
    for f in files:
        k = kind or f.parent.name.rstrip("s")
        if k not in ("model", "training", "inference", "evaluation"):
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


@dataset_app.command("build")
def dataset_build() -> None:
    """Planned (Phase 2). Not implemented — this command does nothing yet."""
    _fail("dataset build is planned for Phase 2 (extractors, classifiers, scanners, dedup). Nothing built.", 2)


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
