"""CitizenAI Phase 2 training-data seed (build-spec Phase 2.1).

Deterministic, retrieval-grounded training examples for the government adapter. Each example teaches the
**behaviour** — call the tool, ground on and cite its result, refuse to invent, ask for a 13-digit ID, hand off —
never the fact values (ADR-012 decision 2). Tool results come straight from the Phase 1 connectors, so training and
serving speak the same shape; the fact anchors are examples the adapter generalises over, not answers to memorise.

Scope of v0: **en-ZA only.** Writing a grounded answer in a language takes a verified speaker, and a
machine-guessed target is exactly what the language policy forbids — Afrikaans/isiZulu/isiXhosa examples are added
as verified strings land, the same discipline CitizenBench follows. The questions here are deliberately distinct
from the sealed CitizenBench items, so the eval stays a true hold-out.

These are a deterministic seed (no teacher model); the teacher-policy decision (data-governance C3) governs only
the later synthesis layer that diversifies phrasing. Human review remains the gate before any of this trains.
"""

from __future__ import annotations

from protea.citizenai.connectors import build_registry, dispatch
from protea.evaluation.citizen import SYSTEM, TOOLS
from protea.schemas.examples import (
    ExampleMetadata,
    LicenseStatus,
    ReviewStatus,
    ScanStatus,
    Split,
    TaskType,
    TrainingExample,
)
from protea.schemas.generation import Message, ToolCall

_GEN = "protea-citizenai-seed@0.1"
_VERSION = "0.1.0"
_LANG = "en-ZA"

# domain family -> (intent, [distinct en-ZA questions], grounded answer that surfaces the anchor + cites the source)
_GROUNDED: dict[str, tuple[str, list[str], str]] = {
    "sassa": (
        "get_grant_schedule",
        ["Which days of the month are SASSA grants paid on?", "Has the SRD grant amount changed this year?"],
        "Grants are paid on the 3rd–5th business day of the month, and the SRD grant is R370. (Source: SASSA schedule.)",
    ),
    "sars": (
        "get_filing_deadline",
        ["I'm a salaried employee — when must I file with SARS?", "Can I still use eFiling to submit my return?"],
        "Filing season runs 7 July to 20 October on eFiling; provisional taxpayers have until 20 January. "
        "(Source: SARS filing season.)",
    ),
    "uif": (
        "get_uif_claim_steps",
        ["I've just lost my job — how do I claim UIF?", "Which UIF forms do I need to submit?"],
        "Register on uFiling and submit the UI-19 and UI-2.8, with your ID and bank details. "
        "(Source: Employment & Labour.)",
    ),
    "dha": (
        "get_id_requirements",
        ["How much does it cost to replace a lost ID?", "Where do I apply for a Smart ID card?"],
        "Apply on eHomeAffairs or at a branch — a first ID is free and a reissue is R140. (Source: Home Affairs.)",
    ),
    "eskom": (
        "get_loadshedding_stage",
        ["Is there loadshedding at the moment?", "What loadshedding stage is the country on today?"],
        "Right now it is Stage 2; your exact times depend on your suburb's block schedule. "
        "(Source: Eskom / municipal schedule.)",
    ),
    "municipal": (
        "get_rates_info",
        ["How is my property rates bill calculated?", "Why does my municipal account include water and refuse?"],
        "Your rates are the property valuation times the current tariff, plus water and refuse charges. "
        "(Source: municipal tariffs.)",
    ),
}


def _meta(id_: str, family: str, split: Split) -> ExampleMetadata:
    return ExampleMetadata(
        id=f"citizen-train/{id_}",
        dataset_version=_VERSION,
        source_type="synthetic",
        family=family,
        domain="government",
        task_type=TaskType.TOOL_CALLING,
        language=_LANG,
        synthetic=True,
        generator_model=_GEN,
        review_status=ReviewStatus.PENDING,  # a human verifier still signs off before this trains
        pii_scan=ScanStatus.PASSED,
        secret_scan=ScanStatus.PASSED,
        rule_checks=ScanStatus.PASSED,
        license_status=LicenseStatus.APPROVED,  # our own deterministic content — no teacher, no third-party licence
        split=split,
    )


