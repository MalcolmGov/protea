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
