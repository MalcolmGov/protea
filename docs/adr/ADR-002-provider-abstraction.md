# ADR-002 — One async `ModelProvider` contract; adapters wrap vendor SDKs or plain HTTP

**Status:** accepted · **Date:** 2026-09-06

## Context
`aria` reaches models through four unconnected layers (audit §1.9). Protea needs one contract that Zara, Gaslite and future consumers call, that the router can swap at runtime, and that the evaluation harness can run identically against every model.

## Decision
- `protea.providers.base.ModelProvider`: async `generate`, `stream`, `generate_structured(request, PydanticModel)`, `health`. Adapters implement `_generate`; streaming and structured output have working defaults (schema-in-prompt plus JSON extraction and validation) that adapters override with native support.
- A provider-neutral request/response schema (`protea.schemas.generation`) with OpenAI-style tool calls as the canonical shape. Tool results are `role="tool"` messages.
- Adapters: `MockProvider` (tests only), `OpenAICompatibleProvider` (OpenAI, vLLM/Protea, Ollama, gateways), `AzureOpenAIProvider`, `AnthropicProvider` (official SDK, native `output_config.format` JSON schema), `GoogleProvider` (generateContent REST, `responseSchema`).
- Every call emits a `UsageEvent` to a pluggable `UsageSink`. Events never carry prompt or completion text. `aria` plugs its `log_api_usage` in as a sink.
- `build_provider(name)` is the only construction path; secrets come from environment settings, never from YAML.

## Alternatives
- LiteLLM or a similar proxy: rejected for v0 — another moving dependency between Zara and the model, and no control over the usage record or structured-output semantics.
- OpenAI-compatible surface only (as the TypeScript runtime does for Anthropic): rejected because it loses Anthropic's native JSON-schema output and typed errors.

## Consequences
- Structured-output failures raise `StructuredOutputError` with the raw text so the router can fall back and the event is recorded.
- Streaming with tool calls is supported natively on OpenAI-compatible and Anthropic adapters; Google streams via the default (non-token) path until needed.
