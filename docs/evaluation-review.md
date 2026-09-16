# Protea — capability review: is the model good enough to sell?

**Date:** 2026-09-16 · **Assessed at:** `main` @ `c3815b5` · **Status:** assessment + tiered actions

Status lives in [`project-status.md`](project-status.md), binding decisions in [`adr/`](adr/), and the model ledger
in [`lineage/protea-agent.md`](lineage/protea-agent.md). This document is the **assessment**: what the committed
evidence says about the model a partner would run on, what it costs to serve, and what has to change first.
Nothing here overrides an ADR.

## 1. Verdict

The **platform is built and it is good**; the **model is not yet differentiated**. v0 is a stock `Qwen/Qwen3-8B`
plus a system prompt, and it is not deployed. Four training rungs all lost to the frozen base, and the benchmark
that decided that cannot tell a frontier model from the base — so both the "we lost" and the "we are at parity"
readings are weaker than they look. Selling this as "a capable model that saves clients frontier spend" needs
three things the programme does not yet have: an instrument that discriminates, a workload where the base is
actually weak, and a serving deployment whose cost per turn is measured rather than assumed.

The commercial case exists but is **volume-gated and currently negative at forecast** — see §4.

## 2. What the evidence does and does not support

The operating contract says the evaluation is the source of truth (ADR-007, ADR-016). Three findings limit how
much truth the current instrument can carry.

**F1 — The benchmark does not separate a frontier model from the base.** Sonnet 5 scores **80.9%**; `Qwen3-8B`
scores **80.7%**, and the base *wins* the configured `frontier_gate` category `structured_output` (96.9% vs 89.9%).
The two runs are also on different suite versions (0.1.0 vs 0.1.1) which the changelog itself declares
incomparable. Consequences: the frontier gate is trivially satisfied by the base; the A1 kill criterion
(`kill_fraction_of_frontier: 0.8`) computes to a 64.7% floor that nothing could fail; and a 2–3 point ZaraScore
delta between rungs is not demonstrably meaningful.

**F2 — The gate is unarmed.** Every `min_score` and `release_min_zarascore` is `0.0`, so "all gates passed" is
vacuous. The machine-readable B0 baseline behind the budgets is not committed (only
`local-Qwen-Qwen3-8B-b968826-…​.md`), so `evaluate compare` cannot enforce floors. `compare` also rejects any
candidate report that is `partial`, and every committed report is partial because the config ships
`judge_provider: null` while **43 of 206 tasks** want a judge.

**F3 — Scoring is generous and partly proxy.** `score = checks passed / checks` hides hard failures: the base
scores 73.7% on `agent_generation` at a **4.0% pass rate**, and 84.5% on `connector_selection` at 31.6%. Skipped
judge checks mean `business_reasoning` (all 15 tasks), `safety` (10) and `instruction_following` (10) are scored
by substring proxies — hedge phrases and `says_any`/`says_none` — not by the traits they name.

## 3. Findings on the model programme

**F4 — Training targeted the categories that were already at ceiling.** The 0.2 blend was
`tool_calling` 724 (42%), `structured_output` 407, `agent_generation` 388, `connector_selection` 142, `routing` 82,
`failure_recovery` **0** — while the base already scored 96.9% on `structured_output`. The categories where the
base is genuinely weak (hallucination 34.4%, `sw` 0%, `fr` 50%, failure_recovery 72.2%) received little or no
data. Training on near-ceiling categories buys degradation, and that is what happened
(`connector_selection` −19.2 at P0; `failure_recovery` −55.5).

