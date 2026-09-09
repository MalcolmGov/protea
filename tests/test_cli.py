from typer.testing import CliRunner

from protea import __version__, doctor
from protea.cli import app

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_doctor_runs_and_reports_core_checks():
    report = doctor.run()
    names = {c.name for c in report.checks}
    assert {"python", "gpu", "cuda", "memory", "disk", "inference_server"} <= names


def test_doctor_json_output_is_valid():
    import json

    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert "checks" in json.loads(result.output)


def test_train_local_exposes_eval_flag():
    # `train local --eval` is what the remote entrypoint uses to self-score the adapter after training.
    # Introspect the registered command's options rather than parsing rendered --help (which rich wraps
    # and ellipsises at narrow terminal widths, differing between local and CI).
    from typer.main import get_command

    local_cmd = get_command(app).commands["train"].commands["local"]
    opts = {opt for param in local_cmd.params for opt in getattr(param, "opts", [])}
    assert "--eval" in opts
