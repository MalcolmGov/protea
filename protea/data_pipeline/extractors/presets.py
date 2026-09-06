"""generated-presets.ts → {agentId: [{tool, connector}]}. The generated array is JSON, so no TS parser is needed."""

from __future__ import annotations

import json
from pathlib import Path


def _array_text(text: str) -> str:
    """Slice the JSON array assigned to GENERATED_PRESETS with plain string searches (linear, no backtracking)."""
    anchor = text.find("GENERATED_PRESETS")
    if anchor == -1:
        raise ValueError("GENERATED_PRESETS not found")
    eq = text.find("=", anchor)
    start = text.find("[", eq if eq != -1 else anchor)
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("GENERATED_PRESETS array not found")
    return text[start : end + 1]


def load_presets(path: Path) -> dict[str, list[dict[str, str]]]:
    try:
        data = json.loads(_array_text(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    out: dict[str, list[dict[str, str]]] = {}
    for preset in data:
        bindings = [
            {"tool": b["tool"], "connector": b["connector"]}
            for b in preset.get("bindings", [])
            if "tool" in b and "connector" in b
        ]
        out[preset["agentId"]] = bindings
    return out
