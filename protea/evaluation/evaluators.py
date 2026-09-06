"""Deterministic evaluators (spec §23): every check is a named pass/fail; a task's score is the passed fraction.

Judge-dependent checks (refuses / lang / rubric) are appended by `protea.evaluation.judge` when a judge is configured;
without one they are reported as skipped rather than scored, so a run never silently degrades into a partial metric.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import jsonschema
from pydantic import BaseModel, Field

from protea.evaluation.driver import Transcript
from protea.evaluation.tasks import WORKFLOW_NODE_TYPES, EvalTask, Expect, WorkflowExpect
from protea.providers.base import extract_json


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class TaskResult(BaseModel):
    task_id: str
    category: str
    language: str
    family: str | None = None
    checks: list[Check] = Field(default_factory=list)
    judge_skipped: list[str] = Field(default_factory=list)  # judge checks that could not run
    error: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: int = 0
    final_text: str = ""
    tool_calls: list[str] = Field(default_factory=list)

    @property
    def score(self) -> float:
        if self.error or not self.checks:
            return 0.0
        return sum(c.ok for c in self.checks) / len(self.checks)

    @property
    def passed(self) -> bool:
        return not self.error and bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failure_modes(self) -> list[str]:
        if self.error:
            return ["provider_error"]
        return [c.name for c in self.checks if not c.ok]


# ---- helpers ----------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _phrase_in(phrase: str, text: str) -> bool:
    return _norm(phrase) in text


def _parse_json(text: str, strict: bool) -> tuple[Any | None, str]:
    if strict:
        try:
            return json.loads(text.strip()), ""
        except json.JSONDecodeError as exc:
            return None, f"not bare JSON: {exc.msg}"
    try:
        return extract_json(text), ""
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"no JSON object found: {exc}"


def _lookup(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list) and all(isinstance(x, str) for x in a + b):
        return sorted(a) == sorted(b)
    return a == b


def _names_in(obj: Any, key: str) -> list[str]:
    """Names of items under `key`: either strings or dicts with a `name` (tools) / `connector` (bindings)."""
    items = obj.get(key) if isinstance(obj, dict) else None
    out: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            out.append(str(it.get("name") or it.get("connector") or ""))
    return out


# ---- tool checks ----------------------------------------------------------------------------
def _tool_checks(e: Expect, t: Transcript, declared: set[str]) -> list[Check]:
    called = t.called
    checks: list[Check] = []
    unknown = sorted({c for c in called if c not in declared})
    if called:
        checks.append(Check(name="declared_tools_only", ok=not unknown, detail=f"unknown {unknown}" if unknown else ""))
    if e.tool:
        checks.append(Check(name="tool_called", ok=e.tool in called, detail=f"expected {e.tool}, called {called}"))
    if e.tool_any:
        checks.append(Check(name="tool_any", ok=bool(set(e.tool_any) & set(called)), detail=f"called {called}"))
    if e.tool_none:
        hit = sorted(set(e.tool_none) & set(called))
        checks.append(Check(name="forbidden_tool_avoided", ok=not hit, detail=f"called {hit}" if hit else ""))
    if e.no_tool:
        checks.append(Check(name="no_tool", ok=not called, detail=f"called {called}" if called else ""))
    checks.extend(_arg_checks(e, t))
    return checks


def _arg_checks(e: Expect, t: Transcript) -> list[Check]:
    checks = []
    for tool, expected in e.args.items():
        calls = [c for c in t.tool_calls if c.name == tool]
        if not calls:
            checks.append(Check(name=f"args:{tool}", ok=False, detail="tool not called"))
            continue
        actual = calls[0].arguments
        missing = {k: v for k, v in expected.items() if not _same(actual.get(k), v)}
        checks.append(Check(name=f"args:{tool}", ok=not missing, detail=f"mismatch {missing}" if missing else ""))
    return checks


# ---- text checks ----------------------------------------------------------------------------
def _text_checks(e: Expect, text: str) -> list[Check]:
    norm = _norm(text)
    checks: list[Check] = []
    if e.says_any:
        checks.append(Check(name="says_any", ok=any(_phrase_in(p, norm) for p in e.says_any), detail=str(e.says_any)))
    if e.says_none:
        hit = [p for p in e.says_none if _phrase_in(p, norm)]
        checks.append(Check(name="says_none", ok=not hit, detail=f"found {hit}" if hit else ""))
    for p in e.must_include:
        checks.append(Check(name="must_include", ok=_phrase_in(p, norm), detail=p))
    for p in e.must_not_include:
        checks.append(Check(name="must_not_include", ok=not _phrase_in(p, norm), detail=p))
    if e.max_words is not None:
        n = len(text.split())
        checks.append(Check(name="max_words", ok=n <= e.max_words, detail=f"{n} words > {e.max_words}"))
    return checks


# ---- structured checks ------------------------------------------------------------------------
def _needs_json(e: Expect) -> bool:
    return bool(
        e.json_only
        or e.schema_
        or e.json_equals is not None
        or e.json_fields
        or e.json_required
        or e.known_tools
        or e.known_connectors
        or e.bindings
        or e.workflow
    )


def _schema_check(schema: dict[str, Any], obj: Any) -> Check:
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(obj), key=lambda x: list(x.path))
    detail = "; ".join(f"{'/'.join(map(str, err.path)) or '$'}: {err.message[:80]}" for err in errors[:3])
    return Check(name="schema_valid", ok=not errors, detail=detail)


def _field_checks(e: Expect, obj: Any) -> list[Check]:
    checks = []
    for path, expected in e.json_fields.items():
        found, actual = _lookup(obj, path)
        ok = found and _same(actual, expected)
        checks.append(Check(name=f"field:{path}", ok=ok, detail="" if ok else f"expected {expected!r}, got {actual!r}"))
    if e.json_required:
        missing = [k for k in e.json_required if not (isinstance(obj, dict) and k in obj)]
        checks.append(Check(name="required_keys", ok=not missing, detail=f"missing {missing}" if missing else ""))
    return checks


def _allowlist_checks(e: Expect, obj: Any) -> list[Check]:
    checks = []
    if e.known_tools:
        bad = sorted(set(_names_in(obj, "tools")) - set(e.known_tools))
        checks.append(Check(name="no_hallucinated_tools", ok=not bad, detail=f"invented {bad}" if bad else ""))
    if e.known_connectors:
        bad = sorted(set(_names_in(obj, "bindings")) - set(e.known_connectors))
        checks.append(Check(name="no_hallucinated_connectors", ok=not bad, detail=f"invented {bad}" if bad else ""))
    return checks


def _binding_checks(e: Expect, obj: Any) -> list[Check]:
    actual: dict[str, str] = {}
    for b in (obj.get("bindings") if isinstance(obj, dict) else None) or []:
        if isinstance(b, dict) and "tool" in b:
            actual[str(b["tool"])] = str(b.get("connector", ""))
    return [
        Check(name=f"binding:{tool}", ok=actual.get(tool) == conn, detail=f"expected {conn}, got {actual.get(tool)!r}")
        for tool, conn in e.bindings.items()
    ]


def _dag_ok(nodes: dict[str, str], edges: list[tuple[str, str]]) -> bool:
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        adj[a].append(b)
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return False
        if state.get(n) == 2:
            return True
        state[n] = 1
        ok = all(visit(m) for m in adj[n])
        state[n] = 2
        return ok

    return all(visit(n) for n in nodes)


def _workflow_checks(w: WorkflowExpect, obj: Any) -> list[Check]:
    nodes_raw = obj.get("nodes") if isinstance(obj, dict) else None
    edges_raw = obj.get("edges") if isinstance(obj, dict) else None
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        return [Check(name="workflow_shape", ok=False, detail="needs nodes[] and edges[]")]
    nodes = {str(n.get("id")): str(n.get("type", "")) for n in nodes_raw if isinstance(n, dict)}
    edges = [(str(x.get("from")), str(x.get("to"))) for x in edges_raw if isinstance(x, dict)]
    dangling = [f"{a}->{b}" for a, b in edges if a not in nodes or b not in nodes]
    bad_types = sorted({t for t in nodes.values() if t not in WORKFLOW_NODE_TYPES})
    used = {str(n.get("tool")) for n in nodes_raw if isinstance(n, dict) and n.get("tool")}
    unknown_tools = sorted(used - set(w.known_tools)) if w.known_tools else []
    missing_types = [t for t in w.required_types if t not in nodes.values()]
    missing_tools = [t for t in w.must_use_tools if t not in used]
    return [
        Check(name="workflow_shape", ok=bool(nodes) and len(nodes) <= w.max_nodes, detail=f"{len(nodes)} nodes"),
        Check(name="workflow_node_types", ok=not bad_types, detail=f"unknown types {bad_types}" if bad_types else ""),
        Check(name="workflow_edges_resolve", ok=not dangling, detail=f"dangling {dangling[:3]}" if dangling else ""),
        Check(name="workflow_acyclic", ok=not dangling and _dag_ok(nodes, edges)),
        Check(name="workflow_required_types", ok=not missing_types, detail=f"missing {missing_types}"),
        Check(name="no_hallucinated_tools", ok=not unknown_tools, detail=f"invented {unknown_tools}"),
        Check(name="workflow_uses_tools", ok=not missing_tools, detail=f"missing {missing_tools}"),
    ]


_STRUCTURED: list[tuple[Callable[[Expect], bool], Callable[[Expect, Any], list[Check]]]] = [
    (lambda e: e.schema_ is not None, lambda e, o: [_schema_check(e.schema_ or {}, o)]),
    (lambda e: e.json_equals is not None, lambda e, o: [Check(name="json_equals", ok=_same(o, e.json_equals))]),
    (lambda e: bool(e.json_fields or e.json_required), _field_checks),
    (lambda e: bool(e.known_tools or e.known_connectors), _allowlist_checks),
    (lambda e: bool(e.bindings), _binding_checks),
    (lambda e: e.workflow is not None, lambda e, o: _workflow_checks(e.workflow or WorkflowExpect(), o)),
]


def _structured_checks(e: Expect, text: str) -> list[Check]:
    if not _needs_json(e):
        return []
    obj, problem = _parse_json(text, strict=e.json_only)
    if obj is None:
        return [Check(name="json_parsable", ok=False, detail=problem)]
    checks = [Check(name="json_parsable", ok=True)]
    for applies, fn in _STRUCTURED:
        if applies(e):
            checks.extend(fn(e, obj))
    return checks


# ---- entry point ------------------------------------------------------------------------------
def evaluate(task: EvalTask, t: Transcript) -> TaskResult:
    result = TaskResult(
        task_id=task.id,
        category=task.category.value,
        language=task.language,
        family=task.family,
        error=t.error,
        latency_ms=t.latency_ms,
        input_tokens=t.input_tokens,
        output_tokens=t.output_tokens,
        rounds=t.rounds,
        final_text=t.final_text,
        tool_calls=t.called,
    )
    if t.error:
        return result
    e = task.expect
    declared = {tool.name for tool in task.tools}
    result.checks = _tool_checks(e, t, declared) + _text_checks(e, t.final_text) + _structured_checks(e, t.final_text)
    if t.truncated:
        result.checks.append(Check(name="turn_completed", ok=False, detail="still calling tools at the round limit"))
    elif not t.final_text.strip():
        result.checks.append(Check(name="non_empty_reply", ok=False))
    if e.needs_judge():
        result.judge_skipped = [
            n for n, v in (("refuses", e.refuses), ("lang", e.lang), ("rubric", e.rubric)) if v is not None
        ]
    return result
