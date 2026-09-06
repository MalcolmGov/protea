import json
from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def test_config_validate_repo_configs():
    result = runner.invoke(app, ["config", "validate", str(REPO / "configs")])
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output
    assert result.output.count("ok ") >= 7


def test_config_validate_rejects_bad_file(tmp_path):
    bad = tmp_path / "training" / "x.yaml"
    bad.parent.mkdir()
    bad.write_text(
        "model: {base_model: m}\ntraining: {method: qlora}\n"
        "dataset: {train: a, validation: b, dataset_key: k}\noutput: {experiment_name: e}\n"
    )
    result = runner.invoke(app, ["config", "validate", str(bad)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_providers_list_and_unconfigured_health(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "PROTEA_INFERENCE_URL"):
        monkeypatch.delenv(var, raising=False)
    from protea.config import get_settings

    get_settings.cache_clear()
    result = runner.invoke(app, ["providers", "list"])
    assert result.exit_code == 0
    assert "mock" in result.output
    assert "anthropic" in result.output
    health = runner.invoke(app, ["providers", "health", "mock"])
    assert health.exit_code == 0
    assert json.loads(health.output)["ok"] is True
    missing = runner.invoke(app, ["providers", "health", "openai"])
    assert missing.exit_code == 1


def test_dataset_validate_and_stats(tmp_path, tool_example):
    path = tmp_path / "d.jsonl"
    path.write_text(tool_example.model_dump_json() + "\n")
    assert runner.invoke(app, ["dataset", "validate", str(path)]).exit_code == 0
    stats = runner.invoke(app, ["dataset", "stats", str(path)])
    assert stats.exit_code == 0
    assert "tool_calling=1" in stats.output


def test_registry_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTEA_REGISTRY_DIR", str(tmp_path))
    from protea.config import get_settings

    get_settings.cache_clear()
    assert "no models registered" in runner.invoke(app, ["registry", "models"]).output
    from protea.registry import ModelRegistry
    from protea.schemas.registry import ModelEntry

    ModelRegistry(tmp_path / "models.json").add(
        ModelEntry(
            family="protea-agent", version="0.1.0", base_model="Qwen/Qwen3-8B", training_dataset="agent-training-0.1.0"
        )
    )
    ok = runner.invoke(app, ["registry", "promote", "protea-agent-0.1.0", "--to", "candidate"])
    assert ok.exit_code == 0
    assert "candidate" in ok.output
    bad = runner.invoke(app, ["registry", "promote", "protea-agent-0.1.0", "--to", "production"])
    assert bad.exit_code == 1
    get_settings.cache_clear()
