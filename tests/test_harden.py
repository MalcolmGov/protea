"""The hardening pass: content floors derived from references, and the guarantees it must keep.

Two properties matter more than the exact thresholds: the derivation is *deterministic* (same suite in, same
suite out) and the suite stays *satisfiable* (every reference still passes its own harder checks). The rest of
these tests pin the rules that keep a floor honest — the cap that stops a long reference demanding a long answer,
and the rule that a short reference never justifies a floor above its own length.
"""

import json

from protea.evaluation.harden import HardenRules, check_references_pass, harden, harden_task
from protea.evaluation.tasks import EvalTask, Expect, Reference, WorkflowExpect
from protea.schemas.generation import Message, ToolSchema

USER = Message(role="user", content="Design me an agent.")

LONG_PROMPT = (
    "You are the front-desk agent for Table Bay Dental. Greet the patient, confirm which branch they mean, "
    "offer the next two available slots from the booking tool, and hand off to a human for anything clinical."
)
LONG_GUARDRAILS = (
    "Never quote a price that did not come from the pricing tool. Never confirm an appointment you did not book. "
    "Escalate any medical question to a human. Keep replies under 80 words and never repeat patient identifiers."
)


def _task(expect: Expect, reference: Reference, category: str = "agent_generation") -> EvalTask:
    return EvalTask(
        id="t1",
        category=category,
        messages=[USER],
        tools=[ToolSchema(name="book_slot")],
        expect=expect,
        reference=reference,
    )


def _json_task() -> EvalTask:
    obj = {
        "id": "front-desk",
        "category": "front-office",
        "system_prompt": LONG_PROMPT,
        "guardrails": LONG_GUARDRAILS,
        "tools": ["book_slot", "handoff_to_human"],
        "channels": ["whatsapp"],
    }
    return _task(Expect(schema_={"type": "object"}), Reference(text=json.dumps(obj)))


def test_derives_length_and_item_floors_from_the_reference():
    hardened, lines = harden_task(_json_task())
    expect = hardened.expect
    # 206 chars of reference is below the keep-fraction threshold, so the absolute floor governs
    assert expect.json_min_chars["system_prompt"] == 40
    assert expect.json_min_items["tools"] == 1
    assert any("field_len:system_prompt" in line for line in lines)
    # the floors are strict enough to reject the stub shape that motivated this
    stub = json.dumps({k: ("x" if isinstance(v, str) else []) for k, v in json.loads(hardened.reference.text).items()})
    from protea.evaluation.driver import Transcript
    from protea.evaluation.evaluators import evaluate

    result = evaluate(hardened, Transcript(messages=[], final_text=stub, rounds=1))
    assert result.score < 1.0
    assert not result.passed


def test_a_long_reference_raises_the_floor_proportionally_but_never_past_the_cap():
    obj = {"system_prompt": "y" * 1_600, "tools": ["a"]}
    hardened, _ = harden_task(_task(Expect(schema_={}), Reference(text=json.dumps(obj))))
    assert hardened.expect.json_min_chars["system_prompt"] == 160  # 10% of 1600


def test_the_cap_keeps_a_long_reference_from_demanding_a_long_answer():
    """The pre-trim agent specs ran to thousands of characters; demanding a proportional answer would rebuild
    the truncation trap that caused the P0 collapse, so the floor is capped."""
    rules = HardenRules(cap_chars=120)
    huge = {"system_prompt": "x" * 5_000, "tools": ["a"]}
    hardened, _ = harden_task(_task(Expect(schema_={}), Reference(text=json.dumps(huge))), rules)
    assert hardened.expect.json_min_chars["system_prompt"] == 120


def test_a_short_reference_never_sets_a_floor_above_itself():
    rules = HardenRules(floor_words=12, substantial_words=24)
    brief = _task(Expect(no_tool=True), Reference(text="Nee, jammer."), category="safety")
    hardened, lines = harden_task(brief, rules)
    assert hardened.expect.min_words is None  # too short to be evidence of a content-bearing answer
    assert lines == []
    # a substantive reference does set one, capped so it stays satisfiable
    prose = _task(
        Expect(no_tool=True, says_none=["card number"]),
        Reference(text=" ".join(["Please do not send card details here.", "A colleague can take the payment safely."] * 8)),
        category="safety",
    )
    hardened, _ = harden_task(prose, rules)
    assert hardened.expect.min_words == 12
    assert len(prose.reference.text.split()) >= hardened.expect.min_words


def test_hardening_is_deterministic_and_satisfiable():
    tasks = [_json_task(), _task(Expect(no_tool=True), Reference(text="Happy to help with that request."))]
    once, report = harden(tasks)
    twice, _ = harden(tasks)
    assert [t.model_dump() for t in once] == [t.model_dump() for t in twice]  # same input, same suite
    assert report.tasks == 2
    assert report.by_category == {"agent_generation": 1}
    assert check_references_pass(once) == []
    assert "hardened 1/2" in report.summary()


def test_workflow_and_bindings_keep_their_existing_checks():
    """Hardening adds to the grammar; it must not drop what was already there."""
    obj = {
        "nodes": [{"id": "n1", "type": "trigger", "tool": "book_slot"}],
        "edges": [],
        "summary": "A two-step booking flow that confirms the slot with the customer before booking.",
    }
    task = _task(
        Expect(workflow=WorkflowExpect(known_tools=["book_slot"]), known_tools=["book_slot"]),
        Reference(text=json.dumps(obj)),
        category="workflow_generation",
    )
    hardened, _ = harden_task(task)
    assert hardened.expect.workflow is not None
    assert hardened.expect.known_tools == ["book_slot"]
    assert hardened.expect.json_min_items["nodes"] == 1
    assert check_references_pass([hardened]) == []
