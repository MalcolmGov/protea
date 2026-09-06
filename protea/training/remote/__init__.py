"""Remote GPU job adapters: plan (always) → launch (only with --confirm)."""

from protea.config.models import RemoteJobConfig
from protea.training.remote.azure import AzureAdapter
from protea.training.remote.base import (
    Estimate,
    GpuSpec,
    JobPlan,
    PlanRequest,
    RemoteAdapter,
    estimate,
    load_gpu_catalogue,
    write_artifacts,
)
from protea.training.remote.kubernetes import KubernetesAdapter
from protea.training.remote.runpod import RunPodAdapter
from protea.training.remote.ssh import SshAdapter

ADAPTERS: dict[str, type[RemoteAdapter]] = {
    "ssh": SshAdapter,
    "runpod": RunPodAdapter,
    "azure": AzureAdapter,
    "kubernetes": KubernetesAdapter,
}


def build_adapter(remote: RemoteJobConfig, settings=None) -> RemoteAdapter:
    return ADAPTERS[remote.provider](remote, settings)


__all__ = [
    "ADAPTERS",
    "Estimate",
    "GpuSpec",
    "JobPlan",
    "PlanRequest",
    "RemoteAdapter",
    "build_adapter",
    "estimate",
    "load_gpu_catalogue",
    "write_artifacts",
]
