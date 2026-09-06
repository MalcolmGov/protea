# ADR-006 — Allowlist extraction, field-level classification, and eval-seeded synthetic tool-calling data

**Status:** accepted · **Date:** 2026-09-06

## Context
Spec §5 forbids the repositories from becoming training data by default. The audited sources hold three kinds of material: authored agent packages (prompts, tools, guardrails), fictional but realistic business knowledge, and evals that name the expected tool but never its arguments or the reply text. The upstream catalogue evals were also "healed" against a mock model.

## Decision
1. **Allowlist discovery.** Only paths named by an `ExtractorSpec` in the dataset config are read. A deny-list (`.env`, `.git`, databases, seeds, credentials) is applied on top. Source roots are pinned to the local clone's git commit, recorded on every example.
2. **Field-level classification.** Each package field gets its own decision: manifest, system prompt, tools and guardrails default to `SAFE_FOR_TRAINING`; knowledge is `RAG_ONLY` and only ever appears as context; evals are `REQUIRES_REVIEW` unless the source is marked `authored`. Secrets block the artefact; PII is redacted with deterministic synthetic values (or placeholders, or blocks) before anything is emitted.
3. **Natural examples** come from packages (agent generation, manifest structured output), presets plus the connector catalogue (connector selection), the routing corpus (routing) and flagship specs (structured output).
4. **Tool calling is eval-seeded synthetic.** Evals become validated seeds: system prompt, tools, input, follow-ups and the `expect` block. A teacher model completes the turn; the completion is kept only if it satisfies the eval grammar (`tool`, `tool_any`, `tool_none`, `no_tool`, `says_any`, `says_none`). Contaminated seeds (knowledge lines built from eval phrases) are flagged and skipped by default. The generator model is recorded and the licence status stays `unknown` until the teacher policy sets it.
5. **Family-level splits and golden.** Split assignment hashes `task_type:family` so market variants never straddle splits. Golden examples are drawn from the test split per task type, listed in the manifest, and `protea dataset golden-check` fails on any overlap.
6. **Near-duplicate targets** are marked by shingle Jaccard within task type and dropped from output files.

## Alternatives
- Whole-repository crawl with a classifier: rejected; the allowlist is smaller, auditable, and the spec's exclusion list is enforced by construction.
- Fabricating tool arguments from schemas for natural tool-calling examples: rejected; it would teach invented arguments.
- Embedding knowledge in targets: rejected (spec §43).

## Consequences
- `protea dataset build` is deterministic for a given config and source commits; the manifest records hashes.
- Tool-calling data cannot exist until the teacher policy is decided; the seeds file makes the gap explicit.
- Datasets register as `draft`; promotion to `approved` requires the sample human review the spec demands.
