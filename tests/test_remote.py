from pathlib import Path

import pytest
import yaml

from protea.config import config_hash, load_config
from protea.training.remote import ADAPTERS, PlanRequest, build_adapter, estimate, load_gpu_catalogue, write_artifacts
from protea.training.remote.base import size_bucket

REPO = Path(__file__).resolve().parent.parent
QLORA = REPO / "configs/training/protea-agent-8b-qlora.yaml"


@pytest.fixture
def catalogue():
    return load_gpu_catalogue(REPO / "configs/pricing/gpu.yaml")


def test_size_bucket():
    assert size_bucket("Qwen/Qwen3-8B") == "8b"
    assert size_bucket("Qwen/Qwen3-14B") == "14b"
    assert size_bucket("Qwen/Qwen2.5-0.5B-Instruct") == "small"
    assert size_bucket("openai/gpt-oss-20b") == "14b"
    assert size_bucket("mystery-model") == "8b"
    assert size_bucket("x", params_b=70) == "large"


def test_estimate_budget_and_vram_notes(catalogue):
    cfg = load_config(QLORA, "training")
    remote = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    est = estimate(cfg, remote, catalogue["a100-80gb"], approx_tokens=2_300_000)
    assert est.tokens_total == 2_300_000 * 3
    assert est.fits_vram
    assert est.within_budget
    assert 0.5 < est.hours < 3
    assert est.notes == []

    tight = cfg.model_copy(deep=True)
    tight.budget.max_cost_usd = 0.5
    tight.budget.max_runtime_minutes = 10
    est = estimate(tight, remote, catalogue["a100-80gb"], approx_tokens=2_300_000)
    assert not est.within_budget
    assert any("exceeds budget" in n for n in est.notes)
    assert any("runtime limit" in n for n in est.notes)

    full = cfg.model_copy(deep=True)
    full.model.load_in_4bit = False
    full.model.base_model = "Qwen/Qwen3-14B"
    full.training.method = "lora"
    est = estimate(full, remote.model_copy(update={"gpu": "l4"}), catalogue["l4"], approx_tokens=1000)
    assert not est.fits_vram

    spot = remote.model_copy(update={"spot": True})
    assert (
        estimate(cfg, spot, catalogue["a100-80gb"], 1_000_000).cost_usd
        < estimate(cfg, remote, catalogue["a100-80gb"], 1_000_000).cost_usd
    )


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_adapter_plans_with_safeguards_and_artifacts(name, catalogue, tmp_path: Path):
    remote_path = {
        "ssh": "ssh-generic.yaml",
        "runpod": "runpod-a100.yaml",
        "azure": "azure-nc24.yaml",
        "kubernetes": "kubernetes.yaml",
    }[name]
    cfg = load_config(QLORA, "training")
    remote = load_config(REPO / "configs/remote" / remote_path, "remote")
    est = estimate(cfg, remote, catalogue[remote.gpu], 2_300_000)
    adapter = build_adapter(remote)
    plan = adapter.plan(
        PlanRequest(
            cfg=cfg, cfg_path=str(QLORA), cfg_hash=config_hash(cfg), estimate=est, run_id="r1", dataset_sha256="ab" * 32
        )
    )
    assert plan.provider == name
    assert plan.max_runtime_minutes == 300
    assert plan.idle_shutdown_minutes == 15
    assert "HF_TOKEN" in plan.env_names
    assert len(plan.safeguards) >= 6
    assert "protea train local --config" in plan.command
    assert "--run-id r1" in plan.command
    for content in plan.artifacts.values():
        assert "hf_" not in content.lower().replace("hf_token", "")  # no secret values, only names
    written = write_artifacts(plan, tmp_path / name)
    assert (tmp_path / name / "plan.json").exists()
    assert len(written) == len(plan.artifacts) + 1
    summary = "\n".join(plan.summary_lines())
    assert "estimated cost" in summary
    assert "agent-training-0.1.0" in summary


