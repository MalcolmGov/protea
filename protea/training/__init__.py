"""Training: dataset rendering, immutable run snapshots, TRL/PEFT trainer, model cards, remote GPU adapters."""

from protea.training.data import DatasetStats, GoldenLeakError, load_records, render_example
from protea.training.run import ImmutableConfigError, RunDir, RunManifest, create_run, open_run

__all__ = [
    "DatasetStats",
    "GoldenLeakError",
    "ImmutableConfigError",
    "RunDir",
    "RunManifest",
    "create_run",
    "load_records",
    "open_run",
    "render_example",
]
