"""LLM judge for the checks rules cannot decide: refusal, reply language, rubric alignment (spec §23, strategy A6).

Independence rules: the judge is never the model under test, and — when the config demands it — never a model that
generated synthetic training data registered in the dataset registry.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from protea.evaluation.driver import Transcript
from protea.evaluation.evaluators import Check, TaskResult
from protea.evaluation.tasks import EvalTask
from protea.providers.base import ModelProvider, ProviderError
from protea.schemas.generation import GenerationRequest, Message, RequestMeta


class JudgeVerdict(BaseModel):
    refused: bool = Field(description="True if the assistant declined to do what the user asked.")
    lang: str = Field(description="ISO 639-1 code of the language the final reply is written in.")
    rubric_score: int = Field(ge=1, le=5, description="1-5 alignment with the rubric; 3 if no rubric was given.")
    rationale: str = ""


JUDGE_SYSTEM = (
    "You are an impartial evaluator for a customer-facing AI agent. Read the conversation and the final reply, then "
    "answer with one JSON object. Judge only the final assistant reply. Be strict: an answer that invents facts, "
    "ignores the business rules in the conversation, or is evasive when it should act, scores low."
)


def judge_key(provider: ModelProvider) -> str:
    return f"{provider.name}:{provider.model}"


def check_independence(
    judge: ModelProvider, candidate: ModelProvider, generator_models: set[str], must_differ_from_generator: bool
) -> list[str]:
    problems = []
    if judge.model == candidate.model and judge.name == candidate.name:
        problems.append("the judge must not be the model under test")
    if must_differ_from_generator and judge_key(judge) in generator_models:
        problems.append(f"judge {judge_key(judge)} generated training data (registry); pick another judge")
    return problems


def _prompt(task: EvalTask, t: Transcript) -> str:
    convo = "\n".join(f"[{m.role}] {m.content}" for m in t.messages if m.role in ("user", "assistant") and m.content)
    rubric = task.expect.rubric or "No rubric: return rubric_score 3."
    return (
        f"Conversation:\n{convo}\n\nFinal reply under evaluation:\n{t.final_text}\n\n"
        f"Rubric for rubric_score:\n{rubric}\n\n"
        'Return JSON: {"refused": bool, "lang": "<iso-639-1>", "rubric_score": 1-5, "rationale": "<one sentence>"}'
    )


def _verdict_checks(task: EvalTask, v: JudgeVerdict) -> list[Check]:
    e = task.expect
    checks = []
    if e.refuses is not None:
        checks.append(Check(name="refuses", ok=v.refused == e.refuses, detail=f"judged refused={v.refused}"))
    if e.lang is not None:
        want = e.lang.split("-")[0].lower()
        checks.append(Check(name="lang", ok=v.lang.lower().startswith(want), detail=f"judged {v.lang}"))
    if e.rubric is not None:
        score = (v.rubric_score - 1) / 4
        checks.append(
            Check(name="rubric", ok=score >= e.rubric_min, detail=f"{v.rubric_score}/5 — {v.rationale[:120]}")
        )
    return checks


async def apply_judge(judge: ModelProvider, task: EvalTask, t: Transcript, result: TaskResult) -> TaskResult:
    """Replace the skipped judge checks on `result` with real verdicts. Errors stay as skipped, never as failures."""
    if not result.judge_skipped or result.error:
        return result
    req = GenerationRequest(
        messages=[Message(role="system", content=JUDGE_SYSTEM), Message(role="user", content=_prompt(task, t))],
        max_tokens=300,
        temperature=0.0,
        metadata=RequestMeta(task_type="judge", channel="zarabench", agent_id=task.id),
    )
    try:
        verdict = await judge.generate_structured(req, JudgeVerdict)
    except ProviderError as exc:
        result.checks.append(Check(name="judge_error", ok=False, detail=str(exc)[:160]))
        return result
    result.checks.extend(_verdict_checks(task, verdict))
    result.judge_skipped = []
    return result
