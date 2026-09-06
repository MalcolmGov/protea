import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "protea"
    shutil.copytree(REPO / "tests" / "fixtures", root / "tests" / "fixtures")
    (root / "registry").mkdir()
    (root / "registry" / "datasets.json").write_text("[]")
    return root


def test_dataset_build_dry_run_and_full(tmp_path):
    root = _root(tmp_path)
    cfg = root / "tests/fixtures/dataset-fixture.yaml"
    dry = runner.invoke(app, ["dataset", "build", "--config", str(cfg), "--dry-run", "--root", str(root)])
    assert dry.exit_code == 0, dry.output
    assert "(dry run)" in dry.output
    assert "sources not found locally: ['missing']" in dry.output
    full = runner.invoke(app, ["dataset", "build", "--config", str(cfg), "--root", str(root)])
    assert full.exit_code == 0, full.output
    out = root / "build" / "fixture"
    assert (out / "train.jsonl").exists()
    check = runner.invoke(app, ["dataset", "golden-check", str(out / "train.jsonl"), str(out / "golden.jsonl")])
    assert check.exit_code == 0, check.output
    # a golden example copied into train must be caught
    golden_line = (out / "golden.jsonl").read_text().splitlines()[0]
    (out / "train.jsonl").open("a").write(golden_line + "\n")
    leak = runner.invoke(app, ["dataset", "golden-check", str(out / "train.jsonl"), str(out / "golden.jsonl")])
    assert leak.exit_code == 1


def test_synthesize_requires_confirm_for_paid_providers(tmp_path):
    seeds = tmp_path / "seeds.jsonl"
    seeds.write_text("")
    result = runner.invoke(app, ["dataset", "synthesize", str(seeds), "--provider", "anthropic"])
    assert result.exit_code == 1
    assert "--confirm" in result.output


def test_synthesize_with_mock_provider(tmp_path):
    root = _root(tmp_path)
    cfg = root / "tests/fixtures/dataset-fixture.yaml"
    assert runner.invoke(app, ["dataset", "build", "--config", str(cfg), "--root", str(root)]).exit_code == 0
    seeds = root / "build/fixture/seeds/tool_calling_seeds.jsonl"
    out = tmp_path / "syn.jsonl"
    result = runner.invoke(
        app, ["dataset", "synthesize", str(seeds), "--provider", "mock", "--limit", "5", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "seeds 5" in result.output
    # the mock answers "OK" without tools, so seeds that require a tool are rejected and reported
    assert "rejected" in result.output
    assert out.exists()
    for line in out.read_text().splitlines():
        assert json.loads(line)["metadata"]["synthetic"] is True
