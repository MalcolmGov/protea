from pathlib import Path

from protea.citizenai.dataset import citizen_training_examples, families
from protea.evaluation.citizen import citizen_tasks
from protea.schemas.examples import LANGUAGE_TAGS, Split, TaskType

REPO = Path(__file__).resolve().parent.parent


def _examples():
    return citizen_training_examples()


def test_examples_are_valid_and_never_golden():
    ex = _examples()
    assert len(ex) >= 20
    for e in ex:
        assert e.messages[-1].role == "assistant"  # last turn is the training target
        assert e.metadata.split in (Split.TRAIN, Split.VALIDATION)  # never golden — golden is the eval hold-out
        assert e.metadata.task_type == TaskType.TOOL_CALLING
        assert e.metadata.synthetic and e.metadata.generator_model  # provenance recorded


def test_v0_is_en_za_only_verified_language():
    langs = {e.metadata.language for e in _examples()}
    assert langs == {"en-ZA"}
    assert langs <= LANGUAGE_TAGS


def test_families_cover_the_government_behaviours():
    fam = set(families(_examples()))
    assert {"sassa", "sars", "uif", "dha", "eskom", "municipal"} <= fam  # grounded domains
    assert {"staleness", "confabulation", "personal-data", "scope"} <= fam  # refusal / grounding behaviours


def test_both_splits_are_populated():
    ex = _examples()
    assert any(e.metadata.split == Split.TRAIN for e in ex)
    assert any(e.metadata.split == Split.VALIDATION for e in ex)


def test_grounded_answer_surfaces_the_retrieved_anchor():
    # a SASSA grounded example must call the tool, get a result, and cite R370 in the final answer
    sassa = [e for e in _examples() if e.metadata.family == "sassa"][0]
    roles = [m.role for m in sassa.messages]
    assert "tool" in roles  # it grounded on a tool result
    assert "R370" in (sassa.messages[-1].content or "")


def test_personal_and_scope_never_call_a_tool_or_fabricate():
    for e in _examples():
        if e.metadata.family in ("personal-data", "scope"):
            assert all(not m.tool_calls for m in e.messages)  # no tool call
            final = e.messages[-1].content or ""
            assert final  # a real refusal / ask, not empty
    personal = [e for e in _examples() if e.metadata.family == "personal-data"][0]
    assert "13" in (personal.messages[-1].content or "")  # asks for the 13-digit ID


def test_training_questions_do_not_leak_the_sealed_eval():
    """The eval is a hold-out: no training question may be identical to a sealed CitizenBench question."""
    train_q = {m.content for e in _examples() for m in e.messages if m.role == "user"}
    eval_q = {m.content for t in citizen_tasks() for m in t.messages if m.role == "user"}
    assert train_q.isdisjoint(eval_q)
