import asyncio
from pathlib import Path

from protea.config import load_config
from protea.evaluation.citizen import citizen_tasks, families, languages
from protea.evaluation.golden import verify
from protea.evaluation.reference import ReferenceProvider
from protea.evaluation.runner import run_benchmark
from protea.evaluation.tasks import load_tasks, task_set_hash
from protea.providers.mock import MockProvider
from protea.schemas.examples import LANGUAGE_TAGS

REPO = Path(__file__).resolve().parent.parent
CFG = REPO / "configs/evaluation/citizen-0.1.yaml"


def test_slice_is_sealed_deterministic_and_matches_the_generator():
    cfg = load_config(CFG, "evaluation")
    assert verify(REPO / cfg.lock_path, repo_root=REPO, min_tasks=10) == []
    committed = load_tasks(REPO / cfg.tasks_path)
    generated = citizen_tasks()
    assert [t.id for t in committed] == [t.id for t in generated]
    # deterministic only — a government report must never be partial
    assert not any(t.expect.needs_judge() for t in committed)
    assert all(t.reference is not None for t in committed)
    assert len(task_set_hash(REPO / cfg.tasks_path)) == 64


def test_families_and_languages_are_the_expected_government_set():
    tasks = citizen_tasks()
    assert set(families(tasks)) == {
        "sassa",
        "sars",
        "uif",
        "dha",
        "eskom",
        "municipal",  # grounded government domains
        "confabulation",
        "personal-data",
        "scope",  # anti-hallucination + scope
        "staleness",  # a time-sensitive fact must carry its as-of date
    }
    langs = set(languages(tasks))
    assert {"en-ZA", "af", "zu"} <= langs
    assert langs <= LANGUAGE_TAGS  # every tag is a declared official/verified language


def _run(provider):
    cfg = load_config(CFG, "evaluation")
    tasks = load_tasks(REPO / cfg.tasks_path)
    return asyncio.run(run_benchmark(cfg, tasks, provider, judge=None, run_id="t", config_hash="c", task_set_hash="t"))


def test_reference_scores_perfect_and_is_never_partial():
    ref = _run(ReferenceProvider(_load()))
    assert ref.zarascore_strict == 1.0
    assert ref.partial is False


def test_a_confabulating_model_fails_grounding_and_hallucination():
    # A model that never calls a tool and invents figures should fail the grounded and confabulation families.
    def guesser(request):
        text = " ".join((m.content or "") for m in request.messages).lower()
        if "sassa" in text or "grant" in text or "imali ye-sassa" in text or "toelae" in text:
            return "Yes, you will definitely get R700 every month."
        return "Sure, no problem, it is done."

    out = _run(MockProvider(guesser))
    assert out.zarascore_strict < 0.5
    assert out.partial is False


def _load():
    cfg = load_config(CFG, "evaluation")
    return load_tasks(REPO / cfg.tasks_path)
