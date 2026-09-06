"""Validation gate (spec §29, §33): structured output is checked against its JSON Schema and repaired once
before anything reaches a caller. A response that still fails is an explicit 422, never a silent best effort."""

from __future__ import annotations

from typing import Any

import jsonschema
from pydantic import BaseModel, Field

from protea.providers.base import ModelProvider, extract_json
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message

MAX_REPAIR_ROUNDS = 3  # hard ceiling; callers may ask for fewer, never more


class GateResult(BaseModel):
    valid: bool
    output: Any | None = None
    errors: list[str] = Field(default_factory=list)
    repairs: int = 0
    raw: str | None = None
    responses: list[GenerationResponse] = Field(default_factory=list)


def schema_errors(schema: dict[str, Any], obj: Any) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(map(str, e.path)) or '$'}: {e.message}"
        for e in sorted(validator.iter_errors(obj), key=lambda e: list(e.path))
    ][:10]


def parse_and_validate(schema: dict[str, Any], text: str | None) -> tuple[Any | None, list[str]]:
    try:
        obj = extract_json(text)
    except ValueError as exc:
        return None, [f"$: not valid JSON ({str(exc)[:80]})"]
    errors = schema_errors(schema, obj)
    return (obj if not errors else None), errors


def repair_request(request: GenerationRequest, raw: str | None, errors: list[str]) -> GenerationRequest:
    """Ask the same model to fix its own output: the failing text and the validator's messages, nothing else."""
    msgs = list(request.messages)
    msgs.append(Message(role="assistant", content=raw or ""))
    msgs.append(
        Message(
            role="user",
            content="Your previous answer did not validate against the required JSON Schema:\n- "
            + "\n- ".join(errors)
            + "\nReturn the corrected JSON object only.",
        )
    )
    return request.model_copy(update={"messages": msgs})


async def generate_validated(
    provider: ModelProvider, request: GenerationRequest, schema: dict[str, Any], *, max_repairs: int = 1
) -> GateResult:
    req = request.model_copy(update={"response_schema": schema, "response_schema_name": request.response_schema_name})
    if not provider.supports_native_json_schema:
        req = provider._with_schema_instruction(req)  # the provider's own prompt-level fallback
    rounds = min(max(int(max_repairs), 0), MAX_REPAIR_ROUNDS)
    result = GateResult(valid=False)
    for attempt in range(rounds + 1):
        resp = await provider.generate(req)
        result.responses.append(resp)
        obj, errors = parse_and_validate(schema, resp.content)
        result.raw = resp.content
        result.errors = errors
        if not errors:
            result.valid = True
            result.output = obj
            result.repairs = attempt
            return result
        if attempt < rounds:
            req = repair_request(req, resp.content, errors)
    result.repairs = rounds
    return result
