"""The eval's system-prompt overlay: run_task must prepend/merge cfg.system_prompt so the model is scored under
its production guardrail framing (the Exp 0 capability)."""

from __future__ import annotations

import asyncio

from protea.config.models import EvaluationConfig
from protea.evaluation.runner import run_task
from protea.evaluation.tasks import EvalTask, Expect
from protea.providers.mock import MockProvider
from protea.schemas.generation import Message


def _cfg(**over) -> EvaluationConfig:
    return EvaluationConfig(suite="zarabench", version="0.1",
                            categories=[{"name": "tool_calling", "weight": 1.0}], **over)


def _task(messages: list[Message]) -> EvalTask:
    return EvalTask(id="t1", category="tool_calling", messages=messages, tools=[], expect=Expect(says_any=["ok"]))


def _first_request_messages(cfg, task):
    prov = MockProvider(["ok"])
    asyncio.run(run_task(cfg, task, prov, None))
    return prov.requests[0].messages


def test_overlay_merges_into_the_tasks_system_message():
    task = _task([Message(role="system", content="You are Desk."), Message(role="user", content="hi")])
    msgs = _first_request_messages(_cfg(system_prompt="POLICY: never invent facts."), task)
    assert msgs[0].role == "system"
    assert msgs[0].content.startswith("POLICY: never invent facts.")
    assert "You are Desk." in msgs[0].content          # the task's own system prompt is preserved after the overlay
    assert sum(1 for m in msgs if m.role == "system") == 1  # merged, not a second system message


def test_overlay_is_added_when_the_task_has_no_system_message():
    task = _task([Message(role="user", content="hi")])
    msgs = _first_request_messages(_cfg(system_prompt="POLICY"), task)
    assert msgs[0].role == "system" and msgs[0].content == "POLICY"


def test_no_overlay_leaves_the_task_untouched():
    task = _task([Message(role="system", content="You are Desk."), Message(role="user", content="hi")])
    msgs = _first_request_messages(_cfg(), task)  # system_prompt is None
    assert msgs[0].role == "system" and msgs[0].content == "You are Desk."
