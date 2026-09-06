import json
from pathlib import Path

import yaml

from protea.data_pipeline.build import build_dataset
from protea.data_pipeline.dedup import mark_duplicates
from protea.data_pipeline.sources import DatasetBuildConfig
from protea.data_pipeline.splits import golden_leak
from protea.registry import DatasetRegistry
from protea.schemas.examples import Split, TaskType, TrainingExample, iter_examples, validate_jsonl

REPO = Path(__file__).resolve().parent.parent


def _cfg() -> DatasetBuildConfig:
    return DatasetBuildConfig.model_validate(yaml.safe_load((REPO / "tests/fixtures/dataset-fixture.yaml").read_text()))


def _stage(tmp_path: Path) -> Path:
    """Copy fixture sources under a temp protea root so registry/output writes never touch the repo."""
    import shutil

    root = tmp_path / "protea"
    shutil.copytree(REPO / "tests" / "fixtures", root / "tests" / "fixtures")
    (root / "registry").mkdir()
    (root / "registry" / "datasets.json").write_text("[]")
    return root


def test_dry_run_counts_without_writing(tmp_path):
    root = _stage(tmp_path)
    report = build_dataset(_cfg(), root, dry_run=True)
    assert report.dry_run is True
    assert report.output_dir is None
    assert not (root / "build").exists()
    assert report.packages == 5  # 4 aria packages (index.json is not a package glob match) + 1 authored
    assert [s["exists"] for s in report.sources] == [True, True, False]
    assert report.secret_findings >= 1
    assert any("leaky" in r["artifact"] for r in report.rejected)


def test_full_build_writes_splits_manifest_card_and_registers(tmp_path):
    root = _stage(tmp_path)
    cfg = _cfg()
    report = build_dataset(cfg, root)
    out = Path(report.output_dir)
    for name in ("train", "validation", "test", "golden"):
        rep = validate_jsonl(out / f"{name}.jsonl")
        assert rep.errors == [], (name, rep.errors)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["files"]["train"]["sha256"]
    assert (out / "DATASET_CARD.md").read_text().startswith("# Dataset card — fixture-training-0.0.1")
    reg = DatasetRegistry(root / "registry" / "datasets.json")
    entry = reg.get("fixture-training-0.0.1")
    assert entry is not None
    assert entry.sources[0].commit == "unresolved" or len(entry.sources[0].commit) > 0
    # every task type we expect from the fixtures is present
    assert {
        TaskType.AGENT_GENERATION.value,
        TaskType.STRUCTURED_OUTPUT.value,
        TaskType.CONNECTOR_SELECTION.value,
        TaskType.ROUTING.value,
    } <= set(report.examples_by_task)
    # the leaky package produced no examples
    all_examples = [
        TrainingExample.model_validate_json(line)
        for name in ("train", "validation", "test", "golden")
        for _, line in iter_examples(out / f"{name}.jsonl")
    ]
    assert not any(ex.metadata.source_id == "asia-leaky" for ex in all_examples)
    assert not any("sk-ant-" in (m.content or "") for ex in all_examples for m in ex.messages)


def test_scrub_and_redaction_reach_targets(tmp_path):
    root = _stage(tmp_path)
    report = build_dataset(_cfg(), root)
    out = Path(report.output_dir)
    texts = []
    for name in ("train", "validation", "test", "golden"):
        for _, line in iter_examples(out / f"{name}.jsonl"):
            ex = TrainingExample.model_validate_json(line)
            texts.append(" ".join(m.content or "" for m in ex.messages))
            if ex.metadata.source_id == "africa-salon-booking" and ex.metadata.task_type == TaskType.AGENT_GENERATION:
                assert ex.metadata.redactions.get("phone", 0) >= 1
    blob = "\n".join(texts)
    assert "MyInstantAI" not in blob
    assert "071 234 5678" not in blob
    assert "desk@salon.example.com" in blob  # placeholder domains are kept
    assert "8001015009087" not in blob
    assert report.scrubbed >= 1


def test_seeds_apply_rule_checks_and_contamination(tmp_path):
    root = _stage(tmp_path)
    report = build_dataset(_cfg(), root)
    seeds = [json.loads(line) for _, line in iter_examples(Path(report.files["seeds"]))]
    ids = {s["seed_id"] for s in seeds}
    assert "africa-salon-booking:list" in ids
    assert "africa-salon-booking:bad-tool" not in ids  # unknown tool
    assert "africa-salon-booking:klingon" not in ids  # unknown language tag
    assert "order-desk:afr" in ids  # authored source, af is an allowed tag
    pharm = [s for s in seeds if s["agent_id"] == "eu-pharmacy"]
    assert any(s["contaminated"] for s in pharm if s["seed_id"].endswith(":e1"))
    assert report.seeds_rejected >= 2


def test_family_split_and_golden_are_leak_free(tmp_path):
    root = _stage(tmp_path)
    report = build_dataset(_cfg(), root)
    out = Path(report.output_dir)
    by_split: dict[str, list[TrainingExample]] = {}
    for name in ("train", "validation", "test", "golden"):
        by_split[name] = [TrainingExample.model_validate_json(line) for _, line in iter_examples(out / f"{name}.jsonl")]
    # africa- and us- salon-booking share a family and must land in the same split for a given task type
    placement = {}
    for name, items in by_split.items():
        for ex in items:
            if ex.metadata.family == "salon-booking":
                placement.setdefault(ex.metadata.task_type.value, set()).add("test" if name == "golden" else name)
    assert all(len(v) == 1 for v in placement.values()), placement
    train_ids = {e.metadata.id for e in by_split["train"]}
    golden_ids = {e.metadata.id for e in by_split["golden"]}
    fam = lambda items: {f"{e.metadata.task_type.value}:{e.metadata.family}" for e in items if e.metadata.family}  # noqa: E731
    assert golden_leak(train_ids, golden_ids, fam(by_split["train"]), fam(by_split["golden"])) == []
    assert all(e.metadata.split == Split.GOLDEN for e in by_split["golden"])
    assert report.golden == len(golden_ids)


def test_dedup_marks_near_identical_targets(tool_example):
    a = tool_example.model_copy(deep=True)
    b = tool_example.model_copy(deep=True)
    b.metadata.id = "other"
    c = tool_example.model_copy(deep=True)
    c.metadata.id = "different"
    c.messages[-1].content = "Completely different reply about pharmacy opening hours on Saturday morning at eight."
    assert mark_duplicates([a, b, c], 0.9) == 1
    assert b.metadata.duplicate_of == a.metadata.id
    assert c.metadata.duplicate_of is None