def test_ssh_script_and_kubernetes_job_carry_the_limits(catalogue):
    cfg = load_config(QLORA, "training")
    ssh = load_config(REPO / "configs/remote/ssh-generic.yaml", "remote")
    req = PlanRequest(
        cfg=cfg,
        cfg_path="c.yaml",
        cfg_hash="h" * 64,
        estimate=estimate(cfg, ssh, catalogue["rtx4090"], 1000),
        run_id="r9",
    )
    script = build_adapter(ssh).plan(req).artifacts["job.sh"]
    assert "MAX_MINUTES=300" in script
    assert "trap checkpoint_and_exit TERM INT" in script
    assert "timeout --signal=TERM" in script
    assert "sudo shutdown -h now" in script

    k8s = load_config(REPO / "configs/remote/kubernetes.yaml", "remote")
    job = yaml.safe_load(build_adapter(k8s).plan(req).artifacts["job.yaml"])
    assert job["spec"]["activeDeadlineSeconds"] == 300 * 60
    assert job["spec"]["backoffLimit"] == 0
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert container["env"][-1]["valueFrom"]["secretKeyRef"]["key"] == "HF_TOKEN"

    az = load_config(REPO / "configs/remote/azure-nc24.yaml", "remote")
    arts = build_adapter(az).plan(req).artifacts
    assert "priority: 'Spot'" in arts["main.bicep"]
    assert "Microsoft.DevTestLab/schedules" in arts["main.bicep"]
    assert "az deployment group create" in arts["deploy.sh"]

    rp = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    mutation = build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]
    assert 'gpuTypeId: "NVIDIA A100 80GB PCIe"' in mutation
    assert '{ key: "HF_TOKEN", value: "${HF_TOKEN}" }' in mutation
    # The pod runs the image's wrapper, which loads creds, syncs checkpoints and enforces the limit; the bare
    # trainer command is built inside the wrapper from PROTEA_CONFIG / PROTEA_RUN_ID, not baked into dockerArgs.
    assert 'dockerArgs: "entrypoint-train.sh"' in mutation
    assert "protea train local" not in mutation
    assert '{ key: "PROTEA_SYNC_MINUTES", value: "10" }' in mutation


def test_remote_config_requires_provider_section():
    from protea.config.models import RemoteJobConfig, StorageSpec

    storage = StorageSpec(kind="s3", uri="s3://x")
    assert storage.endpoint is None
    with pytest.raises(ValueError, match="requires a `runpod` section"):
        RemoteJobConfig(provider="runpod", gpu="l4", storage=storage)


def test_runpod_volume_does_not_shadow_the_code(catalogue):
    """The pod volume must mount off the image's code dir (/workspace/protea), else the baked code is hidden."""
    cfg = load_config(QLORA, "training")
    rp = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    req = PlanRequest(
        cfg=cfg,
        cfg_path=str(QLORA),
        cfg_hash=config_hash(cfg),
        estimate=estimate(cfg, rp, catalogue["a100-80gb"], 1000),
        run_id="r1",
    )
    mutation = build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]
    assert 'volumeMountPath: "/runpod-volume"' in mutation
    assert 'volumeMountPath: "/workspace"' not in mutation


def test_runpod_debug_hold_keeps_a_failed_pod_alive(catalogue, monkeypatch):
    """PROTEA_DEBUG_HOLD_MINUTES wraps the entrypoint so a *failed* pod sleeps instead of vanishing (and taking
    its crash log with it). Unset -> the bare entrypoint; a successful run always short-circuits the `|| sleep`."""
    cfg = load_config(QLORA, "training")
    rp = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    req = PlanRequest(
        cfg=cfg,
        cfg_path=str(QLORA),
        cfg_hash=config_hash(cfg),
        estimate=estimate(cfg, rp, catalogue["a100-80gb"], 1000),
        run_id="r1",
    )

    monkeypatch.delenv("PROTEA_DEBUG_HOLD_MINUTES", raising=False)
    assert 'dockerArgs: "entrypoint-train.sh"' in build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]

    monkeypatch.setenv("PROTEA_DEBUG_HOLD_MINUTES", "30")
    held = build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]
    assert 'dockerArgs: "entrypoint-train.sh || sleep 1800"' in held
    # No `$` or `"` in the wrapper, so it survives expandvars and the GraphQL string unchanged.
    assert '$' not in "entrypoint-train.sh || sleep 1800"

    # Non-numeric / zero / blank are ignored (no accidental hold on normal runs).
    for junk in ("", "0", "abc", "-5"):
        monkeypatch.setenv("PROTEA_DEBUG_HOLD_MINUTES", junk)
        assert 'dockerArgs: "entrypoint-train.sh"' in build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]


