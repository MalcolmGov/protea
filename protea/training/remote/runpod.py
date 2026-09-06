"""RunPod: pod spec via the GraphQL API. Dry run renders the mutation; launch needs RUNPOD_API_KEY and --confirm."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from protea.training.remote.base import JobPlan, PlanRequest, RemoteAdapter

RUNPOD_GPU_IDS = {
    "rtx4090": "NVIDIA GeForce RTX 4090",
    "l4": "NVIDIA L4",
    "a10g": "NVIDIA A10G",
    "l40s": "NVIDIA L40S",
    "a100-80gb": "NVIDIA A100 80GB PCIe",
    "h100-80gb": "NVIDIA H100 80GB HBM3",
}

DEPLOY_MUTATION = """mutation {{
  podFindAndDeployOnDemand(input: {{
    cloudType: {cloud_type}, gpuCount: {gpu_count}, volumeInGb: {volume_gb}, containerDiskInGb: {disk_gb},
    minVcpuCount: 8, minMemoryInGb: 32, gpuTypeId: "{gpu_type}", name: "{name}", imageName: "{image}",
    dockerArgs: "{docker_args}", ports: "22/tcp", volumeMountPath: "/workspace",
    env: [{env}]
  }}) {{ id imageName machineId }}
}}"""


class RunPodAdapter(RemoteAdapter):
    name = "runpod"

    def plan(self, req: PlanRequest) -> JobPlan:
        cfg, run_id, cfg_path = req.cfg, req.run_id, req.cfg_path
        rp = self.remote.runpod
        assert rp is not None
        plan = self._base_plan(
            req,
            env_names=["RUNPOD_API_KEY"],
            safeguards=["the container entrypoint enforces the runtime limit and terminates the pod when the run ends"],
        )
        gpu_type = RUNPOD_GPU_IDS.get(self.remote.gpu, self.remote.gpu)
        env_pairs = [
            ("PROTEA_RUN_ID", run_id),
            ("PROTEA_CONFIG", cfg_path),
            ("PROTEA_STORAGE", self.remote.storage.uri),
            ("PROTEA_MAX_RUNTIME_MINUTES", str(plan.max_runtime_minutes)),
            ("PROTEA_IDLE_SHUTDOWN_MINUTES", str(plan.idle_shutdown_minutes)),
            ("HF_TOKEN", "${HF_TOKEN}"),
            ("PROTEA_STORAGE_CREDENTIALS", "${PROTEA_STORAGE_CREDENTIALS}"),
        ]
        env = ", ".join(f'{{ key: "{k}", value: "{v}" }}' for k, v in env_pairs)
        mutation = DEPLOY_MUTATION.format(
            cloud_type=rp.cloud_type,
            gpu_count=self.remote.gpu_count,
            volume_gb=rp.volume_gb,
            disk_gb=rp.container_disk_gb,
            gpu_type=gpu_type,
            name=f"protea-{cfg.output.experiment_name}-{run_id}",
            image=self.remote.image,
            docker_args=plan.command.replace('"', '\\"'),
            env=env,
        )
        plan.artifacts = {"runpod-deploy.graphql": mutation}
        return plan

    def launch(self, plan: JobPlan, artefact_dir: Path) -> str:
        api_key = getattr(self.settings, "runpod_api_key", None)
        if not api_key:
            raise RuntimeError("RUNPOD_API_KEY is not set")
        mutation = (artefact_dir / "runpod-deploy.graphql").read_text(encoding="utf-8")
        resp = httpx.post(
            "https://api.runpod.io/graphql",
            params={"api_key": api_key},
            json={"query": mutation},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(f"runpod: {json.dumps(data['errors'])[:300]}")
        pod = data["data"]["podFindAndDeployOnDemand"]
        return f"runpod:{pod['id']}"
