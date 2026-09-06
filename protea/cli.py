"""Protea command line. Only commands that do real work are registered; the rest are documented as planned."""
from __future__ import annotations

import json

import typer

from protea import __version__
from protea import doctor as _doctor

app = typer.Typer(help="Protea — Moove Digital's proprietary model platform.", no_args_is_help=True)

PLANNED = {
    "dataset build|validate|stats": "Phase 2 — data pipeline",
    "evaluate": "Phase 3 — evaluation framework and ZaraBench suite",
    "train local|remote --dry-run": "Phase 4 — training",
    "serve": "Phase 5 — inference",
    "benchmark": "Phase 3/6 — model comparison reports",
}


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
        typer.echo(f"planned  protea {cmd:<34} {phase}")
