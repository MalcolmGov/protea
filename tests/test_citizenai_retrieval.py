from datetime import timedelta
from pathlib import Path

from protea.citizenai import StubRetrieval, seed_facts
from protea.citizenai.retrieval import _AS_OF
from protea.evaluation.citizen import _DOMAINS


def test_retrieve_returns_a_fact_for_each_seeded_intent():
    svc = StubRetrieval()
    for fact in seed_facts():
        got = svc.retrieve(fact.intent)
        assert got is fact
        assert got.data, f"{fact.intent} has no structured data"
        assert got.source
    assert svc.retrieve("no_such_intent") is None


def test_seed_matches_citizenbench_domains():
    """Retrieval intents must stay in lockstep with the eval's tools, so grounding can't drift from what's served."""
    eval_tools = {tool for (tool, *_rest) in _DOMAINS.values()}
    retrieval_intents = set(StubRetrieval().intents())
    assert retrieval_intents == eval_tools


def test_citation_carries_source_and_as_of_and_is_labelled_sample():
    fact = seed_facts()[0]
    cite = fact.citation()
    assert "sample" in cite  # seeded facts are illustrative until a real connector is wired
    assert fact.source in cite
    assert _AS_OF.isoformat() in cite


def test_staleness_window():
    fact = seed_facts()[0]
    assert fact.is_stale(_AS_OF + timedelta(days=31), max_age_days=30)
    assert not fact.is_stale(_AS_OF + timedelta(days=10), max_age_days=30)
    assert not fact.is_stale(_AS_OF, max_age_days=0)


def test_source_register_lists_every_intent():
    register = Path("docs/citizenai/sources.md").read_text(encoding="utf-8")
    for fact in seed_facts():
        assert f"`{fact.intent}`" in register, f"{fact.intent} missing from docs/citizenai/sources.md"
