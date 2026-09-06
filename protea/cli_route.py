"""`protea route` — explain a routing decision and show the capability matrix (Phase 7)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

route_app = typer.Typer(help="Model router: explain decisions, inspect the capability matrix.", no_args_is_help=True)

DEFAULT_POLICY = Path("configs/routing/zara-v0.yaml")


def _load(policy: Path, reports: Path | None):
    from protea.config import load_config
    from protea.router import ModelRouter, load_matrix

    cfg = load_config(policy, "routing")
    matrix = load_matrix(reports or Path(cfg.reports_dir), suite=cfg.suite)
    return cfg, ModelRouter(cfg, matrix=matrix, providers={})


@route_app.command("explain")
def route_explain(
    task_type: str = typer.Argument("chat", help="agent_generation | tool_calling | structured_output | … | chat"),
    policy: Path = typer.Option(DEFAULT_POLICY, exists=True),
    reports: Path | None = typer.Option(
        None, help="Benchmark reports directory (defaults to the policy's reports_dir)."
    ),
    tenant: str | None = typer.Option(None, help="Tenant reference for the canary bucket."),
    privacy: str = typer.Option("standard", help="standard | strict"),
    cost_policy: str = typer.Option("balanced", help="cheapest | balanced | best"),
    prompt_chars: int = typer.Option(1000),
    tools: int = typer.Option(0, help="Number of tools declared on the call."),
    connectors: int = typer.Option(0),
    financial: bool = typer.Option(False, "--financial"),
    pin: str | None = typer.Option(None, help="Pinned route name (per-agent version pin)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show which route would serve a request and why, without calling any model."""
    from protea.router import RouteRequest

    cfg, router = _load(policy, reports)
    req = RouteRequest(
        task_type=task_type,  # type: ignore[arg-type]
        prompt_chars=prompt_chars,
        tool_count=tools,
        connector_count=connectors,
        financial=financial,
        privacy=privacy,  # type: ignore[arg-type]
        cost_policy=cost_policy,  # type: ignore[arg-type]
        tenant_ref=tenant,
        pinned_route=pin,
    )
    decision = router.explain(req)
    if as_json:
        typer.echo(json.dumps(decision, indent=2))
        return
    typer.echo(f"policy       {cfg.name} {cfg.version} (canary {cfg.canary_percent}%)")
    typer.echo(f"route        {decision.get('route')}  ({decision.get('reason')})")
    if decision.get("route") is None:
        raise typer.Exit(1)
    typer.echo(
        f"task         {decision['task_type']} → {decision['category']} ≥ {decision['threshold']:.2f}; complexity {decision['complexity']}"
    )
    typer.echo(f"fallbacks    {' → '.join(decision['fallbacks']) or '-'}")
    for v in decision["considered"]:
        mark = "ok  " if v["eligible"] else "skip"
        typer.echo(f"  [{mark}] {v['route']:<18} {v['reason']}")


@route_app.command("matrix")
def route_matrix(
    policy: Path = typer.Option(DEFAULT_POLICY, exists=True),
    reports: Path | None = typer.Option(None),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print the capability matrix: ZaraBench category scores per served model, from committed reports."""
    cfg, router = _load(policy, reports)
    rows = router.matrix.summary()
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo(f"no benchmark reports under {reports or cfg.reports_dir} (mock and reference runs are excluded)")
        return
    for row in rows:
        cats = ", ".join(f"{k}={v:.2f}" for k, v in row.items() if k not in ("model", "zarascore", "partial", "run"))
        flag = " partial" if row["partial"] else ""
        typer.echo(f"{row['model']:<32} zarascore={row['zarascore']:.3f}{flag}  {cats}")
