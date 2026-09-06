import json

import pytest

from protea.evaluation.driver import Transcript
from protea.evaluation.evaluators import evaluate
from protea.evaluation.tasks import EvalTask, Expect, WorkflowExpect
from protea.schemas.generation import Message, ToolCall, ToolSchema

LOOKUP = ToolSchema(name="get_order", parameters={"type": "object", "properties": {"order_id": {"type": "string"}}})
REFUND = ToolSchema(name="issue_refund")


def _task(expect: Expect, tools=(LOOKUP, REFUND), category="tool_calling") -> EvalTask:
    return EvalTask(
        id="t1",
        category=category,
        messages=[Message(role="system", content="desk"), Message(role="user", content="where is order 7?")],
        tools=list(tools),
        expect=expect,
    )


def _transcript(text: str, calls: list[ToolCall] | None = None, **kw) -> Transcript:
    return Transcript(messages=[], tool_calls=calls or [], final_text=text, **kw)


def _names(result) -> dict[str, bool]:
    return {c.name: c.ok for c in result.checks}


def test_tool_grammar_and_arguments():
    e = Expect(tool="get_order", tool_none=["issue_refund"], args={"get_order": {"order_id": "7"}})
    good = _transcript("Order 7 is on its way.", [ToolCall(id="1", name="get_order", arguments={"order_id": "7"})])
    r = evaluate(_task(e), good)
    assert r.passed
    bad = _transcript("Refunded.", [ToolCall(id="1", name="issue_refund"), ToolCall(id="2", name="get_order")])
    r = evaluate(_task(e), bad)
    names = _names(r)
    assert names["forbidden_tool_avoided"] is False
    assert names["args:get_order"] is False
    assert r.failure_modes == ["forbidden_tool_avoided", "args:get_order"]


def test_undeclared_tool_call_is_a_hallucination():
    r = evaluate(_task(Expect(no_tool=True)), _transcript("done", [ToolCall(id="1", name="book_flight")]))
    assert _names(r) == {"declared_tools_only": False, "no_tool": False}
    assert r.score == 0.0


def test_text_checks():
    e = Expect(says_any=["7th"], says_none=["i don't know"], must_include=["paye"], max_words=10)
    r = evaluate(_task(e), _transcript("PAYE is due on the 7th."))
    assert r.passed
    r = evaluate(_task(e), _transcript("I don't know when PAYE is due, sorry, I really do not know at all."))
    names = _names(r)
    assert names["says_any"] is False
    assert names["says_none"] is False
    assert names["max_words"] is False


def test_json_only_tolerates_a_fence_but_rejects_prose():
    obj = {"lane": "stokvel"}
    fenced = "```json\n" + json.dumps(obj) + "\n```"
    strict = evaluate(_task(Expect(json_only=True, json_equals=obj), tools=()), _transcript(fenced))
    assert strict.passed  # the production gate strips a single fence, so the benchmark does too
    prose = evaluate(_task(Expect(json_only=True, json_equals=obj), tools=()), _transcript("Sure: " + fenced))
    assert _names(prose) == {"json_parsable": False}
    lenient = evaluate(_task(Expect(json_equals=obj), tools=()), _transcript("Sure: " + fenced))
    assert lenient.passed


def test_schema_fields_and_allowlists():
    schema = {"type": "object", "required": ["id", "tools"], "properties": {"tools": {"type": "array"}}}
    e = Expect(
        schema=schema,
        json_fields={"channels": ["web", "whatsapp"], "tier": "pro"},
        known_tools=["get_order"],
        json_required=["id"],
    )
    ok = {"id": "x", "tier": "pro", "channels": ["whatsapp", "web"], "tools": [{"name": "get_order"}]}
    assert evaluate(_task(e, tools=()), _transcript(json.dumps(ok))).passed
    bad = {"tier": "standard", "channels": ["web"], "tools": [{"name": "teleport"}]}
    names = _names(evaluate(_task(e, tools=()), _transcript(json.dumps(bad))))
    assert names["schema_valid"] is False
    assert names["field:tier"] is False
    assert names["field:channels"] is False
    assert names["no_hallucinated_tools"] is False
    assert names["required_keys"] is False


def test_bindings_give_partial_credit():
    e = Expect(bindings={"get_job": "webhook", "handoff": "slack"}, known_connectors=["webhook", "slack"])
    out = {"bindings": [{"tool": "get_job", "connector": "webhook"}, {"tool": "handoff", "connector": "teams"}]}
    r = evaluate(_task(e, tools=()), _transcript(json.dumps(out)))
    assert _names(r)["binding:get_job"] is True
    assert _names(r)["binding:handoff"] is False
    assert _names(r)["no_hallucinated_connectors"] is False
    assert 0 < r.score < 1


@pytest.mark.parametrize(
    "edges,expected_ok",
    [
        ([("n1", "n2"), ("n2", "n3")], True),
        ([("n1", "n2"), ("n2", "n3"), ("n3", "n1")], False),
    ],
)
def test_workflow_dag(edges, expected_ok):
    w = WorkflowExpect(required_types=["trigger", "action"], known_tools=["get_order"], must_use_tools=["get_order"])
    obj = {
        "nodes": [
            {"id": "n1", "type": "trigger"},
            {"id": "n2", "type": "lookup", "tool": "get_order"},
            {"id": "n3", "type": "action"},
        ],
        "edges": [{"from": a, "to": b} for a, b in edges],
    }
    r = evaluate(_task(Expect(json_only=True, workflow=w), tools=()), _transcript(json.dumps(obj)))
    assert _names(r)["workflow_acyclic"] is expected_ok
    assert r.passed is expected_ok


def test_workflow_rejects_unknown_types_and_dangling_edges():
    w = WorkflowExpect(known_tools=["get_order"])
    obj = {"nodes": [{"id": "a", "type": "magic", "tool": "teleport"}], "edges": [{"from": "a", "to": "zz"}]}
    names = _names(evaluate(_task(Expect(workflow=w), tools=()), _transcript(json.dumps(obj))))
    assert names["workflow_node_types"] is False
    assert names["workflow_edges_resolve"] is False
    assert names["no_hallucinated_tools"] is False
    assert names["workflow_required_types"] is False


def test_judge_checks_are_skipped_not_failed_without_a_judge():
    r = evaluate(_task(Expect(refuses=True, lang="af", rubric="be nice", no_tool=True)), _transcript("Nee, jammer."))
    assert r.judge_skipped == ["refuses", "lang", "rubric"]
    assert r.passed  # only the rule checks count


def test_provider_error_and_truncation():
    r = evaluate(_task(Expect(no_tool=True)), _transcript("", error="anthropic: 529 overloaded"))
    assert r.score == 0.0
    assert r.failure_modes == ["provider_error"]
    r = evaluate(_task(Expect(tool="get_order")), _transcript("", [ToolCall(id="1", name="get_order")], truncated=True))
    assert _names(r)["turn_completed"] is False


def test_expect_validation():
    with pytest.raises(ValueError):
        Expect(no_tool=True, tool="get_order")
    undeclared = Expect(tool="not_declared")
    with pytest.raises(ValueError):
        _task(undeclared)
