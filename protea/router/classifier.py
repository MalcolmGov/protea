"""Task classification and complexity estimation from the request shape — rules, no model call (spec §55)."""

from __future__ import annotations

from protea.router.policy import Complexity, RouteRequest, TaskType
from protea.schemas.generation import GenerationRequest

_TASK_HINTS: dict[str, tuple[str, ...]] = {
    "agent_generation": ("design an agent", "business brief", "agentspec", "agent specification", "build an agent"),
    "workflow_generation": ("workflow", "nodes", "edges", "steps the agent should follow"),
    "connector_selection": ("connector", "bindings", "available connectors"),
    "repair": ("did not validate", "corrected json", "fix the following"),
    "routing": ("classify the user's message", "exactly one lane", "lane"),
    "business_reasoning": ("business information", "policy", "pricing", "deadline"),
}
_FINANCIAL_HINTS = ("refund", "payment", "invoice", "charge", "transfer", "payout", "credit note", "voucher")


def classify(request: GenerationRequest, *, hint: str | None = None) -> TaskType:
    """Explicit task_type metadata wins; otherwise shape (tools, schema) then prompt phrases."""
    declared = hint or request.metadata.task_type
    if declared in TaskType.__args__:  # type: ignore[attr-defined]
        return declared  # type: ignore[return-value]
    text = " ".join((m.content or "") for m in request.messages).lower()
    for task, phrases in _TASK_HINTS.items():
        if any(p in text for p in phrases):
            return task  # type: ignore[return-value]
    if request.tools:
        return "tool_calling"
    if request.response_schema:
        return "structured_output"
    return "chat"


def is_financial(request: GenerationRequest) -> bool:
    text = " ".join((m.content or "") for m in request.messages).lower()
    if any(h in text for h in _FINANCIAL_HINTS):
        return True
    return any(any(h in t.name.lower() for h in ("refund", "pay", "charge", "transfer")) for t in request.tools or [])


def complexity(prompt_chars: int, tool_count: int, connector_count: int, financial: bool) -> Complexity:
    """Cheap, explainable score: long prompts, many tools/connectors and money push a call up a tier."""
    score = _tier(prompt_chars, 4_000, 16_000) + _tier(tool_count, 4, 9) + _tier(connector_count, 3, None)
    if financial:
        score += 1
    if score == 0:
        return "low"
    return "medium" if score <= 2 else "high"


def _tier(value: int, medium_from: int, high_from: int | None) -> int:
    """0 below `medium_from`, 1 from there, 2 from `high_from` (when given)."""
    if value < medium_from:
        return 0
    if high_from is not None and value >= high_from:
        return 2
    return 1


def describe(
    request: GenerationRequest, *, hint: str | None = None, privacy: str = "standard", **overrides
) -> RouteRequest:
    """Build the router's view of a generation request."""
    prompt_chars = sum(len(m.content or "") for m in request.messages)
    tools = len(request.tools or [])
    return RouteRequest(
        task_type=classify(request, hint=hint),
        prompt_chars=prompt_chars,
        tool_count=tools,
        connector_count=int(overrides.pop("connector_count", 0)),
        financial=is_financial(request),
        has_schema=request.response_schema is not None,
        privacy=privacy,  # type: ignore[arg-type]
        tenant_ref=request.metadata.tenant_ref,
        **overrides,
    )
