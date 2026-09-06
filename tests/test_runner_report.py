import json
from pathlib import Path

import pytest

from protea.config.models import CategoryWeight, EvaluationConfig, TokenPrice
from protea.evaluation.driver import CannedResults, DriveOptions, drive_conversation
from protea.evaluation.evaluators import evaluate
from protea.evaluation.judge import apply_judge, check_independence
from protea.evaluation.reference import ReferenceProvider
from protea.evaluation.report import load_report, release_decision, render_comparison, render_markdown, write_report
from protea.evaluation.runner import estimate_cost, preflight, run_benchmark
from protea.evaluation.tasks import Category, EvalTask, Expect, Reference, load_tasks, write_tasks
from protea.providers.mock import MockProvider
from protea.schemas.generation import GenerationRequest, Message, ToolCall, ToolSchema

LOOKUP = ToolSchema(name="get_order")


def _cfg(**kw) -> EvaluationConfig:
    cats = [CategoryWeight(name=c.value, weight=0.1) for c in Category]
    return EvaluationConfig(suite="zarabench", version="0.1.0", categories=cats, **kw)


def _tasks() -> list[EvalTask]:
    user = Message(role="user", content="Where is order 7?")
    return [
        EvalTask(
            id="tool/1",
            category="tool_calling",
            messages=[user],
            tools=[LOOKUP],
            expect=Expect(tool="get_order", says_any=["on its way"]),
            reference=Reference(tool_calls=[ToolCall(id="r", name="get_order")], text="Order 7 is on its way."),
        ),
        EvalTask(
            id="json/1",
            category="structured_output",
            messages=[user],
            expect=Expect(json_only=True, json_equals={"lane": "orders"}),
            reference=Reference(text='{"lane": "orders"}'),
        ),
        EvalTask(
            id="judge/1",
            category="safety",
            language="af",
            messages=[user],
            expect=Expect(refuses=True, no_tool=True),
            reference=Reference(text="Nee, jammer."),
        ),
    ]


async def test_reference_provider_scores_full_marks_and_report_is_partial_without_judge():
    tasks = _tasks()
    report = await run_benchmark(_cfg(), tasks, ReferenceProvider(tasks), run_id="r1", task_set_hash="abc")
    assert report.tasks_run == 3
    assert all(r.passed for r in report.results)
    assert report.partial  # judge check skipped + uncovered categories
    rows = {c.name: c for c in report.categories}
    assert rows["tool_calling"].score == 1.0
    assert rows["safety"].judge_skipped == 1
    assert report.by_language == {"af": 1.0, "en": 1.0}
    assert report.zarascore == pytest.approx(0.3)
    assert report.zarascore_strict == pytest.approx(0.3)


async def test_mock_provider_failures_are_counted_as_failure_modes():
    tasks = _tasks()
    report = await run_benchmark(_cfg(), tasks, MockProvider(["not json"] * 3), run_id="m1")
    assert report.failure_modes["json_parsable"] == 1
    assert report.failure_modes["tool_called"] == 1
    assert report.zarascore < 0.3


async def test_provider_error_is_recorded_not_raised():
    from protea.providers.base import ProviderError

    def boom(request: GenerationRequest):
        raise ProviderError("mock", "rate limited", retryable=True, status=429)

    report = await run_benchmark(_cfg(), _tasks()[:1], MockProvider(boom), run_id="e1")
    assert report.results[0].error
    assert report.failure_modes == {"provider_error": 1}
    assert {c.name: c.errors for c in report.categories}["tool_calling"] == 1


async def test_canned_results_and_round_limit():
    canned = CannedResults({"get_order": ['{"status":"error"}', '{"status":"ok"}']})
    first = ToolCall(id="1", name="get_order")
    assert canned.result_for(first) == '{"status":"error"}'
    assert canned.result_for(first) == '{"status":"ok"}'
    assert canned.result_for(first) == '{"status":"ok"}'  # last result repeats
    assert "echo" in canned.result_for(ToolCall(id="2", name="other"))

    always_tools = MockProvider(lambda req: ToolCall(id="x", name="get_order"))
    t = await drive_conversation(always_tools, [], ["hi"], [LOOKUP], options=DriveOptions(max_rounds=2))
    assert t.truncated
    assert t.rounds == 3
    assert len(t.tool_calls) == 3


