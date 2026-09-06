from pathlib import Path

import pytest

from protea.config import config_hash, load_config
from protea.training.card import model_card
from protea.training.data import DatasetStats
from protea.training.run import ImmutableConfigError, MetricsLog, RunDir, create_run, find_latest_run, open_run

REPO = Path(__file__).resolve().parent.parent
SMOKE = REPO / "configs/training/ci-smoke.yaml"


def _stats(sha: str = "a" * 64) -> DatasetStats:
    return DatasetStats(path="x", sha256=sha, examples=8, approx_tokens=4000, by_task_type={"routing": 8})


def test_create_open_and_immutable_config(tmp_path: Path):
    cfg = load_config(SMOKE, "training")
    h = config_hash(cfg)
    run, manifest = create_run(cfg, h, tmp_path, run_id="r1", repo=REPO)
    assert run.config_path.exists()
    assert manifest.status == "created"
    assert manifest.base_model == "tiny-random"
    assert manifest.git_commit is None or len(manifest.git_commit) == 40
    assert run.path == tmp_path / "checkpoints/ci-smoke/ci-smoke/r1"
    with pytest.raises(FileExistsError):
        create_run(cfg, h, tmp_path, run_id="r1")

    reopened, m2 = open_run(run.path, h)
    assert m2.run_id == "r1"
    with pytest.raises(ImmutableConfigError):
        open_run(run.path, "f" * 64)
    with pytest.raises(FileNotFoundError):
        open_run(tmp_path / "nowhere", h)
    assert find_latest_run(cfg, tmp_path) == run.path
    other = cfg.model_copy(deep=True)
    other.output.experiment_name = "elsewhere"
    assert find_latest_run(other, tmp_path) is None


def test_checkpoints_metrics_and_card(tmp_path: Path):
    cfg = load_config(SMOKE, "training")
    run, manifest = create_run(cfg, config_hash(cfg), tmp_path, run_id="r2")
    assert run.latest_checkpoint() is None
    for n in (3, 12, 6):
        (run.checkpoints / f"checkpoint-{n}").mkdir(parents=True)
    assert run.latest_checkpoint() == run.checkpoints / "checkpoint-12"

    log = MetricsLog(run, manifest)
    log.log(1, {"loss": 2.5})
    log.log(2, {"loss": 2.1, "text": "ignored"})  # type: ignore[dict-item]
    log.close()
    assert [m["step"] for m in run.metrics()] == [1, 2]

    manifest.train = _stats()
    manifest.validation = _stats("b" * 64)
    manifest.status = "completed"
    manifest.steps = 6
    manifest.final_metrics = {"train_loss": 1.234, "eval_loss": 1.5}
    card = model_card(manifest, cfg)
    assert "| Base model | `tiny-random` |" in card
    assert "lora (rank 4, alpha 8, dropout 0.0)" in card
    assert "| eval_loss | 1.5000 |" in card
    assert "ZaraBench: not yet run" in card
    with_bench = model_card(manifest, cfg, zarabench={"zarascore": "61.0%"})
    assert "### ZaraBench" in with_bench


def test_rundir_manifest_roundtrip(tmp_path: Path):
    cfg = load_config(SMOKE, "training")
    run, manifest = create_run(cfg, config_hash(cfg), tmp_path, run_id="r3")
    manifest.tags["note"] = "roundtrip"
    run.save(manifest)
    assert RunDir(run.path).manifest().tags == {"note": "roundtrip"}
