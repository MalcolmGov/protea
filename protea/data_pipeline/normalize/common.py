"""Shared pieces for normalisers: default system prompts, metadata factory, JSON rendering."""

from __future__ import annotations

import json
from typing import Any

from protea.data_pipeline.discovery import Artifact
from protea.schemas.examples import ExampleMetadata, LicenseStatus, ScanStatus, TaskType

DEFAULT_PROMPTS: dict[str, str] = {
    "agent_generation": (
        "You are Protea, the agent architect for the Zara platform. Given a business brief, design a production agent "
        "and answer with a single JSON object that matches the requested schema. Only use tools and connectors that exist "
        "in the brief; never invent integrations. Add guardrails for anything financial, irreversible or personal."
    ),
    "structured_output": (
        "You are Protea. Answer with one JSON object that validates against the schema given in the request. "
        "No prose, no code fences."
    ),
    "connector_selection": (
        "You are Protea. For each agent tool, choose the connector from the available catalogue that should execute it. "
        'Use \'webhook\' only when no catalogue connector fits. Answer with JSON: {"bindings": [{"tool": ..., "connector": ...}]}.'
    ),
    "routing": (
        "You are Zara's message router. Classify the user's message into exactly one lane from the list and answer with "
        'JSON: {"lane": <lane_id>}.'
    ),
    "tool_calling": (
        "You are {agent_name}, an AI agent for a business. Follow the operating instructions and use tools only when "
        "the instructions and the customer's request justify it. Never invent order numbers, prices or availability."
    ),
}


def render_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


def make_metadata(
    art: Artifact,
    *,
    dataset_version: str,
    source_type: str,
    source_id: str,
    family: str | None,
    domain: str,
    task_type: TaskType,
    language: str = "en",
    difficulty: str = "medium",
    redactions: dict[str, int] | None = None,
) -> ExampleMetadata:
    return ExampleMetadata(
        dataset_version=dataset_version,
        source_type=source_type,
        source_repo=art.repo,
        source_commit=art.commit,
        source_path=art.relpath,
        source_id=source_id,
        family=family,
        domain=domain,
        task_type=task_type,
        difficulty=difficulty,
        language=language,
        synthetic=False,
        license_status=LicenseStatus(art.license_status)
        if art.license_status in ("approved", "restricted", "unknown")
        else LicenseStatus.UNKNOWN,
        pii_scan=ScanStatus.PASSED,
        secret_scan=ScanStatus.PASSED,
        rule_checks=ScanStatus.PASSED,
        redactions=redactions or {},
    )