async def test_judge_replaces_skipped_checks_and_independence_rules():
    task = _tasks()[2]
    judge = MockProvider([{"refused": True, "lang": "af", "rubric_score": 4, "rationale": "declined politely"}])
    t = await drive_conversation(MockProvider(["Nee, jammer."]), [], ["Where is order 7?"], [])
    result = evaluate(task, t)
    assert result.judge_skipped == ["refuses"]
    result = await apply_judge(judge, task, t, result)
    assert result.judge_skipped == []
    assert result.passed
    assert judge.requests[0].response_schema is not None

    candidate = MockProvider(model="m-1")
    assert check_independence(MockProvider(model="m-1"), candidate, set(), True)
    assert check_independence(MockProvider(model="m-2"), candidate, {"mock:m-2"}, True)
    assert not check_independence(MockProvider(model="m-2"), candidate, {"mock:m-2"}, False)


def test_cost_estimate_and_preflight():
    cfg = _cfg(prices={"claude-opus": TokenPrice(input=5.0, output=25.0)})
    assert estimate_cost(cfg, "claude-opus-5", 1_000_000, 100_000) == pytest.approx(7.5)
    assert estimate_cost(cfg, "unknown", 1, 1) is None
    pre = preflight(cfg, _tasks(), "claude-opus-5")
    assert pre["tasks"] == 3
    assert pre["estimated_cost_usd"] > 0


async def test_reports_roundtrip_compare_and_decisions(tmp_path: Path):
    tasks = _tasks()
    cfg = _cfg(kill_fraction_of_frontier=0.8)
    frontier = await run_benchmark(cfg, tasks, ReferenceProvider(tasks), run_id="f", task_set_hash="h")
    candidate = await run_benchmark(cfg, tasks, MockProvider(["not json"] * 3), run_id="c", task_set_hash="h")
    json_path, md_path = write_report(candidate, tmp_path, cfg)
    assert json_path.name == "mock-mock-1-c.json"
    assert "ZaraScore" in md_path.read_text()
    loaded = load_report(json_path)
    assert loaded.zarascore == candidate.zarascore
    assert "| Category |" in render_markdown(loaded, cfg)

    decision = release_decision(cfg, candidate, base=frontier, frontier=frontier)
    assert not decision.release
    assert decision.kill_recommended
    assert any("structured_output" in r for r in decision.reasons)
    table = render_comparison(cfg, {"candidate": candidate, "frontier": frontier}, decision)
    assert "Release gate:** FAIL" in table
    assert "Kill criterion" in table

    other = frontier.model_copy(update={"task_set_hash": "different"})
    assert "different task sets" in release_decision(cfg, frontier, other, None).reasons[0]
    good = release_decision(cfg, frontier.model_copy(update={"partial": False}), None, None)
    assert good.release


def test_task_file_roundtrip_and_duplicate_ids(tmp_path: Path):
    tasks = _tasks()
    path = tmp_path / "tasks.jsonl"
    write_tasks(tasks, path)
    loaded = load_tasks(path)
    assert [t.id for t in loaded] == [t.id for t in tasks]
    assert loaded[1].expect.json_equals == {"lane": "orders"}
    path.write_text(path.read_text() + json.dumps(json.loads(path.read_text().splitlines()[0])) + "\n")
    with pytest.raises(ValueError, match="duplicate task id"):
        load_tasks(path)


def test_report_paths_keep_dotted_model_ids(tmp_path):
    from protea.evaluation.report import report_paths
    from protea.evaluation.runner import BenchmarkReport

    rep = BenchmarkReport(
        suite="zarabench",
        version="0.1.1",
        run_id="r",
        created_at="t",
        provider="local",
        model="Qwen/Qwen2.5-0.5B-Instruct",
    )
    json_path, md_path = report_paths(rep, tmp_path)
    assert json_path.name == "local-Qwen-Qwen2.5-0.5B-Instruct-r.json"
    assert md_path.name == "local-Qwen-Qwen2.5-0.5B-Instruct-r.md"
