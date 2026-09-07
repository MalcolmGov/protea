import json
from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def test_evaluate_tasks_and_verify():
    result = runner.invoke(app, ["evaluate", "tasks", "--root", str(REPO)])
    assert result.exit_code == 0, result.output
    assert "tasks      " in result.output
    assert "families" in result.output
    result = runner.invoke(app, ["evaluate", "verify", "--root", str(REPO)])
    assert result.exit_code == 0, result.output


def test_evaluate_run_reference_and_compare(tmp_path: Path):
    out = tmp_path / "reports"
    ref = runner.invoke(
        app,
        [
            "evaluate",
            "run",
            "--provider",
            "reference",
            "--root",
            str(REPO),
            "--out",
            str(out),
            "--label",
            "ref",
        ],
    )
    assert ref.exit_code == 0, ref.output
    assert "ZaraScore 1.0000" in ref.output
    mock = runner.invoke(
        app,
        [
            "evaluate",
            "run",
            "--provider",
            "mock",
            "--root",
            str(REPO),
            "--out",
            str(out),
            "--label",
            "mock",
            "--limit",
            "40",
        ],
    )
    assert mock.exit_code == 0, mock.output
    reports = sorted(p.name for p in (out / "zarabench-0.1.1").iterdir())
    assert reports == [
        "mock-mock-1-mock.json",
        "mock-mock-1-mock.md",
        "reference-reference-ref.json",
        "reference-reference-ref.md",
    ]

    cmp = runner.invoke(
        app,
        [
            "evaluate",
            "compare",
            str(out / "zarabench-0.1.1/mock-mock-1-mock.json"),
            "--frontier",
            str(out / "zarabench-0.1.1/reference-reference-ref.json"),
            "--json",
        ],
    )
    assert cmp.exit_code == 2, cmp.output
    payload = json.loads(cmp.output)
    assert payload["decision"]["release"] is False
    assert payload["decision"]["kill_recommended"] is True


def test_evaluate_run_paid_provider_requires_confirm():
    result = runner.invoke(app, ["evaluate", "run", "--provider", "anthropic", "--root", str(REPO), "--limit", "3"])
    assert result.exit_code == 1
    assert "execution boundary" in result.output
    assert "--confirm" in result.output


def test_evaluate_run_category_filter_with_no_match():
    result = runner.invoke(app, ["evaluate", "run", "--provider", "mock", "--root", str(REPO), "--categories", "nope"])
    assert result.exit_code == 1
    assert "no tasks selected" in result.output


def test_roadmap_no_longer_lists_evaluation_as_planned():
    result = runner.invoke(app, ["roadmap"])
    assert "Phase 3" in result.output  # only the GPU baseline remains a boundary
    assert "evaluate / benchmark" not in result.output


def test_split_judge_accepts_provider_with_optional_model():
    from protea.cli_evaluate import _split_judge

    assert _split_judge(None) == (None, None)
    assert _split_judge("anthropic") == ("anthropic", None)
    assert _split_judge("anthropic:claude-opus-5") == ("anthropic", "claude-opus-5")
