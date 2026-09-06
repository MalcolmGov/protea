import json
from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent
REPORTS = Path(__file__).parent / "fixtures" / "reports"


def test_route_explain_and_matrix():
    r = runner.invoke(
        app, ["route", "explain", "tool_calling", "--reports", str(REPORTS), "--tenant", "t1", "--tools", "5"]
    )
    assert r.exit_code == 0, r.output
    assert "route        frontier-sonnet" in r.output  # canary_percent is 0 in the committed policy
    assert "outside canary share" in r.output
    r = runner.invoke(app, ["route", "explain", "chat", "--privacy", "strict"])
    assert r.exit_code == 1
    assert "no eligible route" in r.output
    r = runner.invoke(app, ["route", "explain", "structured_output", "--reports", str(REPORTS), "--json"])
    assert r.exit_code == 0
    assert json.loads(r.output)["route"] == "frontier-sonnet"
    r = runner.invoke(app, ["route", "matrix", "--reports", str(REPORTS)])
    assert r.exit_code == 0
    assert "protea:protea-agent" in r.output
    assert "mock" not in r.output
    r = runner.invoke(app, ["route", "matrix", "--reports", str(REPO / "does-not-exist")])
    assert "no benchmark reports" in r.output


def test_serve_facade_check_mounts_router(monkeypatch, tmp_path):
    monkeypatch.setenv("PROTEA_FACADE_TOKEN", "t")
    cfg = tmp_path / "facade.yaml"
    cfg.write_text(
        "backend: mock\nrouting_policy: configs/routing/zara-v0.yaml\nroute_events: "
        + str(tmp_path / "events.jsonl")
        + "\n",
        encoding="utf-8",
    )
    from protea.cli_serve import build_facade

    _, _, facade = build_facade(cfg)
    assert facade.state.facade.router is not None
    assert "/v1/route/generate" in facade.openapi()["paths"]
    r = runner.invoke(app, ["config", "validate", str(REPO / "configs/routing/zara-v0.yaml")])
    assert r.exit_code == 0, r.output
