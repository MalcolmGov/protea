"""Trimming over-long string fields and dropping off-taxonomy rows (the agent_generation collapse fix)."""

from __future__ import annotations

import json

from protea.data_pipeline.trim import trim
from protea.schemas.examples import ExampleMetadata, TaskType, TrainingExample
from protea.schemas.generation import Message


def _agent(target: dict, *, task: TaskType = TaskType.AGENT_GENERATION) -> TrainingExample:
    return TrainingExample(
        metadata=ExampleMetadata(
            dataset_version="0.2.0", source_type="agent_definition", family="x", task_type=task,
            source_repo="MalcolmGov/aria", source_path="data/agents/x.agent.json",
        ),
        messages=[Message(role="user", content="make an agent"),
                  Message(role="assistant", content=json.dumps(target))],
    )


def _spec(**over) -> dict:
    base = {"id": "a", "name": "A", "category": "vertical", "tier": "pro", "objective": "o",
            "channels": ["web"], "languages": ["en"], "tools": [{"name": "t"}],
            "guardrails": "G" * 5000, "system_prompt": "S" * 6000}
    base.update(over)
    return base


def test_caps_long_fields_and_leaves_scored_fields_identical():
    ex = _agent(_spec())
    kept, report = trim([ex], task_types={"agent_generation"},
                        cap_chars={"guardrails": 800, "system_prompt": 800})
    assert report.rows_kept == 1 and report.rows_dropped_offtaxonomy == 0
    assert report.fields_trimmed == {"guardrails": 1, "system_prompt": 1}
    out = json.loads(kept[0].messages[-1].content)
    assert len(out["guardrails"]) == 800 + len("\n…[trimmed]")
    assert len(out["system_prompt"]) == 800 + len("\n…[trimmed]")
    # every eval-scored field is byte-identical
    for f in ("category", "tier", "channels", "languages", "tools", "id", "name", "objective"):
        assert out[f] == _spec()[f]


def test_drops_offtaxonomy_category_rows():
    rows = [_agent(_spec(category="commerce")), _agent(_spec(category="operations")),
            _agent(_spec(category="sales"))]
    kept, report = trim(rows, task_types={"agent_generation"},
                        drop_field_values={"category": {"commerce", "sales"}})
    assert report.rows_dropped_offtaxonomy == 2
    assert report.rows_kept == 1
    assert json.loads(kept[0].messages[-1].content)["category"] == "operations"
    assert report.ok


def test_short_fields_and_other_task_types_pass_through_untouched():
    ag = _agent(_spec(guardrails="short", system_prompt="short"))
    other = _agent(_spec(category="commerce"), task=TaskType.STRUCTURED_OUTPUT)  # wrong task type: untouched
    kept, report = trim([ag, other], task_types={"agent_generation"},
                        cap_chars={"guardrails": 800}, drop_field_values={"category": {"commerce"}})
    assert report.fields_trimmed == {}          # nothing over the cap
    assert report.rows_dropped_offtaxonomy == 0  # the commerce row is structured_output, not in scope
    assert report.rows_kept == 2


def test_non_json_target_is_left_alone():
    ex = TrainingExample(
        metadata=ExampleMetadata(
            dataset_version="0.2.0", source_type="agent_definition", family="x",
            task_type=TaskType.AGENT_GENERATION, source_repo="MalcolmGov/aria", source_path="x",
        ),
        messages=[Message(role="user", content="hi"), Message(role="assistant", content="not json at all")],
    )
    kept, report = trim([ex], task_types={"agent_generation"}, cap_chars={"guardrails": 10})
    assert report.rows_kept == 1 and report.fields_trimmed == {}
    assert kept[0].messages[-1].content == "not json at all"
