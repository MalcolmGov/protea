"""generated-presets.ts → {agentId: [{tool, connector}]}. The generated array is JSON, so no TS parser is needed."""

from __future__ import annotations

import json
import re
from pathlib import Path

_ARRAY = re.compile(r"GENERATED_PRESETS\s*:\s*GeneratedPreset\[\]\s*=\s*(\[.*\])\s*;?\s*$", re.DOTALL)


def load_presets(path: Path) -> dict[str, list[dict[str, str]]]:
    text = path.read_text(encoding="utf-8")
    m = _ARRAY.search(text)
    if not m:
        raise ValueError(f"{path}: GENERATED_PRESETS array not found")
    data = json.loads(m.group(1))
    out: dict[str, list[dict[str, str]]] = {}
    for preset in data:
        bindings = [
            {"tool": b["tool"], "connector": b["connector"]}
            for b in preset.get("bindings", [])
            if "tool" in b and "connector" in b
        ]
        out[preset["agentId"]] = bindings
    return out
