# ADR-014 — The capability spec as Protea's release contract

**Status:** accepted · **Date:** 2026-09-11 · **Scope:** evaluation (ADR-007), release (ADR-011), configuration (ADR-005)

## Context

CLAUDE.md defines Protea as "capability spec + datasets + ZaraBench + training recipes + versioned adapters"
— explicitly not "Qwen + LoRA weights". Four of those five are artifacts in the repo. The **capability spec**
was the one named but never written down: what Protea must be able to do lived implicitly in the ZaraBench
category list and its weights, and in prose across the specs. That left two gaps:

- The eval config's per-category `min_score` gates and `release_min_zarascore` are all `0.0` placeholders
  (ADR-013 holds them there until a frozen baseline exists). Nothing records *what rule* each gate will
  follow once numbers exist, or *why* a category matters commercially — so setting them later would be an
  act of invention rather than derivation.
- "Base-model independence" is asserted (the base can change 8B → 14B → another open-weight model without
  redefining Protea) but nothing stated the contract that survives the swap.

## Decision

1. **`docs/capability-spec.yaml` is the behavioural contract.** One entry per capability: a plain-language
   `statement` of what Protea must do, a `why` giving the commercial rationale, a `tier`, the `evidence`
   (the ZaraBench category and its checks), the `release_rule` in words, and the current `gate_status`. It is
   stated independently of the base model.

2. **Three artifacts, one source of truth each, no forking.**
   - `configs/evaluation/zarabench-0.1.yaml` is the **instrument** — it owns weights, the ZaraScore formula,
     judge discipline, and the numeric gates.
   - `configs/release/zara-v0.yaml` is the **pipeline** — it owns the release rules that read those gates.
   - `docs/capability-spec.yaml` is the **contract** — it owns meaning, rationale, tier and rule-in-words.
     It never restates a weight or a gate number.

3. **Four tiers, by how hard a regression bites a release:** `frontier_gate` (must reach the frontier model,
   not just the base), `priority` (≥ frozen base and ≥ production), `guardrail` (an **absolute** floor, never
   "≥ base" — a base that was already unsafe is not a licence to ship unsafe), `supporting` (do-no-harm vs
   production). The tiers are not free-floating: `test_capability_spec.py` asserts the `frontier_gate` set
   equals the eval config's `frontier_gate_categories` and that `frontier_gate ∪ priority` equals its
   `priority_categories`.

4. **A consistency test keeps a plain doc honest, instead of a premature config kind.** The spec is *not*
   registered as a schema-validated `ConfigKind` (ADR-005) today: nothing in the code reads it yet, and a
   pydantic schema would be an abstraction ahead of need (CLAUDE.md). `tests/test_capability_spec.py` binds
   it to the eval config instead — every capability must be a real ZaraBench category (a bijection), evidence
   must name that category, and guardrail rules must state an absolute floor. That is enough to stop drift.

## Consequences

- **Gate-setting becomes derivation, not invention.** When a clean frozen baseline exists (vanilla Qwen3-8B
  and a true v0.1 adapter), each numeric gate is read off this contract: frontier_gate → the frontier score;
  priority → the frozen base score tightened toward production; guardrail → an argued absolute floor;
  supporting → the production score. Until then the gates stay `0.0` and release is blocked by
  "report not partial" + the security suite, never by training loss (ADR-013 reaffirmed).
- **The spec flags what should not wait.** Safety and hallucination are absolute floors; the spec records
  that these two are the gates that should be argued *now* rather than deferred to a base comparison — a note
  for the gate-setting step, deliberately not a number set here.
- **Graduation path.** If and when gate floors are wired into `protea evaluate compare` so the tool reads them
  from the contract directly, the spec graduates into a validated config kind under `configs/` (a new
  `ConfigKind` + pydantic model + loader entry), and the consistency test tightens into schema validation.
  That step is deferred until there is a consumer for the numbers.
- The spec is versioned (`0.1.0`) alongside ZaraBench `0.1.x`; a new capability or a tier change is a reviewed
  edit that the test gates, the same way `evaluate seal` gates a task-set change.
