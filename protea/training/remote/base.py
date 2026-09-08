"""Remote GPU jobs: estimate first, launch only on explicit confirmation (spec §19, §52; roadmap Phase 4 step 3).

Every adapter renders the artefacts it would use (script, pod spec, Bicep, Job YAML) so a dry run can be reviewed
line by line. Safeguards are part of the plan, not an afterthought: hard runtime limit, idle shutdown, checkpoint
sync to storage that outlives the instance, and secrets passed by environment variable name, never by value.
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from protea.config.models import GpuCatalogue, GpuPrice, RemoteJobConfig, TrainingConfig


class GpuSpec(GpuPrice):
    name: str


def load_gpu_catalogue(path: Path) -> dict[str, GpuSpec]:
    catalogue = GpuCatalogue.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return {name: GpuSpec(name=name, **spec.model_dump()) for name, spec in catalogue.gpus.items()}


def size_bucket(base_model: str, params_b: float | None = None) -> str:
    """Map a base model to the throughput bucket in the catalogue."""
    if params_b is None:
        lowered = base_model.lower()
        for token, size in (
            ("0.5b", 0.5),
            ("1.5b", 1.5),
            ("3b", 3),
            ("7b", 7),
            ("8b", 8),
            ("14b", 14),
            ("20b", 20),
            ("24b", 24),
            ("32b", 32),
            ("70b", 70),
        ):
            if token in lowered:
                params_b = size
                break
    if params_b is None:
        return "8b"
    if params_b <= 4:
        return "small"
    if params_b <= 10:
        return "8b"
    if params_b <= 20:
        return "14b"
    return "large"


class Estimate(BaseModel):
    tokens_total: int
    throughput_tps: float
    hours: float
    setup_hours: float = 0.25
    cost_usd: float
    within_budget: bool
    fits_vram: bool
    notes: list[str] = Field(default_factory=list)


def estimate(
    cfg: TrainingConfig, remote: RemoteJobConfig, gpu: GpuSpec, approx_tokens: int, *, overhead: float = 1.3
) -> Estimate:
    bucket = size_bucket(cfg.model.base_model)
    tps = gpu.tokens_per_second.get(bucket) or min(gpu.tokens_per_second.values())
    tokens_total = int(approx_tokens * cfg.training.epochs)
    hours = tokens_total / (tps * remote.gpu_count * 3600) * overhead + 0.25
    price = gpu.usd_per_hour.get(remote.provider) or gpu.usd_per_hour.get("generic", 0.0)
    if remote.spot:
        price *= gpu.spot_discount
    cost = round(hours * price * remote.gpu_count, 2)
    notes = []
    needed = {
        "small": 8,
        "8b": 20 if not cfg.model.load_in_4bit else 12,
        "14b": 32 if not cfg.model.load_in_4bit else 20,
        "large": 80,
    }[bucket]
    fits = gpu.vram_gb >= needed
    if not fits:
        notes.append(f"{gpu.name} has {gpu.vram_gb} GB; this configuration needs about {needed} GB")
    max_minutes = remote.max_runtime_minutes or cfg.budget.max_runtime_minutes
    if hours * 60 > max_minutes:
        notes.append(f"estimated {hours:.1f} h exceeds the runtime limit of {max_minutes} min")
    within = cost <= cfg.budget.max_cost_usd
    if not within:
        notes.append(f"estimated USD {cost:.2f} exceeds budget.max_cost_usd={cfg.budget.max_cost_usd}")
    return Estimate(
        tokens_total=tokens_total,
        throughput_tps=tps,
        hours=round(hours, 2),
        cost_usd=cost,
        within_budget=within,
        fits_vram=fits,
        notes=notes,
    )


class PlanRequest(BaseModel):
    """Everything an adapter needs to render a plan; produced by the CLI after the estimate."""

    cfg: TrainingConfig
    cfg_path: str
    cfg_hash: str
    estimate: Estimate
    run_id: str
    dataset_sha256: str | None = None


class JobPlan(BaseModel):
    provider: str
    gpu: str
    gpu_count: int
    image: str
    spot: bool
    base_model: str
    method: str
    dataset_key: str
    dataset_sha256: str | None
    config_hash: str
    experiment: str
    estimate: Estimate
    max_runtime_minutes: int
    idle_shutdown_minutes: int
    storage: str
    command: str
    env_names: list[str]  # secrets referenced by NAME only
    safeguards: list[str]
    artifacts: dict[str, str] = Field(default_factory=dict)  # filename -> content

    def summary_lines(self) -> list[str]:
        e = self.estimate
        rows = [
            ("provider", self.provider),
            ("gpu", f"{self.gpu_count} × {self.gpu}" + (" (spot)" if self.spot else "")),
            ("image", self.image),
            ("base model", self.base_model),
            ("method", self.method),
            ("dataset", f"{self.dataset_key} ({(self.dataset_sha256 or 'unregistered')[:12]})"),
            ("config hash", self.config_hash[:12]),
            ("tokens (× epochs)", f"{e.tokens_total:,}"),
            ("throughput", f"{e.throughput_tps:,.0f} tok/s per GPU"),
            ("estimated duration", f"{e.hours:.2f} h (incl. setup)"),
            ("estimated cost", f"USD {e.cost_usd:.2f}"),
            ("within budget", str(e.within_budget)),
            ("fits VRAM", str(e.fits_vram)),
            ("runtime limit", f"{self.max_runtime_minutes} min"),
            ("idle shutdown", f"{self.idle_shutdown_minutes} min"),
            ("storage", self.storage),
            ("secrets by name", ", ".join(self.env_names) or "none"),
        ]
        lines = [f"{k:<20} {v}" for k, v in rows]
        lines += [f"safeguard            {s}" for s in self.safeguards]
        lines += [f"note                 {n}" for n in e.notes]
        return lines


def training_command(cfg_path: str, run_id: str, *, resume: bool = False) -> str:
    parts = ["protea", "train", "local", "--config", cfg_path, "--run-id", run_id, "--allow-unregistered"]
    if resume:
        parts.append("--resume")
    return shlex.join(parts)


def common_safeguards(remote: RemoteJobConfig, max_minutes: int, idle: int) -> list[str]:
    return [
        f"hard runtime limit {max_minutes} min (checkpoint every {remote.checkpoint_sync_minutes} min, synced to {remote.storage.uri})",
        f"idle shutdown after {idle} min without a training process",
        "checkpoint-before-stop: the job traps SIGTERM and saves before exiting",
        "dataset and checkpoints live on external storage; the instance disk is disposable",
        "secrets are read from the environment by name; nothing is baked into artefacts",
    ]


class RemoteAdapter(ABC):
    name: str = "abstract"

    def __init__(self, remote: RemoteJobConfig, settings: Any | None = None):
        self.remote = remote
        self.settings = settings

    @abstractmethod
    def plan(self, req: PlanRequest) -> JobPlan: ...

    @abstractmethod
    def launch(self, plan: JobPlan, artefact_dir: Path) -> str:
        """Submit the job. Only ever called after the plan was printed and --confirm given. Returns a handle."""

    def _base_plan(
        self, req: PlanRequest, *, env_names: list[str] | None = None, safeguards: list[str] | None = None
    ) -> JobPlan:
        cfg = req.cfg
        max_minutes = self.remote.max_runtime_minutes or cfg.budget.max_runtime_minutes
        idle = self.remote.idle_shutdown_minutes or cfg.budget.idle_shutdown_minutes
        return JobPlan(
            provider=self.name,
            gpu=self.remote.gpu,
            gpu_count=self.remote.gpu_count,
            image=self.remote.image,
            spot=self.remote.spot,
            base_model=cfg.model.base_model,
            method=cfg.training.method,
            dataset_key=cfg.dataset.dataset_key,
            dataset_sha256=req.dataset_sha256,
            config_hash=req.cfg_hash,
            experiment=cfg.output.experiment_name,
            estimate=req.estimate,
            max_runtime_minutes=max_minutes,
            idle_shutdown_minutes=idle,
            storage=f"{self.remote.storage.kind}:{self.remote.storage.uri}"
            + (f" @ {self.remote.storage.endpoint}" if self.remote.storage.endpoint else ""),
            command=training_command(req.cfg_path, req.run_id),
            env_names=["HF_TOKEN", "PROTEA_STORAGE_CREDENTIALS", *(env_names or [])],
            safeguards=common_safeguards(self.remote, max_minutes, idle) + (safeguards or []),
        )


def write_artifacts(plan: JobPlan, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in plan.artifacts.items():
        p = out / name
        p.write_text(content, encoding="utf-8")
        written.append(p)
    (out / "plan.json").write_text(plan.model_dump_json(indent=2, exclude={"artifacts"}) + "\n", encoding="utf-8")
    written.append(out / "plan.json")
    return written
