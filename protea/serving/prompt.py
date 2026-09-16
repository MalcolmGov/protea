"""Product system-prompt overlay for the serving path (ADR-017: v0 = frozen base + guardrail prompt).

This is the serving half of the pair whose eval half lives in `protea/evaluation/runner.py`. Both apply the
overlay the same way — prepended to the caller's own system message, never replacing it — so a product-config
eval scores the base under exactly the framing it runs with in production (Exp 0, `docs/experiments/`).

Why merge rather than replace: aria sends a per-agent system prompt, and the estate's agents each carry their
own instructions. The guardrail prompt is a *deployment-wide floor* on top of those, not a substitute for them.

Two properties worth keeping:

* **Idempotent.** A caller that already sends the prompt (or a second wrap applied by the CLI path) does not
  get it twice; the verbatim check is cheap and runs on every request.
* **Streaming-preserving.** `stream()` delegates to the inner provider's stream, so the overlay never silently
  degrades the facade's SSE path into a single buffered chunk.
"""

from __future__ import annotations

from pathlib import Path

from protea.providers.base import ModelProvider
from protea.schemas.generation import GenerationRequest, GenerationResponse, Message


def with_system_prompt(request: GenerationRequest, prompt: str) -> GenerationRequest:
    """Merge `prompt` into the request's system message (or add one). Idempotent for an identical prompt."""
    if not prompt:
        return request
    messages = list(request.messages)
    if any(m.role == "system" and prompt in (m.content or "") for m in messages):
        return request
    if messages and messages[0].role == "system":
        merged = f"{prompt}\n\n{messages[0].content or ''}"
        messages[0] = Message(role="system", content=merged)
    else:
        messages.insert(0, Message(role="system", content=prompt))
    return request.model_copy(update={"messages": messages})


class PromptedProvider(ModelProvider):
    """Applies one deployment's system prompt to every request before the backend sees it.

    Sits inside the tool guard (the guard must inspect what the model produced under this prompt), and mirrors
    `GuardedProvider`: `name`/`supports_native_json_schema` are the inner provider's so routing decisions and
    native-schema support are unchanged, and usage is emitted once, by the inner provider.
    """

    def __init__(self, inner: ModelProvider, prompt: str):
        super().__init__(model=inner.model, usage_sink=inner.usage_sink)
        self.inner = inner
        self.prompt = prompt.strip()
        self.name = inner.name
        self.supports_native_json_schema = inner.supports_native_json_schema

    def _with_schema_instruction(self, request: GenerationRequest) -> GenerationRequest:
        return self.inner._with_schema_instruction(request)

    async def health(self):  # pragma: no cover - passthrough
        return await self.inner.health()

    async def stream(self, request: GenerationRequest):  # type: ignore[override]
        async for chunk in self.inner.stream(with_system_prompt(request, self.prompt)):
            yield chunk

    async def _generate(self, request: GenerationRequest) -> GenerationResponse:
        return await self.inner.generate(with_system_prompt(request, self.prompt))

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        # The inner provider already emitted usage; the overlay only rewrites the prompt.
        return await self._generate(request)


def resolve_system_prompt(system_prompt: str | None, system_prompt_file: str | None) -> str:
    """The prompt text for a deployment: the file's contents when given, else the inline string, else none."""
    if system_prompt_file:
        path = Path(system_prompt_file)
        if not path.exists():
            raise ValueError(f"serve.system_prompt_file does not exist: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"serve.system_prompt_file is empty: {path}")
        return text
    return (system_prompt or "").strip()


def maybe_prompted(provider: ModelProvider, prompt: str) -> ModelProvider:
    """Wrap `provider` with the overlay unless there is no prompt or it is already applied (idempotent)."""
    if not prompt:
        return provider
    if isinstance(provider, PromptedProvider) and provider.prompt == prompt.strip():
        return provider
    return PromptedProvider(provider, prompt)


__all__ = ["PromptedProvider", "maybe_prompted", "resolve_system_prompt", "with_system_prompt"]
