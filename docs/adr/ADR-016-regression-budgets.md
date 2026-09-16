# ADR-016 — Regression budgets, anchored to the B0 baseline

**Status:** accepted · **Date:** 2026-09-12 · **Scope:** capability spec (ADR-014), evaluation (ADR-007), release (ADR-011)

## Context

ADR-014 defined the capability contract and said the numeric release gates would be *derived* from a frozen
baseline once one existed, rather than invented. That baseline now exists:

**B0 — the unmodified Qwen3-8B base** (`Qwen/Qwen3-8B` @ commit `b968826…`), scored on the full sealed
ZaraBench 0.1.1 suite (206/206 tasks), judge-free, is committed at
`evaluation/reports/zarabench-0.1.1/local-Qwen-Qwen3-8B-b968826-20260912T062059Z.md`.

Headline **ZaraScore 80.7%** (strict 47.7%; `partial`, judge checks skipped). Per category:

| Category | Tier | B0 | | Category | Tier | B0 |
|---|---|---|---|---|---|---|
| structured_output | frontier_gate | 96.9% | | business_reasoning | supporting | 85.6% |
| workflow_generation | supporting | 89.2% | | instruction_following | supporting | 77.5% |
| connector_selection | priority | 84.5% | | agent_generation | priority | 73.7% |
| tool_calling | priority | 84.4% | | failure_recovery | supporting | 72.2% |
| | | | | safety | guardrail | 72.1% |
| | | | | **hallucination** | guardrail | **34.4%** |

The base is already strong at structured output and tool calling, and weak at agent-spec field accuracy
(`field:category`/`field:tier` are the top failures under the 0.20-weight `agent_generation`) and at
hallucination. Those are the levers for P0.

## Decision

1. **A candidate may not fall below the frozen baseline by more than its tier's budget, per category.** The
   budgets are the base-model-independent policy; they live in `docs/capability-spec.yaml` under
   `regression_budgets`:

   | Tier | Budget | Rationale |
   |---|---|---|
   | frontier_gate | **0.00** | losing the head-to-head category is never acceptable |
   | priority | 0.02 | hold the load-bearing behaviours within 2 points of base |
   | guardrail | **0.00** | never ship a safety / truthfulness regression |
   | supporting | 0.05 | tolerate small movement |

   The per-category **release floor is computed at compare time** as `baseline_category_score − tier_budget`.
   This keeps a single source of truth: the *policy* is the contract, the *numbers* are the committed baseline
   report. Swapping the base → a new baseline report → new floors, same policy.

2. **A candidate that improves a capability is always fine.** The budget bounds *loss*, not gain — this is what
   stops "+12% on tool use, −8% on grounding" from passing on the aggregate. Every category is judged on its own.

3. **Guardrails get zero tolerance, and hallucination is a P0 target, not just a floor.** B0 hallucination is
   34.4% — the budget forbids making it worse, but the point of P0 is to *raise* it. `test_capability_spec.py`
   enforces that the guardrail and frontier-gate budgets are exactly 0.0.

## Consequences

- **The judge-free caveat bounds what these budgets can currently police.** Safety, business-reasoning and
  instruction-following (language) rely on judge checks that a judge-free B0 skips, so their budgets bite only
  on the deterministic subset until a **judged baseline** exists. A judged B0 (an LLM judge that differs from the
  model under test and from any synthetic-data generator — ADR-007/ADR-013 permit Claude/GPT to *judge*, never
  to generate training data) is the follow-up that makes the safety and language floors real.
- **Enforcement landed 2026-09-16** (this bullet previously said it was deferred). The floor computation is
  now wired: `tier_budgets` in `configs/evaluation/zarabench-0.1.yaml` carry the policy (kept equal to
  `capability-spec.yaml::regression_budgets` by a test), `EvaluationConfig.category_floors` derives
  `baseline − budget` at compare time, an explicit `min_score` still acts as an absolute floor, and a
  cross-version comparison is refused rather than scored. Two properties of this ADR are respected by the
  implementation: the per-category floors are computed, never typed, so swapping the base re-derives them; and
  `guardrail` is listed in `absolute_tiers`, so safety and truthfulness are **not** graded against the base —
  they need an explicit absolute `min_score`, which is an open decision (`docs/evaluation-review.md` §8).
- **A budget is only as meaningful as the category it polices.** `protea evaluate audit` measures the suite's
  floor — a content-free stub scores **62.1%** overall and **1.00 on every `agent_generation` task**
  (2026-09-16), because that category's checks are structural. A within-budget result on such a category says
  little; the instrument fix comes before more training spend (F1 in `docs/evaluation-review.md`).
- **Open item — the machine baseline.** Only the human-readable `.md` baseline is committed; the machine
  `base.json` that `evaluate compare` consumes remains canonical in object storage (R2). The GitHub-logs path
  truncates a 206-task report, so the clean way to land `base.json` in the repo is to have the fetch workflow
  upload it as a build artifact (or a small R2→repo step) — a follow-up, needed before the first automated
  compare.
- **Small-n language slices are provisional.** B0 shows encouraging SA-language scores (en-ZA 85.7%, zu 92.9%,
  af 91.7%) but also sw 0% / fr 50% on 1–2 tasks each; language budgets wait on the authored non-English 0.2 set.
