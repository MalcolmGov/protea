"""`protea release` — gated promotion, canary steps, rollback (Phase 10, ADR-011)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

release_app = typer.Typer(help="Release pipeline: check evidence, promote, canary, rollback.", no_args_is_help=True)

DEFAULT_CONFIG = Path("configs/release/zara-v0.yaml")


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


def _pipeline(config: Path, root: Path):
    from protea.config import load_config
    from protea.release import ReleasePipeline

    return ReleasePipeline(load_config(config, "release"), root)


def _evidence(zarabench, security, base, frontier):
    from protea.release import Evidence

    return Evidence(zarabench=zarabench, security=security, base=base, frontier=frontier)


def _print(report) -> None:
    typer.echo(f"model        {report.model}  status {report.status}  canary {report.canary_percent:g}%")
    for c in report.checks:
        mark = "ok  " if c.ok else "fail"
        typer.echo(f"  [{mark}] {c.stage:<20} {c.detail}")
    typer.echo(f"next         {report.next_step or '- (blocked)'}")


@release_app.command("check")
def release_check(
    model: str = typer.Argument(..., help="Model key, e.g. protea-agent-0.1.0"),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
    zarabench: Path | None = typer.Option(None, exists=True, help="Explicit ZaraBench report for the model."),
    security: Path | None = typer.Option(None, exists=True, help="Explicit security report for the model."),
    base: Path | None = typer.Option(None, exists=True),
    frontier: Path | None = typer.Option(None, exists=True),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show every release stage with its evidence and the promotion the evidence supports. Exit 1 when blocked."""
    from protea.registry import RegistryError

    try:
        report = _pipeline(config, root).check(model, _evidence(zarabench, security, base, frontier))
    except RegistryError as exc:
        _fail(str(exc))
        return
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _print(report)
    if report.blockers:
        raise typer.Exit(1)


@release_app.command("promote")
def release_promote(
    model: str = typer.Argument(...),
    to: str = typer.Option(..., help="candidate | staging | canary | production"),
    reason: str = typer.Option("", help="Recorded in the registry history and the release log."),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
    zarabench: Path | None = typer.Option(None, exists=True),
    security: Path | None = typer.Option(None, exists=True),
    base: Path | None = typer.Option(None, exists=True),
    frontier: Path | None = typer.Option(None, exists=True),
) -> None:
    """Move the model one step (registry status or first canary share) — only when every earlier stage passes."""
    from protea.registry import RegistryError

    if to not in ("candidate", "staging", "canary", "production"):
        _fail("--to must be candidate | staging | canary | production")
    try:
        report = _pipeline(config, root).promote(
            model, to, reason=reason, evidence=_evidence(zarabench, security, base, frontier)
        )
    except RegistryError as exc:
        _fail(str(exc))
        return
    _print(report)


@release_app.command("canary")
def release_canary(
    step: bool = typer.Option(False, "--step", help="Raise the canary to the next configured step."),
    set_percent: float | None = typer.Option(None, "--set", help="Set an explicit tenant share (0–100)."),
    reason: str = typer.Option(""),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
) -> None:
    """Show or change the canary tenant share on the routing policy."""
    from protea.registry import RegistryError

    pipe = _pipeline(config, root)
    try:
        if step:
            pct = pipe.canary_step(reason=reason)
        elif set_percent is not None:
            if not 0 <= set_percent <= 100:
                _fail("--set must be between 0 and 100")
            pipe.set_canary(set_percent, reason=reason or "manual")
            pct = set_percent
        else:
            pct = pipe.canary_percent()
    except RegistryError as exc:
        _fail(str(exc))
        return
    typer.echo(
        f"canary {pipe.cfg.canary.route} = {pct:g}% (steps {pipe.cfg.canary.steps}, observe {pipe.cfg.canary.observe_minutes} min per step)"
    )


@release_app.command("rollback")
def release_rollback(
    reason: str = typer.Option(..., help="Why (recorded in the release log and registry history)."),
    model: str | None = typer.Option(
        None, help="Model to take out of production (default: the family's production model)."
    ),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
) -> None:
    """Canary to zero, the model out of production, the previous production model reinstated when there is one."""
    from protea.registry import RegistryError

    try:
        out = _pipeline(config, root).rollback(reason=reason, model_key=model)
    except RegistryError as exc:
        _fail(str(exc))
        return
    typer.echo(f"rolled back: canary 0%  demoted {out['demoted'] or '-'}  reinstated {out['reinstated'] or '-'}")


@release_app.command("watch")
def release_watch(
    metrics: Path = typer.Argument(
        ..., exists=True, help="Observability summary JSON (aria /v1/observability/summary)."
    ),
    route: str | None = typer.Option(None, help="Bucket under by_route to evaluate (default: the canary route)."),
    config: Path = typer.Option(DEFAULT_CONFIG, exists=True),
    root: Path = typer.Option(Path(".")),
    rollback: bool = typer.Option(False, "--rollback", help="Roll back automatically when a trigger breaches."),
) -> None:
    """Evaluate the rollback triggers against an observability window; exit 1 on a breach."""
    from protea.release import should_rollback

    pipe = _pipeline(config, root)
    data = json.loads(metrics.read_text(encoding="utf-8"))
    name = route or pipe.cfg.canary.route
    bucket = next((b for b in data.get("by_route", []) if b.get("key") == name), None)
    if bucket is None:
        typer.echo(f"no bucket for route {name!r} in {metrics}; nothing to evaluate")
        return
    negative = None
    for row in (data.get("feedback") or {}).get("by_model", []):
        if name in str(row.get("model_version", "")):
            negative = row.get("negative_share")
    reasons = should_rollback(bucket, pipe.cfg.rollback, feedback_negative_share=negative)
    if not reasons:
        typer.echo(f"{name}: {bucket.get('calls')} calls, no rollback trigger breached")
        return
    typer.echo(f"{name}: rollback triggers breached: " + "; ".join(reasons))
    if rollback:
        out = pipe.rollback(reason="; ".join(reasons))
        typer.echo(f"rolled back: canary 0%  demoted {out['demoted'] or '-'}  reinstated {out['reinstated'] or '-'}")
    raise typer.Exit(1)
