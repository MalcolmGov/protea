"""Blending reviewed synthetic rows into a mined train split: reject-dropping, PII scrub, dedup, approval."""

from __future__ import annotations

from protea.data_pipeline.blend import blend
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