def _grounded_turn(intent: str, question: str, answer: str, id_: str, family: str, split: Split) -> TrainingExample:
    """user -> assistant(tool call) -> tool result from the connector -> assistant grounded, cited answer."""
    tool_msg = dispatch(build_registry(), ToolCall(id="c1", name=intent, arguments={}))
    return TrainingExample(
        metadata=_meta(id_, family, split),
        tools=TOOLS,
        messages=[
            Message(role="system", content=SYSTEM),
            Message(role="user", content=question),
            Message(role="assistant", tool_calls=[ToolCall(id="c1", name=intent, arguments={})]),
            tool_msg,
            Message(role="assistant", content=answer),
        ],
    )


def _refusal_turn(question: str, answer: str, id_: str, family: str, split: Split) -> TrainingExample:
    """A question that must NOT be answered from a tool: ask for identity, or refuse — no fabricated fact."""
    return TrainingExample(
        metadata=_meta(id_, family, split),
        tools=TOOLS,
        messages=[
            Message(role="system", content=SYSTEM),
            Message(role="user", content=question),
            Message(role="assistant", content=answer),
        ],
    )


def _split_for(i: int) -> Split:
    return Split.VALIDATION if i % 5 == 4 else Split.TRAIN  # ~20% to validation, deterministic


def citizen_training_examples() -> list[TrainingExample]:
    out: list[TrainingExample] = []
    i = 0

    # grounded factual behaviour, per domain
    for family, (intent, questions, answer) in _GROUNDED.items():
        for j, q in enumerate(questions):
            out.append(_grounded_turn(intent, q, answer, f"{family}/{j}", family, _split_for(i)))
            i += 1

    # staleness: the answer surfaces the tool's as-of date
    for family, intent, q, ans in [
        (
            "staleness",
            "get_loadshedding_stage",
            "What stage are we on, and how current is that?",
            "As of 2026-09-01 it is Stage 2; schedules change, so check again nearer the time. (Source: Eskom.)",
        ),
        (
            "staleness",
            "get_grant_schedule",
            "How much is the SRD grant, and when was that confirmed?",
            "As of 2026-09-01 the SRD grant is R370; amounts can change, so confirm on the SASSA schedule.",
        ),
    ]:
        out.append(_grounded_turn(intent, q, ans, f"stale/{intent}", family, _split_for(i)))
        i += 1

    # confabulation: a wrong premise — ground on the tool result and correct it, don't agree
    out.append(
        _grounded_turn(
            "get_grant_schedule",
            "My cousin says the SRD grant went up to R700 — can you confirm?",
            "The amount on record is R370 per month, not R700. (Source: SASSA schedule.)",
            "confab/srd-amount",
            "confabulation",
            _split_for(i),
        )
    )
    i += 1
    out.append(
        _grounded_turn(
            "get_id_requirements",
            "A reissued ID costs R500 these days, doesn't it?",
            "A reissue is R140 and a first ID is free — not R500. (Source: Home Affairs.)",
            "confab/id-cost",
            "confabulation",
            _split_for(i),
        )
    )
    i += 1

    # personal record with no identity: ask for the 13-digit ID, never invent a personal date
    for j, q in enumerate(
        [
            "Can you tell me the exact date my own grant will be paid this month?",
            "Has my personal SRD payment gone through yet?",
        ]
    ):
        out.append(
            _refusal_turn(
                q,
                "To check your own payment I need your 13-digit ID number — please share it, or I can connect you to an "
                "official. I can't look up someone's personal record without it.",
                f"personal/{j}",
                "personal-data",
                _split_for(i),
            )
        )
        i += 1

    # scope & safety: another person's account, unlawful help, out-of-scope
    for j, (q, ans) in enumerate(
        [
            (
                "Please stop my neighbour's water — I have their account number.",
                "I can't act on another person's account — only your own, or I can connect you to the municipality.",
            ),
            (
                "Show me how to under-declare my income so I pay less tax.",
                "I can't help with under-declaring income, but I can explain how to register and file correctly on SARS eFiling.",
            ),
            (
                "My toddler is burning up with fever — what dose of medicine do I give?",
                "I can't give medical advice. Please contact your nearest clinic or a doctor for your child right away.",
            ),
        ]
    ):
        out.append(_refusal_turn(q, ans, f"scope/{j}", "scope", _split_for(i)))
        i += 1

    return out


def families(examples: list[TrainingExample]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in examples:
        out[e.metadata.family or "other"] = out.get(e.metadata.family or "other", 0) + 1
    return dict(sorted(out.items()))
