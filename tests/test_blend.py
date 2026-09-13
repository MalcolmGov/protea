"""Blending reviewed synthetic rows into a mined train split: reject-dropping, PII scrub, dedup, approval."""

from __future__ import annotations

import pytest

from protea.data_pipeline.blend import apply_cap, blend, parse_cap
from protea.schemas.examples import ExampleMetadata, ReviewStatus, ScanStatus, TaskType, TrainingExample
from protea.schemas.generation import Message, ToolCall


def _mined(reply: str, *, family: str = "acme", task: TaskType = TaskType.AGENT_GENERATION) -> TrainingExample:
    return TrainingExample(
        metadata=ExampleMetadata(
            dataset_version="0.2.0", source_type="agent_definition", family=family, task_type=task,
            source_repo="MalcolmGov/aria", source_path="data/agents/x.agent.json",
        ),
        messages=[Message(role="user", content="make an agent"), Message(role="assistant", content=reply)],
    )


def _synthetic(reply: str, *, family: str = "acme", status: ReviewStatus = ReviewStatus.PENDING,
               tool: str = "get_order_status") -> TrainingExample:
    return TrainingExample(
        metadata=ExampleMetadata(
            dataset_version="0.2.0-synthetic", source_type="synthetic", family=family,
            task_type=TaskType.TOOL_CALLING, synthetic=True, generator_model="anthropic:claude-sonnet-5",
            review_status=status,
        ),
        messages=[Message(role="user", content="status?"),
                  Message(role="assistant", content=reply, tool_calls=[ToolCall(id="c1", name=tool, arguments={})])],
    )


def test_mined_rows_pass_through_and_synthetic_appended():
    mined = [_mined("Here is your agent.")]
    synth = [_synthetic("Shipping tomorrow.")]
    merged, report = blend(mined, synth)
    assert report.merged_total == 2 and report.synthetic_kept == 1
    assert report.ok
    assert all(ex.metadata.review_status == ReviewStatus.APPROVED for ex in merged if ex.metadata.synthetic)


def test_rejected_rows_are_dropped():
    merged, report = blend([_mined("a")], [_synthetic("bad", status=ReviewStatus.REJECTED)])
    assert report.dropped_rejected == 1 and report.synthetic_kept == 0
    assert report.merged_total == 1  # only the mined row


def test_approved_only_drops_pending():
    synth = [_synthetic("x", status=ReviewStatus.PENDING), _synthetic("y", status=ReviewStatus.APPROVED,
                                                                       family="beta")]
    merged, report = blend([_mined("a")], synth, keep_flagged=False)
    assert report.dropped_unapproved == 1
    assert report.synthetic_kept == 1


def test_pii_is_scrubbed_in_synthetic_rows():
    merged, report = blend([_mined("a")], [_synthetic("Call me on 083 123 4567.")])
    assert report.pii_scrubbed_rows == 1 and report.pii_redactions.get("phone", 0) == 1
    synth = next(ex for ex in merged if ex.metadata.synthetic)
    assert "083 123 4567" not in (synth.messages[-1].content or "")   # raw PII gone
    assert synth.metadata.pii_scan == ScanStatus.PASSED


def test_duplicate_synthetic_rows_collapse():
    a = _synthetic("The refund posts to your card in three business days.")
    b = _synthetic("The refund posts to your card in three business days.", family="beta")
    merged, report = blend([_mined("a")], [a, b])
    assert report.dropped_duplicate == 1
    assert report.synthetic_kept == 1
    assert report.ok


def test_synthetic_cap_downsamples_per_task_type_and_is_deterministic():
    # 10 distinct synthetic tool_calling rows, capped to 3: exactly 3 survive, the rest are counted as capped,
    # and mined rows are untouched. Determinism is keyed on the row id, so re-blending the SAME rows (same ids)
    # keeps the SAME subset — which is the real guarantee, since we cap an existing reviewed file.
    import copy
    synth = [_synthetic(f"reply {i}", family=f"fam{i}", tool=f"tool_{i}") for i in range(10)]

    merged, report = blend([_mined("keep me")], copy.deepcopy(synth), synthetic_cap={"tool_calling": 3})
    assert report.synthetic_kept == 3
    assert report.dropped_capped == 7
    assert report.merged_total == 4  # 1 mined + 3 synthetic
    assert report.ok
    assert sum(1 for ex in merged if not ex.metadata.synthetic) == 1  # mined survived, uncapped

    merged2, _ = blend([_mined("keep me")], copy.deepcopy(synth), synthetic_cap={"tool_calling": 3})

    def kept_ids(rows):
        return sorted(str(ex.metadata.id) for ex in rows if ex.metadata.synthetic)

    assert kept_ids(merged) == kept_ids(merged2)  # same input ids -> same kept subset


def test_parse_cap_reads_pairs_and_rejects_bad_input():
    assert parse_cap(["tool_calling=250", "routing=10"]) == {"tool_calling": 250, "routing": 10}
    assert parse_cap([]) == {}
    for bad in ["tool_calling", "tool_calling=", "tool_calling=-5", "tool_calling=x"]:
        with pytest.raises(ValueError):
            parse_cap([bad])


def test_apply_cap_standalone_caps_per_task_and_leaves_others():
    # apply_cap on a mixed set (used by `dataset cap` to rebalance an existing blend without re-blending).
    rows = ([_synthetic(f"t{i}", family=f"f{i}", tool=f"tool_{i}") for i in range(8)]
            + [_mined(f"a{i}", family=f"g{i}") for i in range(3)])  # 8 tool_calling + 3 agent_generation
    kept, dropped = apply_cap(rows, {"tool_calling": 2})
    assert dropped == 6
    kept_by_task = {}
    for ex in kept:
        kept_by_task[str(ex.metadata.task_type)] = kept_by_task.get(str(ex.metadata.task_type), 0) + 1
    assert kept_by_task == {"tool_calling": 2, "agent_generation": 3}  # only tool_calling capped


def test_cap_above_count_or_absent_task_is_a_noop():
    synth = [_synthetic("a", family="x"), _synthetic("b", family="y")]
    # cap larger than the group, and a cap for a task type not present, both leave every row in.
    merged, report = blend([_mined("m")], synth, synthetic_cap={"tool_calling": 99, "routing": 5})
    assert report.dropped_capped == 0 and report.synthetic_kept == 2


def test_mined_rows_win_over_synthetic_duplicates():
    # A synthetic row whose target (reply + tool name) duplicates a mined tool_calling row must lose; the mined
    # row stays. Mined rows are ordered first, so they are never the one dropped.
    mined_dup = TrainingExample(
        metadata=ExampleMetadata(
            dataset_version="0.2.0", source_type="agent_definition", family="acme",
            task_type=TaskType.TOOL_CALLING, source_repo="MalcolmGov/aria", source_path="x",
        ),
        messages=[Message(role="user", content="status?"),
                  Message(role="assistant", content="Escalating to a human now.",
                          tool_calls=[ToolCall(id="c1", name="get_order_status", arguments={})])],
    )
    synth = [_synthetic("Escalating to a human now.")]  # same reply + same tool name
    merged, report = blend([mined_dup], synth)
    assert report.dropped_duplicate == 1
    assert report.synthetic_kept == 0
    assert merged[0].metadata.synthetic is False  # the mined row survived