**F5 — Known method levers were identified and never pulled.** `docs/experiments/agent-generation-rootcause.md`
and the strategy review name them; the code still lacks them:
- **`assistant_only_loss` is off** (`protea/config/models.py:63`), the most likely cause of the forgetting.
- **DPO is a phantom.** `method: Literal["sft","lora","qlora","dpo"]` accepts `dpo` (`models.py:46`), but
  `trainer.py::peft_config` returns `None` for it and `train()` always constructs an `SFTTrainer` — so a `dpo`
  config silently runs **full-parameter SFT**, over budget and with no adapter. Strategy-review A2 (DPO from the
  validation gate's automatic pass/fail pairs) is unimplemented.
- **Constrained decoding was never measured** (strategy-review A4), despite `xgrammar` being configured for
  serving (`protea/serving/vllm.py:26`) and `json_parsable` being the top `agent_generation` failure mode.
- **Train/serve/eval format parity is unverified** (A3) — the trainer falls back to a naive ChatML template when
  the base's tokenizer has none.

**F6 — The B0 anchor is not the shipping configuration.** B0 was measured thinking-**on**, no prompt; v0 ships
thinking-**off**, with the guardrail prompt. Exp 0 shows the prompt alone moves safety 72.1 → 82.1. Every budget
in ADR-016 is anchored to a configuration the product will not run.

**F7 — The registry contradicts the lineage.** `registry/models.json` holds one entry (a Qwen2.5-0.5B CPU
rehearsal); none of the four real rungs are registered, and `registry/datasets.json` stops at 0.2.0 (`draft`),
missing the 0.2.1/0.2.2 sets that were trained. ADR-004 calls the registry the source of truth for releases, so
the router's capability matrix and the release pipeline have nothing real to gate on.

**F8 — Nothing is deployed and no traffic has been observed.** PR #54 is unmerged, the serve path has never run
against a GPU engine in this repo's history, and there is no production traffic, no observability and no consent
plumbing in `aria`. The data flywheel that would supply the missing training data has not started, and there is
no measured cost per turn.

## 4. The commercial case (what the existing model says)

`protea economics report` on `configs/economics/zara-v0.yaml`, at forecast (310,250 requests/month):

| Metric | Value |
|---|---|
| Frontier-only cost | 1,371 USD / month |
| Protea total (GPU + training + engineering + ops) | 2,431 USD / month |
| Savings at forecast | **−1,060 USD / month (−77%)** |
| Break-even volume | **554,332 requests / month (2.1× forecast)** |
| GPU utilisation at forecast | **2%** |

Three things follow, and they are the commercial strategy rather than a footnote:

1. **The fixed cost dominates, not the GPU.** 2,028 USD of the fixed cost is GPU 628 + training 50 + engineering
   1,200 + ops 150. Engineering hours, not silicon, are the largest line — so the payback is a function of how
   few engineering hours a running deployment needs.
2. **Marginal cost per request is already ~15× better than frontier.** At the configured 8.6M requests/month
   capacity (60% utilisation) the same fixed cost covers ~30× today's volume; frontier pricing is
   $0.0044/request. The economics of *selling* Protea are therefore about **aggregating volume across tenants
   onto one engine**, which is the opposite of the one-adapter-per-partner instinct.
3. **Break-even needs ~2× the current forecast.** The lever is not a cheaper model, it is more routable traffic —
   or a smaller serving footprint while volume is low (a 4-bit 8B on a cheaper card, or an existing host before
   a dedicated one). Both belong in the next revision of the forecast, which should be re-cut from real partner
   volume rather than asserted.

The commercial claim to a prospective client — "the same turn at a fraction of frontier cost, running inside your
boundary" — is credible **only once per-turn cost is measured on the real engine** (`serve loadtest`, §6).

## 5. The plan

Tiers are ordered by information gained per dollar. **Tier 0 costs nothing and gates everything else**; Tier 1
is the deployment work that makes v0 real; Tier 2 is what makes training capable of winning; Tier 3 is strategy.

### Tier 0 — fix the instrument (no GPU, no spend)

1. Commit the **machine** B0 report as `base.json`; re-run the frontier baseline on **0.1.1** so the frontier
   gate and the kill criterion compare like-for-like.
2. Arm the gate: real per-category `min_score` values and a real `release_min_zarascore`; gate on **strict pass
   rate** with partial-credit score reported alongside it.
3. Decide the judge policy explicitly: run a judge (with a judged B0) for the 43 judge-dependent tasks, or drop
   those checks from the score rather than silently skipping them.
4. Fix the two known instrument defects: 6 sealed golden references exceed the eval's 4,000-token budget and are
   unsatisfiable for any model; a single run per rung gives no variance estimate (run 2–3 seeds, report spread).
5. Re-baseline B0 under the **shipping** configuration (thinking off + guardrail prompt) — the number every
   future decision should be measured against.

### Tier 1 — make v0 real

6. **Land PR #54** (`publish-serve-images`) and deploy base+prompt behind the vLLM path + facade, thinking-off,
   with the guardrail prompt wired in at the deployment (see §6).
7. **Validate thinking-off on the reasoning-heavy categories** (`agent_generation`, `structured_output`) with one
   full-suite product-config run, and re-record the product-config baseline. *(Spends ~$2 of GPU time.)*
8. **Run `serve loadtest` against the real engine** and replace the assumed 900 output tok/s with a measured
   number; feed it back into `configs/economics/zara-v0.yaml`.
9. **Turn on one routed task type** on the router (capability-gated, canary, fallback to frontier) and confirm
   route/fallback events are recorded; keep the blast radius to one task type per tenant.
10. **Observability + consent plumbing in `aria`** (strategy-review A7): per-tenant `training_consent` and a
    review queue, so fallback events and rated turns can legally become data. Without this the flywheel is
    hypothetical.

### Tier 2 — make training capable of winning

11. **Measure constrained decoding before any further fine-tuning** (A4): score the base with xgrammar-guided JSON
    on `structured_output`/`agent_generation`. If parse and schema failures collapse, the training target becomes
    *behaviour*, not formatting — which reframes the programme and its data budget.
12. **Enable `assistant_only_loss`** with a proper Qwen3 `{% generation %}` chat template plus a smoke test
    (F5), and verify train/serve tool-call format parity against the `hermes` parser.
13. **Implement DPO/ORPO from the validation gate** — the gate already yields automatic preference pairs at zero
    human cost (A2) — or remove `dpo` from the config Literal so it cannot silently run full SFT.
14. **Build data for the gaps, not the ceilings**: hallucination-specific rows, `failure_recovery` (0 rows today,
    15 sealed tasks, −49 observed), and the multilingual `instruction_following` hole (`sw` 0%, `fr` 50%).
15. **Reconcile the registry with the lineage** (F7) so releases, model cards and the capability matrix refer to
    artifacts that exist.
16. **Scale or shrink the base.** 1,211 rows (~1.9M tokens) from one catalogue will not move an 8B: either
    synthesise 10–50k verified rows with the open-weight teacher (the `balance`/`target_tools` selectors exist) or
    move to a 3–4B base where the data ratio and the serving economics both improve. For the *sell-to-clients*
    goal, cost per turn favours the smaller base; the compiler task (low volume, high stakes) can take the 14B.

### Tier 3 — strategy

17. **Make the C4 reframing explicit.** `docs/strategy-review.md` recommended the **runtime turn model** as the
    first economic target (high volume, narrow task, natural data from the estate's tool evals) and the compiler
    as the strategic one. The programme trained the compiler. Given the commercial goal in §4 — volume
    aggregation — the runtime turn is where the money is; build `runtime-training-0.1` or record why not.
18. **Protect comparability.** 0.1.1 already loosened three expectation families after the frontier baseline;
    each such change re-baselines everything. Keep a frozen comparability set that never moves mid-programme.
19. **Decide the tenancy model for partners.** If the economic unit is an aggregated engine, decide what
    "deployed inside your boundary" means for a shared engine (per-tenant adapters, per-tenant keys, per-tenant
    rate limits) before a partner asks.

## 6. Landed in this pass (Tier 1, first slice)

The v0 product decision (ADR-017) was **specified but not implemented**: the guardrail prompt reached the *eval*
(`protea/evaluation/runner.py`) but not *serving*, so the base would have shipped without its product framing.

| Change | Where | Evidence |
|---|---|---|
| Product prompt overlay, merged above any caller system prompt (never replacing it), idempotent, streaming-preserving | `protea/serving/prompt.py`, wired in `protea/serving/app.py::create_app` and `protea/cli_serve.py::_build_router` | `tests/test_serving.py`; end-to-end probe against `configs/serve/facade.yaml` shows the guardrail merged above "You are Acme's booking agent." |
| Facade carries the v0 prompt from config; `serve facade --check` prints prompt size and rate limit before deploy | `configs/serve/facade.yaml`, `protea/serving/config.py`, `protea/cli_serve.py` | `serve facade --check` → `prompt 1405 chars from configs/evaluation/guardrail-system-prompt.md` |
| Per-tenant request limiting (token bucket, `429` + `retry-after`, metric, ops endpoints exempt) | `protea/serving/ratelimit.py`, `protea/serving/app.py::_observe` | `tests/test_serving.py` |
| Canary-echo hardening — never repeat injected codes or hidden instructions, even when refusing | `configs/evaluation/guardrail-system-prompt.md` | Closes the Exp 0 `says_none` failure (ADR-017 consequences) |

Checks: `pytest` 291 passed / 2 skipped, `ruff check .` clean, `protea config validate configs` ok,
`protea evaluate verify` ok, `protea security verify` ok, `protea evaluate run --provider reference` 1.000
everywhere.

**Consequence to respect:** editing the prompt changes the product-config hash, so the Exp 0 numbers and any
previous product-config run are no longer like-for-like. The thinking-off full-suite run (§5.7) is what
re-establishes that baseline — do it before quoting guardrail numbers again.

## 7. What needs a decision before more spend

1. **Approve the Tier 0 re-baselines** (free) — without them, every later comparison inherits F1–F3.
2. **Approve PR #54 + the thinking-off validation run** (~$2 GPU) — the first evidence about the configuration
   that would actually serve.
3. **Approve the target reframing** (C4: runtime turn model first) or explicitly keep compiler-first, given the
   volume aggregation that §4 says the economics need.
4. **Approve the base-model decision** for the sell-to-clients path (smaller base for cost per turn vs 8B for
   capability), which sets the Tier 2 data budget.
