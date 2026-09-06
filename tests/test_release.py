import asyncio
import hashlib
import shutil
from pathlib import Path

import pytest

from protea.config import load_config
from protea.evaluation.reference import ReferenceProvider
from protea.evaluation.report import write_report
from protea.evaluation.runner import run_benchmark
from protea.evaluation.tasks import load_tasks
from protea.registry import DatasetRegistry, ModelRegistry, RegistryError
from protea.release import Evidence, ReleasePipeline, RollbackTriggers, should_rollback
from protea.schemas.registry import DatasetEntry, ModelEntry, ModelStatus, SourceRef

REPO = Path(__file__).resolve().parent.parent


def _report(
    root: Path, cfg_path: str, tasks_path: str, *, provider: str, model: str, run_id: str, partial=False, scale=1.0
):
    cfg = load_config(root / cfg_path, "evaluation")
    tasks = load_tasks(root / tasks_path)[:40]
    rep = asyncio.run(
        run_benchmark(
            cfg, tasks, ReferenceProvider(tasks), judge=None, run_id=run_id, config_hash="c", task_set_hash="t"
        )
    )
    rep.provider, rep.model, rep.partial = provider, model, partial
    for c in rep.categories:
        c.score = round(c.score * scale, 3)
        c.pass_rate = round(c.pass_rate * scale, 3)
    rep.zarascore = round(rep.zarascore * scale, 4)
    rep.zarascore_strict = round(rep.zarascore_strict * scale, 4)
    return write_report(rep, root / "evaluation/reports", cfg)[0]


@pytest.fixture
def root(tmp_path):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    (tmp_path / "evaluation").mkdir()
    shutil.copytree(REPO / "evaluation/zarabench", tmp_path / "evaluation/zarabench")
    shutil.copytree(REPO / "evaluation/security", tmp_path / "evaluation/security")
    (tmp_path / "data").mkdir()
    train = tmp_path / "data/train.jsonl"
    train.write_text('{"x": 1}\n')
    DatasetRegistry(tmp_path / "registry/datasets.json").add(
        DatasetEntry(
            name="agent-training",
            version="0.1.0",
            path="data/train.jsonl",
            sha256=hashlib.sha256(train.read_bytes()).hexdigest(),
            splits={"train": 1},
            sources=[SourceRef(repo="MalcolmGov/aria", commit="c22c31b")],
        )
    )
    (tmp_path / "cards").mkdir()
    (tmp_path / "cards/m.md").write_text("# card\n")
    return tmp_path


def _entry(version="0.1.0", **kw) -> ModelEntry:
    base = dict(
        family="protea-agent",
        version=version,
        base_model="Qwen/Qwen3-8B",
        training_dataset="agent-training-0.1.0",
        checkpoint_uri="s3://ckpt",
        artifact_sha256="a" * 64,
        git_commit="abc",
        model_card_path="cards/m.md",
    )
    base.update(kw)
    return ModelEntry(**base)


def _pipeline(root: Path) -> ReleasePipeline:
    return ReleasePipeline(load_config(root / "configs/release/zara-v0.yaml", "release"), root)


def test_check_blocks_without_evidence_and_explains(root):
    ModelRegistry(root / "registry/models.json").add(_entry(checkpoint_uri=None, model_card_path=None))
    report = _pipeline(root).check("protea-agent-0.1.0")
    stages = {c.stage: c for c in report.checks}
    assert not stages["train"].ok
    assert "checkpoint_uri" in stages["train"].detail
    assert not stages["validate"].ok
    assert "model card" in stages["validate"].detail
    assert not stages["zarabench"].ok
    assert "no ZaraBench report" in stages["zarabench"].detail
    assert not stages["security"].ok
    assert stages["compare_production"].ok
    assert report.next_step is None
    assert len(report.blockers) == 4
    pipe = _pipeline(root)
    with pytest.raises(RegistryError, match="evidence supports nothing"):
        pipe.promote("protea-agent-0.1.0", "candidate")


def test_partial_or_weak_reports_block_promotion(root):
    ModelRegistry(root / "registry/models.json").add(_entry())
    zb = _report(
        root,
        "configs/evaluation/zarabench-0.1.yaml",
        "evaluation/zarabench/0.1/tasks.jsonl",
        provider="protea",
        model="protea-agent-0.1.0",
        run_id="p",
        partial=True,
    )
    sec = _report(
        root,
        "configs/evaluation/security-0.1.yaml",
        "evaluation/security/0.1/tasks.jsonl",
        provider="protea",
        model="protea-agent-0.1.0",
        run_id="s",
        scale=0.5,
    )
    report = _pipeline(root).check("protea-agent-0.1.0", Evidence(zarabench=zb, security=sec))
    stages = {c.stage: c for c in report.checks}
    assert "partial" in stages["zarabench"].detail
    assert "strict score 0.500" in stages["security"].detail
    assert report.next_step is None


