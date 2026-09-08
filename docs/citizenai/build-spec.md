# SA CitizenAI — v0 build specification (Pillar 11)

**Date:** 2026-09-08 · **Status:** Phase 0 complete; Phases 1–4 planned. · **Decision of record:** `docs/adr/ADR-012-citizenai-government-domain.md`

Status vocabulary: `done` · `in progress` · `planned` · `blocked (needs approval / real data)`.

CitizenAI is a free, multilingual assistant for South African government services (SASSA grants, SARS tax, UIF,
Home Affairs, Eskom outages, municipal rates) over WhatsApp and USSD. This spec turns the ADR-012 decision — that
CitizenAI is a **government domain on the Protea platform, not a separate model** — into a phased, executable build
with explicit stop-and-confirm boundaries. It is deliberately honest about what is a design task (free), what needs
real government data or partners, and what spends money.

## 0. Scope and non-goals

**In scope for v0:** grounded answers to common questions in the six domains above, in a growing set of the 11
official languages, over WhatsApp and USSD, free to the citizen, with no login and no PII retained after a session.

**Explicit non-goals for v0 (from ADR-012):**
- No separate language model — CitizenAI rides the shared Protea base model via a government LoRA adapter.
- No custom "PA-BPE" tokenizer — parked behind measurement (ADR-012 decision 3); a new tokenizer would force a
  separate, GPU-heavy model build, and the strategy's token-saving figures are unverified.
- No facts baked into weights — grant dates, deadlines and stages are retrieved and cited, never memorised.
- No fabricated statistics — the strategy's commercial figures (per-query SLA, CSI and grant amounts) are **targets
  to validate with the counterparties**, not claims this build asserts.

## 1. What already exists (Phase 0 — done)

| Artefact | Where | What it gives |
|---|---|---|
| Architecture decision | `docs/adr/ADR-012-citizenai-government-domain.md` | reuse-on-Protea; facts by retrieval; tokenizer parked; sovereignty is operational |
| The whole Protea platform | this repo | base model, vLLM facade, router, tool-permission guard, tenant scoping, observability, release pipeline, economics |
| Working prototype | published artifact (USSD + WhatsApp demo) | shows the grounded, multilingual, zero-PII flow end to end on a mock backend |
| CitizenBench 0.1 | `protea/evaluation/citizen.py`, `configs/evaluation/citizen-0.1.yaml`, `evaluation/citizen/0.1/` | a deterministic bar: does the agent retrieve the fact, surface it, and refuse to invent — tagged by language |

So the measurement, the architecture and the demo are done. Phases 1–4 build the real thing against them.

## 2. Architecture and data flow

```
[ Citizen: feature phone USSD *120*…#  |  WhatsApp ]
        |  language auto-detect · zero-rated · no login · no PII kept
        v
[ Channel gateway ]  USSD session state + ~182-char chunking · WhatsApp Business API
        |  end-user ref (salted hash), never the raw number  (aria end-user scoping)
        v
[ Protea facade ]  tool-permission guard · router · observability
        |
        v
[ Government adapter on the shared base model (multi-LoRA) ]
        |  the model PHRASES; it must call a retrieval tool for every fact
        v
[ Government retrieval layer ]  authoritative sources + connectors
   SASSA · SARS · Home Affairs/eNaTIS · Eskom · municipal · legislation
        |
        v
[ Grounded, cited answer in the citizen's language ]  (nothing retained after the session)
```

Two invariants carry the correctness story:
- **Every fact is retrieved and cited.** The system prompt and CitizenBench both forbid inventing a date, amount or
  stage; the retrieval layer is the source of truth, not the weights.
- **Nothing personal is stored.** Sessions are ephemeral; the caller reaches the runtime only as a salted reference
  (the aria end-user scoping already merged), never a raw phone number.

## 3. The build, in phases

Each phase begins with inspect → findings → ADR (if a real decision) → file list → incremental implementation →
tests → docs, and ends at any spend/data boundary for confirmation.

### Phase 1 — Government knowledge & retrieval layer (mostly free; real data needs sources)
1. **Source map & correctness policy.** For each domain, name the authoritative source and its update cadence
   (e.g. SASSA payment schedule, SARS filing-season calendar, Eskom/municipal loadshedding blocks). A `docs/citizenai/
   sources.md` register with owner, format, refresh interval and a human-verification step.
