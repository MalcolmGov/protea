"""Offline end-to-end training smoke: tiny random model, checkpoint, resume, register. Skipped without the stack."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from protea.cli import app

pytest.importorskip("torch")
pytest.importorskip("trl")

REPO = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "protea"
    (root / "configs/training").mkdir(parents=True)
    shutil.copytree(REPO / "tests/fixtures/training", root / "tests/fixtures/training")
    shutil.copy(REPO / "configs/training/ci-smoke.yaml", root / "configs/training/ci-smoke.yaml")
    (root / "registry").mkdir()
    (root / "registry/models.json").write_text("[]")
    (root / "registry/datasets.json").write_text("[]")
    return root


def test_train_checkpoint_resume_card_and_register(tmp_path: Path, monkeypatch):
    root = _root(tmp_path)
    cfg = root / "configs/training/ci-smoke.yaml"
    base = ["train", "local", "--config", str(cfg), "--root", str(root), "--allow-unregistered"]

    first = runner.invoke(app, [*base, "--run-id", "t1", "--max-steps", "3"])
    assert first.exit_code == 0, first.output
    run_dir = root / "checkpoints/ci-smoke/ci-smoke/t1"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["steps"] == 3
    assert "eval_loss" in manifest["final_metrics"]
    assert (run_dir / "adapter/adapter_model.safetensors").exists()
    assert (run_dir / "checkpoints/checkpoint-3").exists()
    assert (run_dir / "model-card.md").exists()
    assert len((run_dir / "metrics.jsonl").read_text().splitlines()) >= 3

    resumed = runner.invoke(app, [*base, "--resume", "--max-steps", "5"])
    assert resumed.exit_code == 0, resumed.output
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["steps"] == 5
    assert manifest["resumed_from"].endswith("checkpoint-3")

    # the frozen config is immutable: changing the learning rate must be refused on resume
    text = cfg.read_text().replace("learning_rate: 5.0e-4", "learning_rate: 1.0e-4")
    cfg.write_text(text)
    refused = runner.invoke(app, [*base, "--resume"])
    assert refused.exit_code == 1
    assert "immutable" in refused.output

    runs = runner.invoke(app, ["train", "runs", "--root", str(root), "--output-dir", "checkpoints/ci-smoke"])
    assert "ci-smoke/t1" in runs.output
    assert "completed" in runs.output

    monkeypatch.setenv("PROTEA_REGISTRY_DIR", str(root / "registry"))
    from protea.config import get_settings

    get_settings.cache_clear()
    reg = runner.invoke(app, ["train", "register", str(run_dir), "--version", "0.0.1", "--root", str(root)])
    assert reg.exit_code == 0, reg.output
    entries = json.loads((root / "registry/models.json").read_text())
    assert entries[0]["deployment_status"] == "experimental"
    assert entries[0]["artifact_sha256"]
    dup = runner.invoke(app, ["train", "register", str(run_dir), "--version", "0.0.1", "--root", str(root)])
    assert dup.exit_code == 1
    get_settings.cache_clear()