def test_runpod_entrypoint_override_and_eval_env_passthrough(catalogue, monkeypatch):
    """PROTEA_ENTRYPOINT swaps the container command (e.g. the eval entrypoint), and the set PROTEA_* eval knobs are
    forwarded into the pod env so the eval entrypoint knows which adapter to score. Training leaves them unset."""
    cfg = load_config(QLORA, "training")
    rp = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    req = PlanRequest(
        cfg=cfg,
        cfg_path=str(QLORA),
        cfg_hash=config_hash(cfg),
        estimate=estimate(cfg, rp, catalogue["a100-80gb"], 1000),
        run_id="r1",
    )
    for var in (
        "PROTEA_ENTRYPOINT",
        "PROTEA_DEBUG_HOLD_MINUTES",
        "PROTEA_ADAPTER_KEY",
        "PROTEA_BASE_MODEL",
        "PROTEA_EVAL_PER_CATEGORY",
        "PROTEA_EVAL_CATEGORIES",
        "PROTEA_SERVED_AS",
    ):
        monkeypatch.delenv(var, raising=False)

    # Default: training entrypoint, none of the eval knobs leak into the pod env.
    m = build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]
    assert 'dockerArgs: "entrypoint-train.sh"' in m
    assert "PROTEA_ADAPTER_KEY" not in m

    # Eval launch: swap the entrypoint and forward the eval knobs as pod env pairs.
    monkeypatch.setenv("PROTEA_ENTRYPOINT", "entrypoint-eval.sh")
    monkeypatch.setenv("PROTEA_ADAPTER_KEY", "checkpoints/x/y/adapter")
    monkeypatch.setenv("PROTEA_BASE_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("PROTEA_EVAL_PER_CATEGORY", "2")
    m = build_adapter(rp).plan(req).artifacts["runpod-deploy.graphql"]
    assert 'dockerArgs: "entrypoint-eval.sh"' in m
    assert '{ key: "PROTEA_ADAPTER_KEY", value: "checkpoints/x/y/adapter" }' in m
    assert '{ key: "PROTEA_BASE_MODEL", value: "Qwen/Qwen3-8B" }' in m
    assert '{ key: "PROTEA_EVAL_PER_CATEGORY", value: "2" }' in m


def test_eval_entrypoint_is_shipped_and_scores_local(catalogue):
    """The image ships the eval wrapper + the ZaraBench suite, and the wrapper scores the adapter with the free
    in-process `local` provider (no judge, no API spend) and pushes the report."""
    import shutil
    import subprocess

    dockerfile = (REPO / "deployment/protea/Dockerfile.train").read_text(encoding="utf-8")
    assert "COPY evaluation ./evaluation" in dockerfile  # the suite tasks the scorer reads
    assert "COPY deployment/protea/entrypoint-eval.sh /usr/local/bin/entrypoint-eval.sh" in dockerfile

    script = REPO / "deployment/protea/entrypoint-eval.sh"
    body = script.read_text(encoding="utf-8")
    assert "protea evaluate run --provider local" in body
    assert "PROTEA_LOCAL_ADAPTER" in body  # the local provider reads the adapter path from this env
    assert "--per-category" in body
    assert 'protea-storage push "$OUT"' in body  # the report is shipped to storage
    assert 'exec >"$LOG" 2>&1' in body and "trap push_log EXIT" in body  # same off-box log capture as training
    # Scratch (the adapter copy + the report dir) must NOT live under $WORKDIR: /workspace/protea is root-owned and
    # the image runs as the unprivileged `protea` user, so writing there fails EACCES — the adapter never downloads,
    # every task errors, and no report is pushed. Keep all writes under a writable base.
    assert 'ADAPTER_DIR="$WORKDIR/' not in body and 'OUT="$WORKDIR/' not in body
    assert "WORKBASE=" in body
    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_runpod_launch_expands_secret_placeholders(catalogue, tmp_path, monkeypatch):
    """launch() substitutes ${SECRET} from the environment in memory; the on-disk artefact keeps placeholders."""
    from protea.training.remote import write_artifacts

    cfg = load_config(QLORA, "training")
    rp = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    req = PlanRequest(
        cfg=cfg,
        cfg_path=str(QLORA),
        cfg_hash=config_hash(cfg),
        estimate=estimate(cfg, rp, catalogue["a100-80gb"], 1000),
        run_id="r1",
    )
    adapter = build_adapter(rp)
    plan = adapter.plan(req)
    write_artifacts(plan, tmp_path)

    monkeypatch.setenv("HF_TOKEN", "hf_realvalue123")
    monkeypatch.setenv("PROTEA_STORAGE_CREDENTIALS", "AKIAKEYID:secretpart")
    resolved = adapter._resolved_mutation(tmp_path)
    assert '{ key: "HF_TOKEN", value: "hf_realvalue123" }' in resolved
    assert '{ key: "PROTEA_STORAGE_CREDENTIALS", value: "AKIAKEYID:secretpart" }' in resolved
    assert "${HF_TOKEN}" not in resolved
    # The artefact on disk still carries only placeholders — no secret is ever written down.
    on_disk = (tmp_path / "runpod-deploy.graphql").read_text(encoding="utf-8")
    assert 'value: "${HF_TOKEN}"' in on_disk
    assert "hf_realvalue123" not in on_disk


def test_train_entrypoint_is_wired_and_valid():
    """The RunPod wrapper exists, is shipped by the image, and does the persistence work the plan promises."""
    import shutil
    import subprocess

    script = REPO / "deployment/protea/entrypoint-train.sh"
    dockerfile = (REPO / "deployment/protea/Dockerfile.train").read_text(encoding="utf-8")
    body = script.read_text(encoding="utf-8")

    # The image installs the wrapper and runs it by default.
    assert "COPY deployment/protea/entrypoint-train.sh /usr/local/bin/entrypoint-train.sh" in dockerfile
    assert 'CMD ["entrypoint-train.sh"]' in dockerfile

    # It loads object-store credentials, pulls the dataset, syncs during the run, checkpoints on SIGTERM,
    # bounds the run with `timeout`, and pushes the final adapter (the only durable output, unguarded).
    assert "AWS_ACCESS_KEY_ID" in body
    assert "AWS_SECRET_ACCESS_KEY" in body
    assert "protea-storage pull" in body
    assert "protea-storage push" in body
    assert "trap checkpoint_and_exit TERM INT" in body
    assert "timeout --signal=TERM" in body
    assert "protea train local --config" in body
    assert "--eval" in body  # the run self-evaluates the adapter before the final push

    # The run captures its output to a file and ships it to storage on ANY exit, so a startup crash on a pod that
    # self-deletes is still diagnosable off-box (the console logs die with the pod).
    assert 'exec >"$LOG" 2>&1' in body
    assert "trap push_log EXIT" in body
    assert 'protea-storage push "$LOGDIR"' in body
    # The log dir must be writable by the image's unprivileged user; /workspace/protea is root-owned, so a new dir
    # under $WORKDIR would fail and kill the run before the trap is set. Keep it under /tmp.
    assert "/tmp/protea/logs" in body
    assert 'mkdir -p "$WORKDIR/logs"' not in body

    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_runpod_carries_s3_compatible_endpoint(catalogue):
    """A non-AWS S3 store (Cloudflare R2) surfaces its endpoint as AWS_ENDPOINT_URL and in the plan summary."""
    endpoint = "https://acct123.r2.cloudflarestorage.com"
    cfg = load_config(QLORA, "training")
    base = load_config(REPO / "configs/remote/runpod-a100.yaml", "remote")
    remote = base.model_copy(deep=True)
    remote.storage = remote.storage.model_copy(update={"uri": "s3://my-bucket/agent-training", "endpoint": endpoint})
    req = PlanRequest(
        cfg=cfg,
        cfg_path=str(QLORA),
        cfg_hash=config_hash(cfg),
        estimate=estimate(cfg, remote, catalogue["a100-80gb"], 1_000_000),
        run_id="r2",
    )
    plan = build_adapter(remote).plan(req)
    mutation = plan.artifacts["runpod-deploy.graphql"]
    assert f'{{ key: "AWS_ENDPOINT_URL", value: "{endpoint}" }}' in mutation
    assert endpoint in "\n".join(plan.summary_lines())
    assert endpoint not in mutation.replace(f'value: "{endpoint}"', "")  # only the env value, nowhere unexpected

    # A plain AWS S3 store (no endpoint) does not emit AWS_ENDPOINT_URL.
    aws = remote.model_copy(deep=True)
    aws.storage = aws.storage.model_copy(update={"endpoint": None})
    aws_mutation = build_adapter(aws).plan(req).artifacts["runpod-deploy.graphql"]
    assert "AWS_ENDPOINT_URL" not in aws_mutation
