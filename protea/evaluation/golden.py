"""Golden-set sealing (spec §25): the task file is hash-pinned and its families are recorded as held out.

`verify` runs in CI; `held_out_families` feeds the dataset pipeline so future dataset versions never train on them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from protea.evaluation.tasks import EvalTask, load_tasks, task_set_hash, task_stats


class GoldenLock(BaseModel):
    suite: str
    version: str
    tasks_path: str
    sha256: str
    count: int
    by_category: dict[str, int]
    held_out_families: list[str] = Field(default_factory=list)
    sealed_at: str


def seal(suite: str, version: str, tasks_path: Path, lock_path: Path, *, repo_root: Path) -> GoldenLock:
    tasks = load_tasks(tasks_path)
    stats = task_stats(tasks)
    lock = GoldenLock(
        suite=suite,
        version=version,
        tasks_path=str(tasks_path.resolve().relative_to(repo_root.resolve())),
        sha256=task_set_hash(tasks_path),
        count=stats["total"],
        by_category=stats["by_category"],
        held_out_families=stats["families"],
        sealed_at=datetime.now(UTC).isoformat(),
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(lock.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return lock


def load_lock(lock_path: Path) -> GoldenLock:
    return GoldenLock.model_validate(json.loads(lock_path.read_text(encoding="utf-8")))


def verify(lock_path: Path, *, repo_root: Path, min_tasks: int = 150) -> list[str]:
    """Problems with the sealed set: hash drift, missing file, too few tasks, uncovered category, invalid tasks."""
    if not lock_path.exists():
        return [f"lock not found: {lock_path}"]
    lock = load_lock(lock_path)
    tasks_path = repo_root / lock.tasks_path
    if not tasks_path.exists():
        return [f"task file not found: {tasks_path}"]
    problems = []
    digest = task_set_hash(tasks_path)
    if digest != lock.sha256:
        problems.append(f"task set hash {digest[:12]} != sealed {lock.sha256[:12]} (re-seal deliberately, with review)")
    try:
        tasks: list[EvalTask] = load_tasks(tasks_path)
    except ValueError as exc:
        return problems + [str(exc)]
    if len(tasks) < min_tasks:
        problems.append(f"{len(tasks)} tasks < required {min_tasks}")
    if len(tasks) != lock.count:
        problems.append(f"{len(tasks)} tasks != sealed count {lock.count}")
    empty = [c for c, n in lock.by_category.items() if n == 0]
    if empty:
        problems.append(f"categories without tasks: {empty}")
    return problems


def held_out_families(lock_path: Path | None) -> set[str]:
    if lock_path is None or not lock_path.exists():
        return set()
    return set(load_lock(lock_path).held_out_families)
