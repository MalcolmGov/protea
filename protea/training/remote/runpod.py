"""RunPod: pod spec via the GraphQL API. Dry run renders the mutation; launch needs RUNPOD_API_KEY and --confirm."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from protea.training.remote.base import JobPlan, PlanRequest, RemoteAdapter

RUNPOD_GPU_IDS = {
    "rtx4090": "NVIDIA GeForce RTX 4090",
    "l4": "NVIDIA L4",
    "a10g": "NVIDIA A10G",
    "l40s": "NVIDIA L40S",
    "a6000": "NVIDIA RTX A6000",
    "a100-80gb": "NVIDIA A100 80GB PCIe",
    "h100-80gb": "NVIDIA H100 80GB HBM3",
}

DEPLOY_MUTATION = """mutation {{
  podFindAndDeployOnDemand(input: {{
    cloudType: {cloud_type}, gpuCount: {gpu_count}, volumeInGb: {volume_gb}, containerDiskInGb: {disk_gb},
    minVcpuCount: 2, minMemoryInGb: 8, gpuTypeId: "{gpu_type}", name: "{name}", imageName: "{image}",
    dockerArgs: "{docker_args}", ports: "22/tcp", volumeMountPath: "{volume_mount_path}",
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
            safeguards=[
                "entrypoint-train.sh wraps the trainer in `timeout` at the runtime limit, streams checkpoints to "
                "storage while it runs, and pushes the final adapter before exit",
                "the pod self-terminates after the run only when RUNPOD_API_KEY is passed to it (opt-in; the launch "
                "does not inject it by default)",
            ],
        )
        gpu_type = RUNPOD_GPU_IDS.get(self.remote.gpu, self.remote.gpu)
        env_pairs = [
            ("PROTEA_RUN_ID", run_id),
            ("PROTEA_CONFIG", cfg_path),
            ("PROTEA_STORAGE", self.remote.storage.uri),
            ("PROTEA_MAX_RUNTIME_MINUTES", str(plan.max_runtime_minutes)),
            ("PROTEA_IDLE_SHUTDOWN_MINUTES", str(plan.idle_shutdown_minutes)),
            ("PROTEA_SYNC_MINUTES", str(self.remote.checkpoint_sync_minutes)),
            ("HF_TOKEN", "${HF_TOKEN}"),
            ("PROTEA_STORAGE_CREDENTIALS", "${PROTEA_STORAGE_CREDENTIALS}"),
        ]
        # Non-AWS S3 stores (Cloudflare R2, MinIO) need the endpoint; aws-cli/boto3 read it from AWS_ENDPOINT_URL.
        # It is a plain URL, not a secret, so it is embedded directly rather than referenced by name.
        if self.remote.storage.endpoint:
            env_pairs.append(("AWS_ENDPOINT_URL", self.remote.storage.endpoint))
        env = ", ".join(f'{{ key: "{k}", value: "{v}" }}' for k, v in env_pairs)
        # Optional debug hold. RunPod deletes a pod the instant its container's main process exits, so a startup
        # crash in entrypoint-train.sh is invisible: the pod (and its logs) are gone before anyone can read them.
        # When PROTEA_DEBUG_HOLD_MINUTES is set at launch, keep a *failed* pod alive (`|| sleep N`) so its crash
        # log stays readable in the RunPod console. A successful run short-circuits the `||` and exits normally, so
        # this never delays or bills a good run. The command holds no `$`/`"`, so it survives expandvars and the
        # GraphQL string unchanged. Leave the env unset for normal runs.
        docker_args = "entrypoint-train.sh"
        hold = os.environ.get("PROTEA_DEBUG_HOLD_MINUTES", "").strip()
        if hold.isdigit() and int(hold) > 0:
            docker_args = f"entrypoint-train.sh || sleep {int(hold) * 60}"
        mutation = DEPLOY_MUTATION.format(
            cloud_type=rp.cloud_type,
            gpu_count=self.remote.gpu_count,
            volume_gb=rp.volume_gb,
            disk_gb=rp.container_disk_gb,
            gpu_type=gpu_type,
            name=f"protea-{cfg.output.experiment_name}-{run_id}",
            image=self.remote.image,
            # The image's entrypoint (bash -lc) runs this; the wrapper builds the trainer command from PROTEA_CONFIG
            # / PROTEA_RUN_ID and handles credentials, sync, the runtime limit and the final push.
            docker_args=docker_args,
            volume_mount_path=rp.volume_mount_path,
            env=env,
        )
        plan.artifacts = {"runpod-deploy.graphql": mutation}
        return plan

    def _resolved_mutation(self, artefact_dir: Path) -> str:
        # The artefact stores secrets as ${NAME} placeholders and never their values (so it is safe to review and
        # keep). Expand them from the environment in memory, only here at launch, so the real values reach RunPod
        # without ever being written to disk. An unset placeholder is left intact so RunPod surfaces a clear error.
        return os.path.expandvars((artefact_dir / "runpod-deploy.graphql").read_text(encoding="utf-8"))

    def launch(self, plan: JobPlan, artefact_dir: Path) -> str:
        api_key = getattr(self.settings, "runpod_api_key", None)
        if not api_key:
            raise RuntimeError("RUNPOD_API_KEY is not set")
        mutation = self._resolved_mutation(artefact_dir)
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
