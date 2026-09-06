"""`protea-storage` — the sync helper the remote job scripts call: datasets, checkpoints and adapters live on
storage that outlives the instance (ADR-008 safeguard 7). Wraps the provider tools; `--dry-run` prints only."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import typer

app = typer.Typer(
    help="Push/pull run directories to S3, Azure Blob, rsync targets or local paths.", no_args_is_help=True
)


HTTPS = "https://"


def _fail(msg: str, code: int = 1) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)
    raise typer.Exit(code)


def _is_remote_path(uri: str) -> bool:
    return ":" in uri and "@" in uri.split(":", 1)[0] and not uri.startswith(("http://", HTTPS))


def sync_command(src: str, dst: str) -> list[str]:
    """Pick the tool by URI scheme. Both sides may be local; one side may be remote."""
    remote = src if not Path(src).exists() or src.startswith(("s3://", HTTPS)) else dst
    if remote.startswith("s3://"):
        return ["aws", "s3", "sync", src, dst]
    if remote.startswith(HTTPS) and ".blob.core.windows.net" in remote:
        return ["azcopy", "sync", src, dst, "--recursive=true"]
    if _is_remote_path(remote):
        return ["rsync", "-az", "--partial", src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    return ["rsync", "-a", src.rstrip("/") + "/", dst.rstrip("/") + "/"]


def _run(cmd: list[str], dry_run: bool) -> None:
    typer.echo(shlex.join(cmd))
    if dry_run:
        return
    if shutil.which(cmd[0]) is None:
        _fail(f"{cmd[0]} is not installed")
    subprocess.run(cmd, check=True)


@app.command()
def push(
    src: Path = typer.Argument(..., exists=True),
    uri: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Upload SRC to URI/<basename(SRC)>."""
    _run(sync_command(str(src), f"{uri.rstrip('/')}/{src.name}"), dry_run)


@app.command()
def pull(
    uri: str = typer.Argument(...),
    dst: list[Path] = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Download URI/<basename(DST)> into each DST (missing remote directories are not an error)."""
    for d in dst:
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)
        cmd = sync_command(f"{uri.rstrip('/')}/{d.name}", str(d))
        typer.echo(shlex.join(cmd))
        if dry_run:
            continue
        if shutil.which(cmd[0]) is None:
            _fail(f"{cmd[0]} is not installed")
        subprocess.run(cmd, check=False)


if __name__ == "__main__":
    app()
