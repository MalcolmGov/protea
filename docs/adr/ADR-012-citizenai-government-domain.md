# ADR-012 — SA CitizenAI as a government domain on Protea, not a separate model

**Status:** proposed · **Date:** 2026-09-08 · **Scope:** Pillar 11 (SA CitizenAI); no build phase yet

## Context

Pillar 11 (SA CitizenAI) proposes a free, zero-rated government-services assistant for South African citizens in
all 11 official languages, over WhatsApp and USSD, answering on SASSA grants, SARS tax, UIF, Home Affairs, Eskom
outages and municipal rates. The pillar's own diagram names a "PA-BPE Sovereign LLM (Government Fine-Tune)". The
question this record settles: does CitizenAI need a separate language model, or does it reuse the Protea platform
already built for Zara?

The Protea platform (Phases 1–10) is a base model plus hot-swappable LoRA adapters, served behind a facade with a
router, a tool-permission guard, tenant scoping, observability and an economic model. Multilingual coverage of the
SA languages (af, zu, xh, st, tn, ve, sw and more) is already a stated goal and a tracked risk (roadmap §7). The
government use case is, in platform terms, a large multilingual, low-cost, high-scale instance of exactly what the
platform is built to deliver.

## Decisions

1. **CitizenAI reuses the Protea platform; it is not a separate language model.** It is built as a government domain
   on the same base model and the same serving stack (facade, router, guard, tenant scoping, observability,
   economics). What is new is additive: a government-domain LoRA adapter, a government-facts retrieval layer, the
   government API connectors, the WhatsApp and USSD channel gateways, and an isolated deployment. None of these is a
   second model. Multi-LoRA serving (ADR-009) lets the government adapter ride the same GPU as commercial tenants.

2. **Government facts come from retrieval, not from fine-tuning.** Grant payment dates, tax deadlines, outage
   schedules and rates change constantly and must be exactly correct, so they are never baked into frozen weights
   where they go stale and the model quotes last year's SASSA date with confidence. The government adapter is
   fine-tuned for domain vocabulary, tone and language fluency; the *facts* are retrieved at query time from
   authoritative government sources (documents and the SASSA / SARS / Home Affairs / eNaTIS bridges) and the answer is
   grounded in what was retrieved. This corrects the pillar's "fine-tune on all legislation" framing: fine-tune for
   language, retrieve for facts.

3. **The custom "PA-BPE" tokenizer is parked behind measurement, not adopted up front.** A custom byte-pair tokenizer
   trained on SA languages can lower the token-per-word penalty that multilingual tokenizers impose on low-resource
   languages — a real phenomenon worth measuring. But a new tokenizer forces retrained embeddings and continued
   pretraining, which cannot reuse an existing base model's weights: it is a separate, GPU-heavy model build, the one
   choice in this pillar that would break reuse. The pillar's specific figures ("1.15 tok/word", "70% off") are
   unverified and must not enter a government proposal unmeasured. v1 uses the base model's existing tokenizer (Qwen's
   multilingual coverage is adequate) and measures the real token penalty on isiZulu / Xhosa / Sesotho text through
   ZaraBench's language slices. A custom tokenizer becomes its own funded R&D track only if the measured penalty and
   the volume justify it.

4. **Separation is operational (sovereignty), not architectural.** Government will likely require data-sovereignty
   isolation: on-SA-soil hosting, no commingling of citizen data with commercial tenants, separate security
   accreditation, POPIA compliance, and the pillar's zero-PII ephemeral mode (no session persisted after it ends).
   That is a separately hosted, separately governed *instance of the same Protea codebase* with an isolated data plane
   — a government deployment, not a government model. The multi-tenant design and the tool-permission guard already
   support that walling; the zero-PII mode is a runtime policy tightening the privacy-safe logging default (Phase 9),
   not new model work.

## Consequences

- The CitizenAI build plan is additive on Protea: one government-domain dataset and adapter through the existing
  `protea dataset`/`train` pipeline; a retrieval layer and the SASSA/SARS/Home Affairs/eNaTIS connectors; WhatsApp and
  a new thin USSD gateway (menu-driven, ~182-char turns); and an isolated sovereign deployment. It does not fork the
  model or the serving stack.
- The commercial and government tiers share the base model and the platform, so improvements to one (multilingual
  quality, the guard, the router) benefit both; the government adapter is branded "sovereign" while riding the shared
  platform.
- The single item that would turn this into a separate, expensive model programme is a custom tokenizer. Decision 3
  gates that behind measured evidence, so the default path stays reuse.
- Nothing here is built yet; this record fixes the architecture before Phase 1 (corpus and fine-tuning) starts, so the
  team does not begin a separate model or a custom tokenizer by default. The paid steps (a government-adapter training
  run, sovereign hosting) remain execution boundaries requiring explicit approval.
