from pathlib import Path

from typer.testing import CliRunner

from protea.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


def test_train_local_dry_run_and_registry_pin():
    cfg = str(REPO / "configs/training/ci-smoke.yaml")
    unpinned = runner.invoke(app, ["train", "local", "--config", cfg, "--root", str(REPO), "--dry-run"])
    assert unpinned.exit_code == 1
    assert "not registered" in unpinned.output
    ok = runner.invoke(
        app, ["train", "local", "--config", cfg, "--root", str(REPO), "--dry-run", "--allow-unregistered"]
    )
    assert ok.exit_code == 0, ok.output
    assert "dry run: nothing created" in ok.output
    assert "tiny-random" in ok.output


def test_train_remote_dry_run_never_launches(tmp_path: Path):
    cfg = str(REPO / "configs/training/ci-smoke.yaml")
    for remote in ("runpod-a100", "ssh-generic", "azure-nc24", "kubernetes"):
        result = runner.invoke(
            app,
            [
                "train",
                "remote",
                "--config",
                cfg,
                "--remote",
                str(REPO / "configs/remote" / f"{remote}.yaml"),
                "--root",
                str(REPO),
                "--out",
                str(tmp_path / "remote"),
                "--allow-unregistered",
                "--run-id",
                "dry",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "execution boundary" in result.output
        assert "dry run: nothing launched" in result.output
        assert "estimated cost" in result.output
    assert (tmp_path / "remote/ci-smoke-runpod/runpod-deploy.graphql").exists()
    assert (tmp_path / "remote/ci-smoke-azure/main.bicep").exists()


def test_train_remote_rejects_unknown_gpu(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("provider: ssh\ngpu: tpu-v9\nstorage: {kind: rsync, uri: x:/y}\nssh: {host: h}\n")
    result = runner.invoke(
        app,
        [
            "train",
            "remote",
            "--config",
            str(REPO / "configs/training/ci-smoke.yaml"),
            "--remote",
            str(bad),
            "--root",
            str(REPO),
            "--allow-unregistered",
        ],
    )
    assert result.exit_code == 1
    assert "unknown gpu" in result.output


def test_train_runs_empty(tmp_path: Path):
    result = runner.invoke(app, ["train", "runs", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no runs" in result.output


def test_roadmap_lists_only_the_launch_boundary_for_phase_4():
    result = runner.invoke(app, ["roadmap"])
    assert "train remote --confirm" in result.output
    assert "train local" not in result.output
