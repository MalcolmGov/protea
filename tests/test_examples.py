import json

import pytest
from pydantic import ValidationError

from protea.schemas.examples import ExampleMetadata, Split, TaskType, TrainingExample, validate_jsonl
from protea.schemas.generation import Message, ToolCall


def test_valid_tool_example_round_trips(tool_example):
    data = json.loads(tool_example.model_dump_json())
    again = TrainingExample.model_validate(data)
    assert again.metadata.family == "salon-booking"
    assert again.approx_tokens() > 0


def test_non_synthetic_requires_provenance(meta_kwargs):
    meta_kwargs.pop("source_path")
    with pytest.raises(ValidationError, match="traceable"):
        ExampleMetadata(**meta_kwargs)


def test_synthetic_requires_generator(meta_kwargs):
    with pytest.raises(ValidationError, match="generator_model"):
        ExampleMetadata(**{**meta_kwargs, "synthetic": True})
    ok = ExampleMetadata(**{**meta_kwargs, "synthetic": True, "generator_model": "Qwen/Qwen3-235B-A22B"})
    assert ok.synthetic


def test_unknown_language_rejected(meta_kwargs):
    with pytest.raises(ValidationError, match="language"):
        ExampleMetadata(**{**meta_kwargs, "language": "klingon"})


def test_tool_result_without_call_rejected(meta_kwargs):
    metadata = ExampleMetadata(**meta_kwargs)
    messages = [
        Message(role="user", content="hi"),
        Message(role="tool", tool_call_id="nope", content="{}"),
        Message(role="assistant", content="done"),
    ]
    with pytest.raises(ValidationError, match="tool result without"):
        TrainingExample(metadata=metadata, messages=messages)


def test_undeclared_tool_rejected(meta_kwargs, lookup_tool):
    metadata = ExampleMetadata(**meta_kwargs)
    messages = [
        Message(role="user", content="delete everything"),
        Message(role="assistant", tool_calls=[ToolCall(id="x", name="delete_customer", arguments={})]),
    ]
    with pytest.raises(ValidationError, match="undeclared tool"):
        TrainingExample(metadata=metadata, tools=[lookup_tool], messages=messages)


def test_validate_jsonl_reports_stats_and_errors(tmp_path, tool_example, meta_kwargs):
    golden = tool_example.model_copy(deep=True)
    golden.metadata.id = "golden-1"
    golden.metadata.split = Split.GOLDEN
    dup = tool_example.model_copy(deep=True)  # same id as tool_example -> duplicate
    lines = [
        tool_example.model_dump_json(),
        golden.model_dump_json(),
        dup.model_dump_json(),
        '{"metadata": {}, "messages": []}',
        "",
    ]
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(lines))
    report = validate_jsonl(path)
    assert report.total == 4
    assert report.valid == 3
    assert report.duplicate_ids == 1
    assert report.by_task_type == {TaskType.TOOL_CALLING.value: 3}
    assert report.golden_ids == ["golden-1"]
    assert report.errors[0]["line"] == 4
    assert not report.ok