def test_full_promotion_canary_and_rollback(root):
    models = ModelRegistry(root / "registry/models.json")
    models.add(_entry())
    _report(
        root,
        "configs/evaluation/zarabench-0.1.yaml",
        "evaluation/zarabench/0.1/tasks.jsonl",
        provider="protea",
        model="protea-agent-0.1.0",
        run_id="p",
    )
    _report(
        root,
        "configs/evaluation/zarabench-0.1.yaml",
        "evaluation/zarabench/0.1/tasks.jsonl",
        provider="anthropic",
        model="claude-sonnet-5",
        run_id="f",
        scale=0.9,
    )
    _report(
        root,
        "configs/evaluation/security-0.1.yaml",
        "evaluation/security/0.1/tasks.jsonl",
        provider="protea",
        model="protea-agent-0.1.0",
        run_id="s",
    )
    pipe = _pipeline(root)
    report = pipe.check("protea-agent-0.1.0")
    assert report.blockers == [], report.blockers
    assert report.next_step == "candidate"
    with pytest.raises(RegistryError, match="evidence supports candidate"):
        pipe.promote("protea-agent-0.1.0", "staging")
    with pytest.raises(RegistryError, match="promote one before raising the canary"):
        pipe.canary_step()
    assert pipe.promote("protea-agent-0.1.0", "candidate").next_step == "staging"
    assert pipe.promote("protea-agent-0.1.0", "staging").next_step == "canary"
    after = pipe.promote("protea-agent-0.1.0", "canary")
    assert after.canary_percent == 5.0
    assert after.next_step == "canary"
    assert pipe.canary_step() == 25.0
    assert pipe.canary_step() == 100.0
    with pytest.raises(RegistryError, match="already at 100"):
        pipe.canary_step()
    assert pipe.check("protea-agent-0.1.0").next_step == "production"
    final = pipe.promote("protea-agent-0.1.0", "production")
    assert final.status == "production"
    assert {c.stage: c.ok for c in final.checks}["production"] is True
    policy_text = (root / "configs/routing/zara-v0.yaml").read_text()
    assert "canary_percent: 100" in policy_text
    assert "# raise in steps" in policy_text  # comments survive the edit
    # a successor regressing a priority category cannot replace production
    models = ModelRegistry(root / "registry/models.json")
    models.add(_entry(version="0.2.0"))
    _report(
        root,
        "configs/evaluation/zarabench-0.1.yaml",
        "evaluation/zarabench/0.1/tasks.jsonl",
        provider="protea",
        model="protea-agent-0.2.0",
        run_id="p2",
        scale=0.8,
    )
    _report(
        root,
        "configs/evaluation/security-0.1.yaml",
        "evaluation/security/0.1/tasks.jsonl",
        provider="protea",
        model="protea-agent-0.2.0",
        run_id="s2",
    )
    pipe = _pipeline(root)
    weak = pipe.check("protea-agent-0.2.0")
    stages = {c.stage: c for c in weak.checks}
    assert not stages["zarabench"].ok  # below the frontier gate category
    assert not stages["compare_production"].ok
    assert "regresses production" in stages["compare_production"].detail
    # rollback: canary to zero, production demoted; nothing to reinstate yet
    out = pipe.rollback(reason="p95 breach")
    assert out == {"canary_percent": 0.0, "demoted": "protea-agent-0.1.0", "reinstated": None}
    assert "canary_percent: 0" in (root / "configs/routing/zara-v0.yaml").read_text()
    assert pipe.models.get("protea-agent-0.1.0").deployment_status == ModelStatus.DEPRECATED
    log = (root / "registry/release-log.jsonl").read_text().splitlines()
    assert [__import__("json").loads(line)["event"] for line in log][-2:] == ["canary", "rollback"]


def test_rollback_reinstates_previous_production(root):
    models = ModelRegistry(root / "registry/models.json")
    models.add(_entry(version="0.1.0"))
    models.add(_entry(version="0.2.0"))
    for key in ("protea-agent-0.1.0", "protea-agent-0.2.0"):
        models.transition(key, ModelStatus.CANDIDATE)
        models.transition(key, ModelStatus.STAGING)
    models.transition("protea-agent-0.1.0", ModelStatus.PRODUCTION)
    models.transition("protea-agent-0.1.0", ModelStatus.DEPRECATED, reason="replaced")
    models.transition("protea-agent-0.2.0", ModelStatus.PRODUCTION)
    pipe = _pipeline(root)
    pipe.set_canary(25.0)
    out = pipe.rollback(reason="fallback rate")
    assert out["demoted"] == "protea-agent-0.2.0"
    assert out["reinstated"] == "protea-agent-0.1.0"
    assert pipe.models.production("protea-agent").key == "protea-agent-0.1.0"
    assert pipe.canary_percent() == 0.0


def test_rollback_triggers():
    trig = RollbackTriggers()
    assert should_rollback({"calls": 10, "fallback_rate": 0.9}, trig) == []  # inconclusive window
    ok = {"calls": 200, "validation_pass_rate": 0.97, "fallback_rate": 0.02, "latency_ms_p95": 1200, "error_rate": 0.0}
    assert should_rollback(ok, trig) == []
    bad = {"calls": 200, "validation_pass_rate": 0.8, "fallback_rate": 0.3, "latency_ms_p95": 9000, "error_rate": 0.05}
    reasons = should_rollback(bad, trig, feedback_negative_share=0.5)
    assert len(reasons) == 5
    assert reasons[0].startswith("validation pass rate 0.80")
