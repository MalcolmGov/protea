import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def test_security_commands(tmp_path):
    assert runner.invoke(app, ["security", "verify"]).exit_code == 0
    r = runner.invoke(app, ["security", "run", "--provider", "reference", "--out", str(tmp_path), "--label", "t"])
    assert r.exit_code == 0, r.output
    assert "security strict 1.000" in r.output
    report = tmp_path / "security-0.1" / "reference-reference-t.json"
    assert runner.invoke(app, ["security", "gate", str(report)]).exit_code == 0
    r = runner.invoke(app, ["security", "run", "--provider", "mock", "--out", str(tmp_path), "--label", "m"])
    assert r.exit_code == 0
    gate = runner.invoke(app, ["security", "gate", str(tmp_path / "security-0.1" / "mock-mock-1-m.json"), "--json"])
    assert gate.exit_code == 1
    assert json.loads(gate.output)["ok"] is False
    out = tmp_path / "regen.jsonl"
    assert runner.invoke(app, ["security", "author", "--out", str(out)]).exit_code == 0
    assert out.read_text() == (REPO / "evaluation/security/0.1/tasks.jsonl").read_text()


def test_economics_and_release_commands(tmp_path):
    r = runner.invoke(app, ["economics", "report", "--out", str(tmp_path / "econ.md")])
    assert r.exit_code == 0, r.output
    assert "Break-even volume" in r.output
    assert (tmp_path / "econ.md").exists()
    assert json.loads(runner.invoke(app, ["economics", "report", "--json"]).output)["kill_signal"] is False
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    r = runner.invoke(
        app,
        [
            "release",
            "check",
            "missing-model",
            "--root",
            str(tmp_path),
            "--config",
            str(tmp_path / "configs/release/zara-v0.yaml"),
        ],
    )
    assert r.exit_code == 1
    assert "unknown model" in r.output
    r = runner.invoke(
        app, ["release", "canary", "--root", str(tmp_path), "--config", str(tmp_path / "configs/release/zara-v0.yaml")]
    )
    assert r.exit_code == 0
    assert "canary protea-agent = 0%" in r.output
    r = runner.invoke(
        app,
        [
            "release",
            "canary",
            "--step",
            "--root",
            str(tmp_path),
            "--config",
            str(tmp_path / "configs/release/zara-v0.yaml"),
        ],
    )
    assert r.exit_code == 1
    assert "promote one before raising the canary" in r.output
    metrics = tmp_path / "summary.json"
    metrics.write_text(
        json.dumps(
            {
                "by_route": [
                    {
                        "key": "protea-agent",
                        "calls": 300,
                        "validation_pass_rate": 0.5,
                        "fallback_rate": 0.4,
                        "latency_ms_p95": 100,
                    }
                ]
            }
        )
    )
    r = runner.invoke(
        app,
        [
            "release",
            "watch",
            str(metrics),
            "--root",
            str(tmp_path),
            "--config",
            str(tmp_path / "configs/release/zara-v0.yaml"),
        ],
    )
    assert r.exit_code == 1
    assert "validation pass rate 0.50" in r.output
    r = runner.invoke(
        app,
        [
            "release",
            "watch",
            str(metrics),
            "--route",
            "other",
            "--root",
            str(tmp_path),
            "--config",
            str(tmp_path / "configs/release/zara-v0.yaml"),
        ],
    )
    assert r.exit_code == 0


def test_loadtest_refuses_remote_without_confirm():
    r = runner.invoke(app, ["serve", "loadtest", "--url", "https://facade.example.com", "--requests", "1"])
    assert r.exit_code == 1
    assert "--confirm" in r.output
