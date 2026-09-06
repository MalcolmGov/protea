from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app
from protea.cli_storage import app as storage_app
from protea.cli_storage import sync_command

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def test_serve_vllm_prints_the_engine_command():
    r = runner.invoke(app, ["serve", "vllm", "--config", str(REPO / "configs/inference/vllm-qwen3-8b.yaml")])
    assert r.exit_code == 0, r.output
    cmd = r.output.strip()
    assert cmd.startswith("vllm serve Qwen/Qwen3-8B")
    assert "--served-model-name protea-agent" in cmd
    assert "--tool-call-parser hermes" in cmd
    assert "--api-key $PROTEA_INFERENCE_TOKEN" in cmd
    assert "--enable-lora" not in cmd
    r = runner.invoke(
        app,
        ["serve", "vllm", "--config", str(REPO / "configs/inference/vllm-qwen3-8b.yaml"), "--adapter", "/adapters/a1"],
    )
    assert "--lora-modules protea-agent=/adapters/a1" in r.output


def test_serve_facade_check_with_mock_backend(monkeypatch):
    monkeypatch.setenv("PROTEA_FACADE_TOKEN", "t")
    from protea.config import get_settings

    get_settings.cache_clear()
    r = runner.invoke(
        app, ["serve", "facade", "--config", str(REPO / "configs/serve/facade.yaml"), "--backend", "mock", "--check"]
    )
    assert r.exit_code == 0, r.output
    assert "ready        True" in r.output
    assert "bearer token required" in r.output
    get_settings.cache_clear()


def test_serve_facade_refuses_without_token(monkeypatch):
    monkeypatch.delenv("PROTEA_FACADE_TOKEN", raising=False)
    from protea.config import get_settings

    get_settings.cache_clear()
    r = runner.invoke(
        app, ["serve", "facade", "--config", str(REPO / "configs/serve/facade.yaml"), "--backend", "mock", "--check"]
    )
    assert r.exit_code == 1
    assert "PROTEA_FACADE_TOKEN" in r.output
    get_settings.cache_clear()


def test_storage_commands_pick_the_tool(tmp_path: Path):
    assert sync_command(str(tmp_path), "s3://bucket/runs/x")[:3] == ["aws", "s3", "sync"]
    assert sync_command("https://acct.blob.core.windows.net/runs/x", str(tmp_path))[0] == "azcopy"
    assert sync_command(str(tmp_path), "protea@host:/srv/runs/x")[0] == "rsync"
    r = runner.invoke(storage_app, ["push", str(tmp_path), "s3://bucket/runs", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == f"aws s3 sync {tmp_path} s3://bucket/runs/{tmp_path.name}"
    r = runner.invoke(
        storage_app, ["pull", "s3://bucket/runs", str(tmp_path / "runs"), str(tmp_path / "protea_data"), "--dry-run"]
    )
    assert r.exit_code == 0, r.output
    assert "s3://bucket/runs/runs" in r.output
    assert "s3://bucket/runs/protea_data" in r.output
