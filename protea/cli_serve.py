"""`protea serve` — the inference facade and the vLLM server command (Phase 5)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

serve_app = typer.Typer(help="Serving: the OpenAI-compatible facade and the vLLM engine command.", no_args_is_help=True)

DEFAULT_FACADE = Path("configs/serve/facade.yaml")
DEFAULT_VLLM = Path("configs/inference/vllm-qwen3-8b.yaml")


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


def build_facade(config: Path, *, backend: str | None = None, model: str | None = None):
    """Validate the config, build the backend provider and the FastAPI app. No network calls."""
    from protea.config import get_settings, load_config
    from protea.providers import ProviderNotConfigured, build_provider
    from protea.serving.app import create_app

    cfg = load_config(config, "serve")
    settings = get_settings()
    try:
        provider = build_provider(backend or cfg.backend, model=model or cfg.backend_model)
    except ProviderNotConfigured as exc:
        _fail(str(exc))
        raise
    router = _build_router(cfg, provider) if cfg.routing_policy else None
    try:
        app = create_app(cfg, provider, token=settings.protea_facade_token, router=router)
    except ValueError as exc:
        _fail(str(exc))
        raise
    return cfg, provider, app


def _build_router(cfg, backend):
    """Router candidates are built lazily by provider name; the facade's own backend serves the candidate whose
    provider matches it, so a `protea` candidate never opens a second connection to the engine."""
    from protea.config import load_config
    from protea.router import JsonlRouteSink, ModelRouter

    policy = load_config(Path(cfg.routing_policy), "routing")
    preset = {c.name: backend for c in policy.candidates if c.provider == backend.name}
    sink = JsonlRouteSink(Path(cfg.route_events)) if cfg.route_events else None
    return ModelRouter(policy, providers=preset, sink=sink)


@serve_app.command("facade")
def serve_facade(
    config: Path = typer.Option(DEFAULT_FACADE, exists=True),
    backend: str | None = typer.Option(None, help="Override the backend provider name (e.g. mock)."),
    model: str | None = typer.Option(None, help="Override the backend model id."),
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None),
    check: bool = typer.Option(False, "--check", help="Build the app, probe the backend once, print readiness, exit."),
) -> None:
    """Run the OpenAI-compatible facade (health, readiness, metrics, validation gate) in front of a backend."""
    cfg, provider, app = build_facade(config, backend=backend, model=model)
    state = app.state.facade
    if check:
        try:
            ready = asyncio.run(state.check_ready(force=True))
        except Exception as exc:  # noqa: BLE001 — surface any backend problem as not-ready
            ready, state.ready_detail = False, str(exc)
        typer.echo(f"backend      {provider.name}:{provider.model}")
        typer.echo(f"served as    {cfg.served_model} (aliases {sorted(cfg.aliases)})")
        typer.echo(f"auth         {'bearer token required' if cfg.require_token else 'open'}")
        typer.echo(f"ready        {ready} ({state.ready_detail})")
        raise typer.Exit(0 if ready else 1)
    import uvicorn

    uvicorn.run(
        app,
        host=host or cfg.host,
        port=port or cfg.port,
        workers=1,
        timeout_graceful_shutdown=int(cfg.drain_timeout_s),
        log_level="info",
    )


@serve_app.command("vllm")
def serve_vllm(
    config: Path = typer.Option(DEFAULT_VLLM, exists=True),
    adapter: str | None = typer.Option(None, help="LoRA adapter path or registry checkpoint to mount."),
    run: bool = typer.Option(False, "--run", help="Execute the command (needs a GPU and the vllm extra)."),
) -> None:
    """Print (or run) the vLLM OpenAI-compatible server command for an inference config."""
    import os
    import shutil

    from protea.config import load_config
    from protea.serving.vllm import vllm_args, vllm_command

    cfg = load_config(config, "inference")
    typer.echo(vllm_command(cfg, adapter_path=adapter))
    if not run:
        return
    if cfg.require_token and not os.environ.get("PROTEA_INFERENCE_TOKEN"):
        _fail("PROTEA_INFERENCE_TOKEN must be set when require_token is true")
    if shutil.which("vllm") is None:
        _fail("vllm is not installed on this machine (pip install -e '.[vllm]' on the GPU host)")
    args = [a if not a.startswith("$") else os.environ.get(a[1:], "") for a in vllm_args(cfg, adapter_path=adapter)]
    os.execvp(args[0], args)  # replaces this process with the engine so signals reach it directly


@serve_app.command("loadtest")
def serve_loadtest(
    url: str = typer.Option("http://127.0.0.1:8080", help="Facade base URL."),
    token: str | None = typer.Option(None, help="Bearer token (default: PROTEA_FACADE_TOKEN)."),
    concurrency: int = typer.Option(8),
    requests: int = typer.Option(100),
    max_tokens: int = typer.Option(64),
    latency_budget_ms: int | None = typer.Option(None, help="Fail when p95 exceeds this."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Required for a non-local URL (the backend may bill per token)."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Load-test a running facade: concurrent /v1/generate calls, latency percentiles, throughput, error rate."""
    from protea.config import get_settings
    from protea.serving.loadtest import LoadOptions, run_load

    local = any(h in url for h in ("127.0.0.1", "localhost", "[::1]"))
    if not local and not confirm:
        _fail(f"{url} is not local; pass --confirm to load a remote facade (its backend may spend tokens)")
    opts = LoadOptions(
        concurrency=concurrency, requests=requests, max_tokens=max_tokens, latency_budget_ms=latency_budget_ms
    )
    report = asyncio.run(run_load(url, token=token or get_settings().protea_facade_token, options=opts))
    typer.echo(report.model_dump_json(indent=2) if as_json else report.summary())
    if report.errors or report.within_budget is False:
        raise typer.Exit(1)
