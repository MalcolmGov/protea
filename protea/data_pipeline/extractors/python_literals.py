"""Read module-level literal structures from Python source WITHOUT importing it (registries, corpus, flagship specs)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def _find_assignment(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and node.targets:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            return node.value
    return None


def _call_to_dict(node: ast.Call) -> dict[str, Any] | None:
    func = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "call")
    if func == "field":  # dataclasses.field(default_factory=...) → unknown, skip
        return None
    out: dict[str, Any] = {"__type__": func}
    for kw in node.keywords:
        if kw.arg:
            out[kw.arg] = _to_value(kw.value)
    return out


def _to_value(node: ast.expr | None) -> Any:
    """Convert literals, lists, dicts and dataclass-style Call(kw=literal) nodes into plain Python values."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_to_value(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_to_value(k): _to_value(v) for k, v in zip(node.keys, node.values, strict=True)}
    if isinstance(node, ast.Call):
        return _call_to_dict(node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value
    return None


def load_literal(path: Path, variable: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = _find_assignment(tree, variable)
    if node is None:
        raise ValueError(f"{path}: no module-level assignment named {variable}")
    return _to_value(node)


def registry_entries(path: Path, variable: str) -> list[dict[str, Any]]:
    """A dict of dataclass calls → list of plain dicts (without __type__)."""
    data = load_literal(path, variable)
    if not isinstance(data, dict):
        raise ValueError(f"{path}:{variable} is not a dict literal")
    out = []
    for key, value in data.items():
        if isinstance(value, dict):
            entry = {k: v for k, v in value.items() if k != "__type__"}
            entry.setdefault("id", key)
            out.append(entry)
    return out
