"""Evaluation framework: task contract, conversation driver, evaluators, judge, runner, reports, golden sealing."""

from protea.evaluation.evaluators import Check, TaskResult, evaluate
from protea.evaluation.runner import BenchmarkReport, run_benchmark
from protea.evaluation.tasks import Category, EvalTask, Expect, load_tasks, write_tasks

__all__ = [
    "BenchmarkReport",
    "Category",
    "Check",
    "EvalTask",
    "Expect",
    "TaskResult",
    "evaluate",
    "load_tasks",
    "run_benchmark",
    "write_tasks",
]
