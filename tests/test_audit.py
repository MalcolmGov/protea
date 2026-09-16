"""The suite's adversarial floor (`protea evaluate audit`): a stub must not be able to score well.

These tests pin the *mechanism*, not the sealed suite's current numbers: a shape-only category must expose the
stub, a phrase-or-hedge category must reject it, and the reported floor must be the weighted mean of the rows.
"""

import json

from protea.config.models import CategoryWeight, EvaluationConfig
from protea.evaluation.audit import audit, stub_transcript
from protea.evaluation.tasks import EvalTask, Expect, Reference
from protea.schemas.generation import Message, ToolSchema

USER = Message(role="user", content="Design me an agent.")
SHAPE = "agent_generation"  # a category whose checks are structural
CONTENT = "hallucination"  # a category whose checks need actual language


def _task(task_id: str, category: str, expect: Expect, tools: list[ToolSchema] | None = None) -> EvalTask:
    return EvalTask(
        id=task_id,
        category=category,
        messages=[USER],
        tools=tools or [],
        expect=expect,
        reference=Reference(text="reference answer"),
    )


SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "tools": {"type": "array"}},
    "required": ["id", "tools"],
}


def _cfg() -> EvaluationConfig:
    return EvaluationConfig(
        suite="audit",
        version="0",
        categories=[CategoryWeight(name=SHAPE, weight=0.5), CategoryWeight(name=CONTENT, weight=0.5)],
    )


def _tasks() -> list[EvalTask]:
    return [
        _task("shape/1", SHAPE, Expect(schema_=SCHEMA, json_fields={"id": "x"})),
        _task("shape/2", SHAPE, Expect(json_only=True, json_required=["id"])),
        _task("signal/1", CONTENT, Expect(says_any=["not sure", "would need to check"])),
        _task("signal/2", CONTENT, Expect(must_include=["refund policy"], no_tool=True)),
    ]


def test_a_shape_only_category_is_fully_satisfied_by_the_stub():
    result = audit(_tasks(), _cfg())
    rows = {r.category: r for r in result.categories}
    assert rows[SHAPE].stub_mean == 1.0
    assert rows[SHAPE].stub_full_marks == 2
    assert SHAPE in result.worst_offenders


def test_a_content_category_rejects_the_stub():
    result = audit(_tasks(), _cfg())
    rows = {r.category: r for r in result.categories}
    assert rows[CONTENT].stub_mean < 1.0
    assert rows[CONTENT].stub_full_marks == 0
    assert CONTENT not in result.worst_offenders


def test_the_floor_is_the_weighted_mean_of_the_rows_and_is_rendered():
    result = audit(_tasks(), _cfg())
    rows = {r.category: r for r in result.categories}
    assert result.tasks == 4
    # a category the stub fully satisfies still counts at full weight — that is the point of the floor
    assert result.stub_zarascore == rows[SHAPE].stub_mean * 0.5 + rows[CONTENT].stub_mean * 0.5
    assert result.stub_zarascore > rows[CONTENT].stub_mean
    assert "stub ZaraScore" in result.render()
    assert f"fully satisfied by a stub: {SHAPE}" in result.render()
    assert json.loads(json.dumps(result.model_dump()))["tasks"] == 4


def test_stub_emits_the_expected_tool_call_but_no_knowledge():
    # EvalTask refuses an expectation that names an undeclared tool, so the tool is declared here
    task = _task(
        "tool/1", SHAPE, Expect(tool="get_order", args={"get_order": {"order_id": "7"}}), [ToolSchema(name="get_order")]
    )
    transcript = stub_transcript(task)
    assert [c.name for c in transcript.tool_calls] == ["get_order"]
    assert transcript.tool_calls[0].arguments == {"order_id": "7"}
    assert "order" not in transcript.final_text.lower()  # no facts, ever

    refused = stub_transcript(_task("tool/2", SHAPE, Expect(no_tool=True, refuses=True)))
    assert refused.tool_calls == []
    assert refused.final_text