2. **Retrieval index + grounding contract.** A retrieval service that, given an intent, returns the fact plus its
   source and as-of date; the answer must cite them. Build the interface and a stub over the sample facts first
   (the prototype's knowledge base is the seed), then swap real sources in behind it.
3. **Government connectors.** SASSA, SARS eFiling, Home Affairs/eNaTIS, Eskom, municipal — defined in Protea's
   connector pattern; live bridges are `blocked (needs data)` until access is granted, stubbed until then.
4. **Extend CitizenBench** as sources land: real intents, more languages (verified strings only), and staleness
   checks (an answer must carry an as-of date).

### Phase 2 — Government adapter (blocked: needs a GPU run, gated)
1. **Dataset.** Retrieval-grounded Q&A across the six domains and the covered languages, built through the existing
   `protea dataset` pipeline; the teacher policy is the same open decision as the commercial tier
   (`docs/data-governance.md`).
2. **Train.** One LoRA adapter on the **shared base model** (the same Qwen3-8B the commercial tier trains), via
   `protea train remote --confirm`. **Execution boundary:** a rented GPU, ~USD 10–40, quoted by `--dry-run` first.
3. **Measure.** CitizenBench 0.1 (grounding, anti-confabulation, scope) plus the language slices, and the security
   suite. Promotion follows the normal release pipeline; a CitizenAI deployment is gated on a passing CitizenBench
   report for the exact served adapter.

### Phase 3 — Channels & zero-PII runtime (real code; WhatsApp API has a cost)
1. **USSD gateway.** A thin service mapping menu-driven, ~182-char feature-phone sessions to the facade — language
   menu, session state, chunked answers. No smartphone, no data required.
2. **WhatsApp Business API.** Inbound/outbound via the facade. **Execution boundary:** the WhatsApp API and the
   zero-rating arrangement carry per-message and reverse-billing costs — a commercial decision with MTN/Vodacom.
3. **Zero-PII ephemeral mode.** A runtime policy: no session persisted after it ends, POPIA-by-design, building on
   the privacy-safe logging default (Phase 9) and the salted end-user reference already merged in aria.

### Phase 4 — Sovereign deployment & pilot (blocked: partners, procurement, hosting)
1. **Isolated deployment.** A separately hosted, separately governed instance of the Protea stack (ADR-012 decision
   4) — on-SA-soil hosting, its own accreditation, no commingling with the commercial tier.
2. **Language pilot.** Launch on the languages CitizenBench actually covers, expanding as verified translations and
   grounding scores land — never a machine-guessed language in front of citizens.
3. **Partner tracks (external, not engineering):** DPSA/GCIS SLA, MTN/Vodacom zero-rating, CSI/development-finance
   sponsorship. These are the strategy's commercial phases; the figures in the pillar are targets to validate with
   the counterparties, recorded here as assumptions, not results.

## 4. Correctness & data governance

Government answers have real human cost when wrong (a missed grant, a missed deadline), so correctness is the
programme's first constraint, ahead of breadth:
- **Retrieval is the source of truth**; the model never states a fact it did not retrieve, and every answer carries a
  source and an as-of date.
- **Human-verified sources.** Each source in `sources.md` has a named owner and a verification step before it feeds
  answers; sample/illustrative data is labelled as such and never served as live.
- **The eval gate.** CitizenBench must pass for the served adapter before any deployment; the grounding and
  anti-confabulation families are the release floor for the domain.

## 5. Language expansion

v0 covers en-ZA, Afrikaans and isiZulu in full (isiXhosa partial) with **verified** question strings. The rule is
quality over fake breadth: a language reaches citizens only once its questions and its grounding are verified by a
speaker and measured in CitizenBench, not machine-translated. The slice and the prototype are structured to take the
remaining official languages one verified set at a time.

## 6. Execution boundaries (stop-and-confirm)

| Action | Cost / exposure | Boundary |
|---|---|---|
| Government adapter training | rented GPU, ~USD 10–40 | `train remote --dry-run` then explicit `--confirm` |
| Ingesting live government data | data access, correctness liability | per-source approval + human verification in `sources.md` |
| WhatsApp Business API + zero-rating | per-message + reverse-billing cost | commercial agreement (MTN/Vodacom); not enabled by code |
| Sovereign hosting | on-SA-soil infrastructure, accreditation | separate deployment decision; never provisioned automatically |
| Production launch | citizens rely on the answers | passing CitizenBench for the served adapter + sign-off |

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Stale facts served as current | a citizen acts on a wrong date/amount | retrieval-only facts; as-of dates; source refresh cadence; CitizenBench staleness checks |
| Machine-guessed low-resource languages | wrong or offensive output at scale | verified strings only; language gated on CitizenBench coverage |
| Cross-person / PII leakage | privacy breach, POPIA violation | zero-PII ephemeral mode; salted end-user ref; the merged tenant-scoping fix; the security suite |
| Custom-tokenizer temptation | a separate, expensive model programme | ADR-012 decision 3 — measure the token penalty first; default stays reuse |
| Zero-rating / SLA dependency | no reach or no funding without partners | tracked as external commercial phases; engineering does not block on them |
| Over-claiming commercial numbers | credibility with government | figures are stated as targets to validate, never as results |

## 8. Success measures

- **Grounding rate** on CitizenBench (retrieved-and-cited, no invented facts) — the release floor for the domain.
- **Language coverage** — count of languages with verified questions and a passing grounding score.
- **Deflection & cost per answered query** — measured against the economics model once real volumes exist.
- **Reach** — share of answers served over USSD (feature-phone) vs WhatsApp.

## 9. What v0 is not

Not a separate LLM; not a custom tokenizer; not facts-in-weights; not a live government service until a source
register and a passing eval exist; and not a set of asserted commercial figures. It is the government domain of the
platform this repository already builds, measured by its own bar, deployed under its own sovereignty boundary.
