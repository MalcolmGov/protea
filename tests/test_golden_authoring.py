"""Authoring from a dataset build, sealing, verification and the hold-out guard — on the fixture sources and on the
committed ZaraBench 0.1 task set."""

import shutil
from pathlib import Path

import yaml

from protea.config import load_config
from protea.data_pipeline.build import build_dataset
from protea.data_pipeline.sources import DatasetBuildConfig
from protea.evaluation.authoring import AuthoringSpec, author_tasks, expect_from_eval
from protea.evaluation.golden import held_out_families, load_lock, seal, verify
from protea.evaluation.reference import ReferenceProvider
from protea.evaluation.runner import run_benchmark
from protea.evaluation.tasks import Category, load_tasks, task_stats, write_tasks
from protea.schemas.examples import Split, TrainingExample, iter_examples

REPO = Path(__file__).resolve().parent.parent


def _fixture_build(tmp_path: Path) -> tuple[Path, DatasetBuildConfig, Path]:
    root = tmp_path / "protea"
    shutil.copytree(REPO / "tests" / "fixtures", root / "tests" / "fixtures")
    (root / "registry").mkdir()
    (root / "registry" / "datasets.json").write_text("[]")
    cfg_path = root / "tests/fixtures/dataset-fixture.yaml"
    cfg = DatasetBuildConfig.model_validate(yaml.safe_load(cfg_path.read_text()))
    report = build_dataset(cfg, root)
    assert report.output_dir
    return root, cfg, Path(report.output_dir)


async def test_author_seal_verify_on_fixture_build(tmp_path: Path):
    root, cfg, build_dir = _fixture_build(tmp_path)
    tasks = author_tasks(build_dir, AuthoringSpec(seed=1))
    assert tasks
    categories = {t.category for t in tasks}
    assert Category.AGENT_GENERATION in categories
    assert all(t.reference is not None for t in tasks)
    eval_cfg = load_config(REPO / "configs/evaluation/zarabench-0.1.yaml", "evaluation")
    report = await run_benchmark(eval_cfg, tasks, ReferenceProvider(tasks))
    assert all(r.passed for r in report.results), [r.task_id for r in report.results if not r.passed]

    tasks_path = root / "evaluation/tasks.jsonl"
    lock_path = root / "evaluation/golden.lock"
    write_tasks(tasks, tasks_path)
    lock = seal("zarabench", "0.0.1", tasks_path, lock_path, repo_root=root)
    assert lock.count == len(tasks)
    assert lock.held_out_families
    assert verify(lock_path, repo_root=root, min_tasks=1) == []
    problems = verify(lock_path, repo_root=root, min_tasks=10_000)
    assert any("< required" in p for p in problems)
    tasks_path.write_text(tasks_path.read_text() + "\n")
    assert any("hash" in p for p in verify(lock_path, repo_root=root, min_tasks=1))
    assert held_out_families(lock_path) == set(lock.held_out_families)
    assert held_out_families(root / "missing.lock") == set()

    # a rebuild that honours the lock never trains on a sealed family
    cfg2 = cfg.model_copy(update={"holdout_lock": "evaluation/golden.lock", "output_dir": "build/fixture2"})
    report = build_dataset(cfg2, root)
    sealed = set(lock.held_out_families)
    for name in ("train.jsonl", "validation.jsonl"):
        for _, line in iter_examples(Path(report.output_dir) / name):
            ex = TrainingExample.model_validate_json(line)
            assert ex.metadata.family not in sealed, ex.metadata.family
            assert ex.metadata.split in (Split.TRAIN, Split.VALIDATION)


def test_expect_from_eval_maps_the_estate_grammar():
    e = expect_from_eval(
        {
            "tool": "get_deadlines",
            "says_any": ["7th"],
            "says_none": ["i don't know"],
            "refuses": False,
            "lang": "af",
            "no_long_reply": True,
        }
    )
    assert e.tool == "get_deadlines"
    assert e.refuses is False
    assert e.lang == "af"
    assert e.max_words == 60
    assert e.needs_judge()


async def test_committed_zarabench_task_set_is_sealed_and_self_consistent():
    cfg = load_config(REPO / "configs/evaluation/zarabench-0.1.yaml", "evaluation")
    assert cfg.tasks_path and cfg.lock_path
    assert verify(REPO / cfg.lock_path, repo_root=REPO) == []
    tasks = load_tasks(REPO / cfg.tasks_path)
    stats = task_stats(tasks)
    assert stats["total"] >= 150
    assert set(stats["by_category"]) == {c.value for c in Category}
    assert stats["with_reference"] == stats["total"]
    assert load_lock(REPO / cfg.lock_path).held_out_families == stats["families"]
    report = await run_benchmark(cfg.model_copy(update={"concurrency": 16}), tasks, ReferenceProvider(tasks))
    assert report.zarascore == 1.0
    assert all(r.passed for r in report.results), [r.task_id for r in report.results if not r.passed]
