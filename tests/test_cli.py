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


def test_dataset_review_sorts_rows_and_annotates(tmp_path):
    # Two rows: one clean, one carrying a secret. The command must exit non-zero (a blocked row) and the
    # annotated copy must stamp review_status accordingly.
    import json

    clean = {
        "metadata": {"dataset_version": "0.2.0-synthetic", "source_type": "synthetic", "family": "acme",
                     "task_type": "tool_calling", "synthetic": True, "generator_model": "anthropic:claude-sonnet-5"},
        "messages": [{"role": "user", "content": "status?"},
                     {"role": "assistant", "content": "Shipping tomorrow.",
                      "tool_calls": [{"id": "c1", "name": "get_order_status", "arguments": {}}]}],
        "tools": [],
    }
    leaky = json.loads(json.dumps(clean))
    leaky["messages"][1]["content"] = "token sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV"

    src = tmp_path / "synthetic.jsonl"
    src.write_text(json.dumps(clean) + "\n" + json.dumps(leaky) + "\n", encoding="utf-8")
    out = tmp_path / "reviewed.jsonl"

    result = runner.invoke(app, ["dataset", "review", str(src), "--out", str(out)])
    assert result.exit_code != 0  # a blocked (secret) row fails the gate
    assert "blocked 1" in result.output

    lines = [json.loads(x) for x in out.read_text().splitlines()]
    statuses = {ln["messages"][1]["content"][:5]: ln["metadata"]["review_status"] for ln in lines}
    assert statuses["Shipp"] == "pending"   # clean, but not auto-approved without --approve-clean
    assert statuses["token"] == "rejected"  # the secret row


def test_train_local_exposes_eval_flag():
    # `train local --eval` is what the remote entrypoint uses to self-score the adapter after training.
    # Introspect the registered command's options rather than parsing rendered --help (which rich wraps
    # and ellipsises at narrow terminal widths, differing between local and CI).
    from typer.main import get_command

    local_cmd = get_command(app).commands["train"].commands["local"]
    opts = {opt for param in local_cmd.params for opt in getattr(param, "opts", [])}
    assert "--eval" in opts
