"""Run directory, immutable config snapshot and manifest (spec §16, §17, §78).

runs/<experiment>/<run_id>/
  config.yaml      the exact config that produced this run (frozen; resuming with a different hash is refused)
  manifest.json    provenance: dataset hashes, base model, git commit, environment, status, final metrics
  metrics.jsonl    one line per logging step
  checkpoints/     trainer checkpoints (resume point)
  adapter/         final LoRA adapter or merged weights
  model-card.md    generated after training
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from protea.config.models import TrainingConfig
from protea.training.data import DatasetStats


class ImmutableConfigError(ValueError):
    pass


class RunManifest(BaseModel):
    run_id: str
    experiment_name: str
    model_family: str
    created_at: str
    config_hash: str
    base_model: str
    method: str
    seed: int
    dataset_key: str
    train: DatasetStats | None = None
    validation: DatasetStats | None = None
    git_commit: str | None = None
    git_dirty: bool = False
    environment: dict[str, Any] = Field(default_factory=dict)
    status: str = "created"  # created | running | completed | failed
    resumed_from: str | None = None
    steps: int = 0
    duration_s: float = 0.0
    final_metrics: dict[str, float] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)


def git_commit(repo: Path) -> tuple[str | None, bool]:
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, False
    return sha.stdout.strip() or None, bool(dirty.stdout.strip())


def environment_info() -> dict[str, Any]:
    info: dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform()}
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = torch.cuda.is_available()
        info["device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except ImportError:
        info["torch"] = None
    for mod in ("transformers", "trl", "peft", "bitsandbytes"):
        try:
            info[mod] = __import__(mod).__version__
        except ImportError:
            info[mod] = None
    return info


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


class RunDir:
    def __init__(self, path: Path):
        self.path = path

    @property
    def config_path(self) -> Path:
        return self.path / "config.yaml"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    @property
    def metrics_path(self) -> Path:
        return self.path / "metrics.jsonl"

    @property
    def checkpoints(self) -> Path:
        return self.path / "checkpoints"

    @property
    def adapter(self) -> Path:
        return self.path / "adapter"

    @property
    def card_path(self) -> Path:
        return self.path / "model-card.md"

    def manifest(self) -> RunManifest:
        return RunManifest.model_validate(json.loads(self.manifest_path.read_text(encoding="utf-8")))

    def save(self, manifest: RunManifest) -> None:
        self.manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def latest_checkpoint(self) -> Path | None:
        if not self.checkpoints.exists():
            return None
        found = [p for p in self.checkpoints.iterdir() if p.is_dir() and p.name.startswith("checkpoint-")]
        if not found:
            return None
        return max(found, key=lambda p: int(p.name.split("-")[-1]))

    def metrics(self) -> list[dict[str, Any]]:
        if not self.metrics_path.exists():
            return []
        return [json.loads(line) for line in self.metrics_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def create_run(
    cfg: TrainingConfig, cfg_hash: str, root: Path, *, run_id: str | None = None, repo: Path | None = None
) -> tuple[RunDir, RunManifest]:
    run_id = run_id or new_run_id()
    run = RunDir(root / cfg.output.dir / cfg.output.experiment_name / run_id)
    if run.path.exists():
        raise FileExistsError(f"run directory already exists: {run.path} (use --resume)")
    run.path.mkdir(parents=True)
    run.config_path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    sha, dirty = git_commit(repo or root)
    manifest = RunManifest(
        run_id=run_id,
        experiment_name=cfg.output.experiment_name,
        model_family=cfg.output.model_family,
        created_at=datetime.now(UTC).isoformat(),
        config_hash=cfg_hash,
        base_model=cfg.model.base_model,
        method=cfg.training.method,
        seed=cfg.training.seed,
        dataset_key=cfg.dataset.dataset_key,
        git_commit=sha,
        git_dirty=dirty,
        environment=environment_info(),
        tags=dict(cfg.output.tags),
    )
    run.save(manifest)
    return run, manifest


def open_run(path: Path, cfg_hash: str) -> tuple[RunDir, RunManifest]:
    """Reopen an existing run for resume. The config hash must match the frozen snapshot."""
    run = RunDir(path)
    if not run.manifest_path.exists():
        raise FileNotFoundError(f"not a run directory: {path}")
    manifest = run.manifest()
    if manifest.config_hash != cfg_hash:
        raise ImmutableConfigError(
            f"config hash {cfg_hash[:12]} differs from the run's frozen config {manifest.config_hash[:12]}; "
            "a run's configuration is immutable — start a new run instead"
        )
    return run, manifest


def find_latest_run(cfg: TrainingConfig, root: Path) -> Path | None:
    base = root / cfg.output.dir / cfg.output.experiment_name
    if not base.exists():
        return None
    runs = sorted(p for p in base.iterdir() if (p / "manifest.json").exists())
    return runs[-1] if runs else None


class MetricsLog:
    """JSONL metrics; MLflow mirroring when a tracking URI is configured and mlflow is importable."""

    def __init__(self, run: RunDir, manifest: RunManifest, mlflow_uri: str | None = None):
        self.run = run
        self._mlflow = None
        if mlflow_uri:
            try:
                import mlflow

                mlflow.set_tracking_uri(mlflow_uri)
                mlflow.set_experiment(manifest.experiment_name)
                self._mlflow = mlflow
                mlflow.start_run(run_name=manifest.run_id)
                mlflow.log_params(
                    {"config_hash": manifest.config_hash, "base_model": manifest.base_model, "method": manifest.method}
                )
            except ImportError:
                self._mlflow = None

    def log(self, step: int, values: dict[str, float]) -> None:
        with self.run.metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"step": step, **values}) + "\n")
        if self._mlflow is not None:
            self._mlflow.log_metrics({k: v for k, v in values.items() if isinstance(v, int | float)}, step=step)

    def close(self) -> None:
        if self._mlflow is not None:
            self._mlflow.end_run()


def python_executable() -> str:
    return sys.executable
