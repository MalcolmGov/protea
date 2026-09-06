import json
from pathlib import Path

import pytest

from protea.schemas.examples import ExampleMetadata, Split, TrainingExample
from protea.schemas.generation import Message, ToolCall
from protea.schemas.registry import DatasetEntry, DatasetStatus, SourceRef
from protea.training.data import GoldenLeakError, check_dataset_pin, load_records, render_chatml, render_example


def test_render_example_folds_tools_into_system_and_hermes_tool_calls(tool_example):
    rec = render_example(tool_example)
    roles = [m["role"] for m in rec["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert "<tools>" in rec["messages"][0]["content"]
    assert '"name": "get_order_status"' in rec["messages"][0]["content"]
    call = rec["messages"][2]["content"]
    assert call.startswith("<tool_call>")
    assert json.loads(call.split("<tool_call>")[1].split("</tool_call>")[0]) == {
        "name": "get_order_status",
        "arguments": {"order_id": "4821"},
    }
    text = render_chatml(rec)
    assert text.startswith("<|im_start|>system\n")
    assert text.count("<|im_end|>") == 5


def test_render_example_without_system_message_adds_one(meta_kwargs, lookup_tool):
    ex = TrainingExample(
        metadata=ExampleMetadata(**meta_kwargs),
        tools=[lookup_tool],
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", tool_calls=[ToolCall(id="c", name="get_order_status", arguments={})]),
        ],
    )
    rec = render_example(ex)
    assert rec["messages"][0]["role"] == "system"
    assert rec["messages"][0]["content"].startswith("You are a helpful assistant.")


def _write(path: Path, examples: list[TrainingExample]) -> Path:
    path.write_text("".join(e.model_dump_json() + "\n" for e in examples), encoding="utf-8")
    return path


def test_load_records_stats_and_golden_guard(tmp_path: Path, tool_example):
    train = _write(tmp_path / "train.jsonl", [tool_example, tool_example.model_copy(deep=True)])
    records, stats = load_records(train, max_examples=1)
    assert len(records) == 1
    assert stats.examples == 1
    assert stats.by_task_type == {"tool_calling": 1}
    assert stats.by_language == {"en-ZA": 1}
    assert stats.approx_tokens > 0
    assert len(stats.sha256) == 64

    golden = tool_example.model_copy(deep=True)
    golden.metadata.split = Split.GOLDEN
    leaky = _write(tmp_path / "leaky.jsonl", [tool_example, golden])
    with pytest.raises(GoldenLeakError, match="golden example"):
        load_records(leaky)


def test_dataset_pin_against_registry(tmp_path: Path, tool_example):
    train = _write(tmp_path / "train.jsonl", [tool_example])
    _, stats = load_records(train)
    entry = DatasetEntry(
        name="agent-training",
        version="0.1.0",
        path=str(train),
        sha256=stats.sha256,
        splits={"train": 1},
        sources=[SourceRef(repo="MalcolmGov/aria", commit="c22c31b")],
        status=DatasetStatus.DRAFT,
    )
    assert check_dataset_pin("agent-training-0.1.0", stats, [entry]) == []
    assert "not registered" in check_dataset_pin("other-1.0.0", stats, [entry])[0]
    drifted = entry.model_copy(update={"sha256": "0" * 64})
    assert "sha256" in check_dataset_pin("agent-training-0.1.0", stats, [drifted])[0]
    golden = entry.model_copy(update={"golden": True})
    assert "never be trained" in check_dataset_pin("agent-training-0.1.0", stats, [golden])[0]
