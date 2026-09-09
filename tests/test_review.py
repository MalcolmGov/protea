"""The synthetic-row review gate: correctness is already gated at synthesis, so this covers the softer
checks — provenance, secrets, PII, golden-lock leakage, dedup, and degenerate/refusal targets."""

from __future__ import annotations

from protea.data_pipeline.review import reference_shingles, review_examples
from protea.schemas.examples import ExampleMetadata, TaskType, TrainingExample
from protea.schemas.generation import Message, ToolCall


def _row(reply: str, *, family: str = "acme-support", synthetic: bool = True, tool: str | None = None,
         user: str = "where is my order?", model: str = "anthropic:claude-sonnet-5") -> TrainingExample:
    calls = [ToolCall(id="c1", name=tool, arguments={"q": user})] if tool else []
    last = Message(role="assistant", content=reply or None, tool_calls=calls)
    return TrainingExample(
        metadata=ExampleMetadata(
            dataset_version="0.2.0-synthetic",
            source_type="synthetic",
            family=family,
            task_type=TaskType.TOOL_CALLING,
            synthetic=synthetic,
            generator_model=model if synthetic else None,
            source_repo=None if synthetic else "MalcolmGov/aria",
            source_path=None if synthetic else "data/agents/x.agent.json",
        ),
        messages=[Message(role="user", content=user), last],
        tools=[],
    )


def test_clean_rows_pass():
    report = review_examples([_row("Your order ships tomorrow.", tool="get_order_status")])
    assert report.clean == 1 and report.blocked == 0 and report.flagged == 0
    assert report.ok


def test_secret_is_blocked():
    row = _row("Use key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV to authenticate.", tool="get_order_status")
    report = review_examples([row])
    assert report.blocked == 1 and report.secret_rows == 1
    assert not report.ok  # a secret must fail the gate
    assert report.rows[0].lane == "blocked"


def test_pii_is_flagged_not_blocked():
    row = _row("I've sent the tracking link to 083 123 4567.", tool="get_order_status")
    report = review_examples([row])
    assert report.flagged == 1 and report.pii_rows == 1
    assert "phone" in report.pii_by_kind
    assert report.ok  # PII is a human-review flag, not a hard block


def test_golden_lock_family_is_blocked():
    row = _row("Escalating now.", family="bank-branch", tool="handoff_to_human")
    report = review_examples([row], held_out_families={"bank-branch"})
    assert report.blocked == 1 and report.golden_lock_violations == 1
    assert not report.ok


def test_missing_provenance_is_blocked():
    # A non-synthetic-looking row (no generator_model) in the synthetic batch: provenance is broken.
    row = _row("ok", tool="get_order_status")
    row.metadata.generator_model = None
    row.metadata.synthetic = False
    row.metadata.source_repo = "MalcolmGov/aria"
    row.metadata.source_path = "x"
    report = review_examples([row])
    assert report.blocked == 1 and report.invariant_violations == 1


def test_degenerate_and_refusal_are_flagged():
    degen = _row("ok")  # no tool call, near-empty
    refusal = _row("I'm unable to help with that request.", tool="get_order_status")
    report = review_examples([degen, refusal])
    assert report.degenerate == 1
    assert report.refusal_shaped == 1
    assert report.flagged == 2


def test_within_batch_duplicates_flagged():
    a = _row("Your order ships tomorrow morning from the Cape Town depot.", tool="get_order_status")
    b = _row("Your order ships tomorrow morning from the Cape Town depot.", tool="get_order_status")
    report = review_examples([a, b])
    assert report.duplicates_within == 1  # the second is the dup; the first is kept
    assert report.flagged == 1


def test_cross_reference_duplicates_flagged():
    existing = _row("The refund was processed to your original card within three business days.",
                    tool="get_refund_status")
    incoming = _row("The refund was processed to your original card within three business days.",
                    tool="get_refund_status")
    report = review_examples([incoming], reference_targets=reference_shingles([existing]))
    assert report.duplicates_vs_reference == 1
    assert report.flagged == 1


def test_distribution_and_worst_lane_wins():
    # A row with BOTH a secret (block) and PII (flag) must land in the worst lane: blocked.
    row = _row("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV mailed to jane.doe@example.com", tool="get_order_status")
    report = review_examples([row, _row("All set.", tool="get_order_status", family="other-co")])
    assert report.rows[0].lane == "blocked"
    assert report.by_family == {"acme-support": 1, "other-co": 1}
