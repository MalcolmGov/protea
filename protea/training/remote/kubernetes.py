"""Kubernetes: a Job with GPU requests, activeDeadlineSeconds as the hard limit, PVC-backed storage, checkpoint on stop."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from protea.training.remote.base import JobPlan, PlanRequest, RemoteAdapter


class KubernetesAdapter(RemoteAdapter):
    name = "kubernetes"

    def plan(self, req: PlanRequest) -> JobPlan:
        cfg, run_id, cfg_path = req.cfg, req.run_id, req.cfg_path
        k8s = self.remote.kubernetes
        assert k8s is not None
        plan = self._base_plan(
            req,
            safeguards=[
                "activeDeadlineSeconds kills the pod at the runtime limit; backoffLimit 0 so a failure never re-runs silently"
            ],
        )
        name = f"protea-{cfg.output.experiment_name}-{run_id}".lower().replace("_", "-")[:63]
        job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": k8s.namespace,
                "labels": {"app": "protea-train", "experiment": cfg.output.experiment_name},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": plan.max_runtime_minutes * 60,
                "ttlSecondsAfterFinished": 3600,
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "terminationGracePeriodSeconds": 300,
                        **({"serviceAccountName": k8s.service_account} if k8s.service_account else {}),
                        **({"nodeSelector": k8s.node_selector} if k8s.node_selector else {}),
                        "containers": [
                            {
                                "name": "train",
                                "image": self.remote.image,
                                "command": ["bash", "-lc", plan.command],
                                "env": [
                                    {"name": "PROTEA_RUN_ID", "value": run_id},
                                    {"name": "PROTEA_CONFIG", "value": cfg_path},
                                    {"name": "PROTEA_STORAGE", "value": self.remote.storage.uri},
                                    {
                                        "name": "HF_TOKEN",
                                        "valueFrom": {"secretKeyRef": {"name": "protea-secrets", "key": "HF_TOKEN"}},
                                    },
                                ],
                                "resources": {"limits": {"nvidia.com/gpu": self.remote.gpu_count}},
                                "volumeMounts": [{"name": "data", "mountPath": "/workspace"}],
                                "lifecycle": {
                                    "preStop": {
                                        "exec": {
                                            "command": ["bash", "-lc", "pkill -TERM -f 'protea train' && sleep 240"]
                                        }
                                    }
                                },
                            }
                        ],
                        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": k8s.pvc}}],
                    }
                },
            },
        }
        plan.artifacts = {"job.yaml": yaml.safe_dump(job, sort_keys=False)}
        return plan

    def launch(self, plan: JobPlan, artefact_dir: Path) -> str:
        subprocess.run(["kubectl", "apply", "-f", str(artefact_dir / "job.yaml")], check=True)
        k8s = self.remote.kubernetes
        assert k8s is not None
        return f"kubernetes:{k8s.namespace}/{yaml.safe_load(plan.artifacts['job.yaml'])['metadata']['name']}"
