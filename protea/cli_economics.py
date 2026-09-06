"""`protea economics` — the §69 economic model: frontier tokens vs self-hosted Protea at forecast volume."""

from __future__ import annotations

import json
from pathlib import Path

import typer

economics_app = typer.Typer(
    help="Economic model: monthly cost on each path, break-even, kill signal.", no_args_is_help=True
)

DEFAULT_CONFIG = Path("configs/economics/zara-v0.yaml")


@economics_app.command("report")
def economics_report(
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    out: Path | None = typer.Option(None, help="Write the Markdown report here as well."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Compute the model from the config's forecast, prices and overheads. Exit 2 on a kill signal."""
    from protea.config import load_config
    from protea.economics import as_dict, evaluate, render_markdown

    report = evaluate(load_config(config, "economics"))
    if as_json:
        typer.echo(json.dumps(as_dict(report), indent=2))
    else:
        typer.echo(render_markdown(report))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report), encoding="utf-8")
    if report.kill_signal:
        raise typer.Exit(2)
