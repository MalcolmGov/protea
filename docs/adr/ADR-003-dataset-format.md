# ADR-003 — Training examples are chat messages with tool-call blocks plus a provenance envelope

**Status:** accepted · **Date:** 2026-09-06

## Context
Spec §8 requires every example to be traceable; §15 requires a conversation representation with explicit tool invocation; strategy-review A3 requires the training format to match the serving format.

## Decision
- One JSONL line per example: `{"metadata": {...}, "messages": [...], "tools": [...]}` validated by `protea.schemas.examples.TrainingExample`.
- `messages` use the provider-neutral `Message` shape (OpenAI-style `tool_calls`, `role="tool"` results) so the same object feeds providers, evaluation and the trainer's chat-template rendering.
- `metadata` carries id, dataset_version, source repo/commit/path/id, family (dedup and split key), domain, task_type, difficulty, language, synthetic + generator_model, review/pii/secret/license status, split, duplicate_of. Non-synthetic examples must carry repo and path; synthetic ones must name their generator.
- Splits are `train / validation / test / golden`; golden ids are surfaced by validation so CI can assert no training manifest references them.
- Rendering to a model's native template (Qwen3 Hermes tool format, etc.) happens at training time from this canonical form, never by hand-authoring template text.

## Alternatives
- ShareGPT / Alpaca formats: no first-class tool calls or provenance.
- Provider-specific formats (Anthropic content blocks): would tie the dataset to one vendor.

## Consequences
- `protea dataset validate` and `stats` work on any file in this format today.
- Language tags are an explicit allowlist so the Africa specialisation track (§60) tags data consistently from the first example.
