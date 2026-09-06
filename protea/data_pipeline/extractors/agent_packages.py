"""Agent packages: aria `*.agent.json` (zara.agent-package/v1) and miai-agents package directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

MARKET_PREFIXES = ("africa", "asia", "eu", "oceania", "us")
ACCEPTED_FORMATS = {"zara.agent-package/v1", "miai.agent-package/v1"}


class AgentPackage(BaseModel):
    id: str
    family: str
    market: str | None = None
    name: str
    version: str = "1.0.0"
    category: str = "general"
    tier: str = "standard"
    summary: str = ""
    channels: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    compliance: list[str] = Field(default_factory=list)
    model: dict[str, Any] = Field(default_factory=dict)
    handoff: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = ""
    knowledge: str = ""
    tools: list[dict[str, Any]] = Field(default_factory=list)
    guardrails: str = ""
    evals: list[dict[str, Any]] = Field(default_factory=list)
    format: str = "zara.agent-package/v1"
    source_path: str = ""

    @property
    def tool_names(self) -> set[str]:
        return {t.get("name", "") for t in self.tools}


def family_of(agent_id: str, market: str | None = None) -> tuple[str, str | None]:
    for prefix in MARKET_PREFIXES:
        if agent_id.startswith(prefix + "-"):
            return agent_id[len(prefix) + 1 :], prefix
    return agent_id, market


def _from_parts(manifest: dict[str, Any], parts: dict[str, Any], fmt: str, source_path: str) -> AgentPackage:
    agent_id = manifest.get("id") or Path(source_path).stem.replace(".agent", "")
    family, market = family_of(agent_id, manifest.get("market"))

    def text(key: str, default: str = "") -> str:
        return manifest.get(key) or default

    def items(key: str) -> list[Any]:
        return list(manifest.get(key) or [])

    return AgentPackage(
        id=agent_id,
        family=family,
        market=market,
        name=text("name", agent_id),
        version=str(text("version", "1.0.0")),
        category=text("category", "general"),
        tier=text("tier", "standard"),
        summary=text("summary"),
        channels=items("channels"),
        languages=items("languages"),
        compliance=items("compliance"),
        model=dict(manifest.get("model") or {}),
        handoff=dict(manifest.get("handoff") or {}),
        system_prompt=parts.get("system_prompt") or "",
        knowledge=parts.get("knowledge") or "",
        tools=list(parts.get("tools") or []),
        guardrails=parts.get("guardrails") or "",
        evals=[e for e in (parts.get("evals") or []) if isinstance(e, dict)],
        format=fmt,
        source_path=source_path,
    )


def load_package_json(path: Path, relpath: str) -> AgentPackage:
    data = json.loads(path.read_text(encoding="utf-8"))
    fmt = data.get("format") or ""
    if fmt not in ACCEPTED_FORMATS:
        raise ValueError(f"{relpath}: unsupported package format {fmt!r}")
    return _from_parts(data.get("manifest") or {}, data, fmt, relpath)


def load_package_dir(manifest_path: Path, relpath: str) -> AgentPackage:
    """miai-agents layout: manifest.json referencing system_prompt.md, knowledge.md, tools.json, guardrails.md, evals.jsonl."""
    folder = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def read(key: str, default: str) -> str:
        p = folder / (manifest.get(key) or default)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    tools_text = read("tools", "tools.json")
    evals_text = read("evals", "evals.jsonl")
    evals = []
    for line in evals_text.splitlines():
        line = line.strip()
        if line:
            try:
                evals.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    parts = {
        "system_prompt": read("prompt", "system_prompt.md"),
        "knowledge": read("knowledge", "knowledge.md"),
        "tools": json.loads(tools_text) if tools_text.strip() else [],
        "guardrails": read("guardrails", "guardrails.md"),
        "evals": evals,
    }
    return _from_parts(manifest, parts, "miai.agent-package/v1", relpath)
