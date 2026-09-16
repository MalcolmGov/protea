# Protea — capability review: is the model good enough to sell?

**Date:** 2026-09-16 · **Assessed at:** `main` @ `c3815b5` + the Tier 0 changes below · **Status:** assessment + tiered actions

Status lives in [`project-status.md`](project-status.md), binding decisions in [`adr/`](adr/), and the model ledger
in [`lineage/protea-agent.md`](lineage/protea-agent.md). This document is the **assessment**: what the committed
evidence says about the model a partner would run on, what it costs to serve, and what has to change first.
Nothing here overrides an ADR.

## 1. Verdict

The **platform is built and it is good**; the **model is not yet differentiated**, and the **instrument could not
have told us if it were**. v0 is a stock `Qwen/Qwen3-8B` plus a system prompt, undeployed. Four training rungs
lost to the frozen base, and the benchmark that decided that:

- cannot separate a frontier model from the base (F2), and
- **awarded 62.1% to a content-free stub — 100% of `agent_generation`, the highest-weighted category (F1).**
  ZaraBench 0.2, added in this pass, cuts that to **44.5%** (**1.0% strict**) with no category fully satisfied
  by a stub.

So the honest reading of "both adapters lose to base" is not yet "training did not work" — it is that the score
being optimised was, in large part, shape rather than capability. Selling this as "a capable model that saves
clients frontier spend" needs three things the programme does not have: checks that require content, a workload
where the base is genuinely weak, and a deployment whose cost per turn is measured rather than assumed.

The commercial case exists but is **volume-gated and negative at forecast** (§4).

## 2. What the evidence does and does not support

The operating contract says the evaluation is the source of truth (ADR-007, ADR-016). Four findings limit how much
truth the current instrument can carry. **F1 was measured during this review and is the most severe.**

**F1 — A content-free stub scores 62.1% (NEW).** `protea evaluate audit` (added with this review) scores a stub —
schema-shaped JSON with `"x"` for every string, the one declared tool call, no hedging, no facts — through the
real evaluators. Result on the sealed 0.1.1 set:

| Category | Weight | Stub mean | Stub full marks |
|---|---|---|---|
| agent_generation | 0.20 | **1.00** | **25/25** |
| structured_output | 0.15 | 0.89 | 25/32 |
| safety | 0.05 | 0.82 | 12/20 |
| failure_recovery | 0.05 | 0.68 | 0/15 |
| instruction_following | 0.05 | 0.57 | 7/20 |
| business_reasoning | 0.10 | 0.51 | 0/15 |
| workflow_generation | 0.10 | 0.50 | 0/15 |
| connector_selection | 0.10 | 0.35 | 0/19 |
| tool_calling | 0.15 | 0.28 | 2/30 |
| hallucination | 0.05 | 0.10 | 0/15 |

The stub's ZaraScore is **62.1% (strict 37.5%)** against the base's 80.7%. A 70-token object scores **1.00** on
every `agent_generation` task, because the checks are structural: bare JSON, the schema, four field values, no
invented tools — nothing inspects `guardrails`, `system_prompt` or whether the tool list is sane. The category the
whole training programme was optimised against (weight 0.20, three rungs of blends and trims aimed at it) cannot
distinguish a stub from a production-ready spec. `failure_recovery` and `safety` leak the same way.

Consequences to carry forward: a ZaraScore must be quoted **with its floor**; small rung-to-rung deltas (< ~2
points) are inside the free band; and any further training spend should wait until the hollow categories carry
content checks.

> **Update (2026-09-16) — closed by ZaraBench 0.2.** Two deterministic passes write a new suite version
> (`affirm` then `harden`), and every reference is proved to still pass its own harder checks. The stub floor
> falls **62.1% → 44.5%**, the **strict** floor **37.5% → 1.0%**, `agent_generation` goes from 25/25 full marks
> to 0/25, no category is fully satisfied by a stub, and two `tool_calling` tasks are the entire remainder. The
> free band that is left is partial credit, not free passes — which is why 0.2's gate judges the strict pass rate
> (§6.2). 0.1.1 stays sealed for the P0–P0.2 lineage; the two versions are not comparable.

**F2 — The benchmark does not separate a frontier model from the base.** Sonnet 5 scored **80.9%**; `Qwen3-8B`
scored **80.7%**, and the base *wins* the configured `frontier_gate` category `structured_output` (96.9% vs
89.9%). The two runs are on different suite versions (0.1.0 vs 0.1.1) which the changelog declares incomparable —
`compare` now refuses such a comparison outright rather than scoring it. Consequences: the frontier gate cannot be
evaluated until a same-version frontier baseline exists; the A1 kill criterion (`kill_fraction_of_frontier: 0.8`)
computes to a 64.7% floor that nothing could fail (and that a stub nearly clears).

**F3 — The gate was unarmed; it is now armed (FIXED in this pass).** Every `min_score` was `0.0`, so "all gates
passed" was vacuous. ADR-016's budgets are now config (`tier_budgets`, kept equal to the capability spec's
`regression_budgets` by a test), per-category floors are derived at compare time as `base − budget`,
cross-version comparisons are refused outright, the strict pass rate is surfaced as a non-blocking advisory, and
a `partial` report no longer releases silently. ADR-014's separate rule is honoured too: `guardrail` is in
`absolute_tiers`, so safety and hallucination are **not** graded against the base — they need an explicit
absolute `min_score`, which is unset and therefore reported as *unfloored* on every comparison. What remains
open: the machine-readable baseline is still **not committed** (only the `.md`), so floors need a baseline JSON
passed in; the committed reports are all `partial` (43/206 judge-dependent tasks with `judge_provider: null`);
and the two absolute guardrail floors are an unmade product decision.

**F4 — Scoring is generous and partly proxy.** `score = checks passed / checks` hides hard failures: the base
scores 73.7% on `agent_generation` at a **4.0% pass rate**; a stub collects **0.5** on a `must_include` task by
saying nothing (the `no_tool` check passes trivially). Skipped judge checks mean `business_reasoning` (all 15
tasks), `safety` (10) and `instruction_following` (10) are scored by substring proxies — hedge phrases and
`says_any`/`says_none` — not by the traits they name.

## 3. Findings on the model programme

**F5 — Training targeted the categories that were already at ceiling.** The 0.2 blend was `tool_calling` 724
(42%), `structured_output` 407, `agent_generation` 388, `connector_selection` 142, `routing` 82,
`failure_recovery` **0** — while the base already scored 96.9% on `structured_output` (and F1 now shows the
`agent_generation` target was largely free). The categories where the base is genuinely weak (hallucination
34.4%, `sw` 0%, `fr` 50%, failure_recovery 72.2%) received little or no data.

**F6 — Known method levers were identified and never pulled.** `docs/experiments/agent-generation-rootcause.md`
and the strategy review name them; the code still lacks them:
- **`assistant_only_loss` is off** (`protea/config/models.py:63`), the most likely cause of the forgetting.
- **DPO is a phantom.** `method: Literal["sft","lora","qlora","dpo"]` accepts `dpo` (`models.py:46`), but
  `trainer.py::peft_config` returns `None` for it and `train()` always constructs an `SFTTrainer` — so a `dpo`
  config silently runs **full-parameter SFT**. Strategy-review A2 (DPO from the validation gate's automatic
  pass/fail pairs) is unimplemented.
- **Constrained decoding was never measured** (strategy-review A4), despite `xgrammar` being configured for
  serving (`protea/serving/vllm.py:26`) and `json_parsable` being the top `agent_generation` failure mode.
- **Train/serve/eval format parity is unverified** (A3) — the trainer falls back to a naive ChatML template.

**F7 — The B0 anchor is not the shipping configuration.** B0 was measured thinking-**on**, no prompt; v0 ships
thinking-**off**, with the guardrail prompt. Exp 0 shows the prompt alone moves safety 72.1 → 82.1. Every budget is
anchored to a configuration the product will not run.

**F8 — The registry contradicts the lineage.** `registry/models.json` holds one entry (a Qwen2.5-0.5B CPU
rehearsal); none of the four real rungs are registered, and `registry/datasets.json` stops at 0.2.0 (`draft`),
missing the 0.2.1/0.2.2 sets that were trained. ADR-004 calls the registry the source of truth for releases.

**F9 — Nothing is deployed and no traffic has been observed.** PR #54 is unmerged, the serve path has never run
against a GPU engine in this repo's history, and there is no production traffic, observability or consent
plumbing in `aria`. With no standing host (see §6), this stays blocked by choice, not by code.

**F10 — Several categories were graded by a phrase match against a template (CLOSED 2026-09-16).**
`authoring.py:192` synthesises a reference from the task's own expectation when no example text exists:
`f"Thanks for reaching out. {says_any[0]}. Let me know if there is anything else I can do."`. Every reference in
`safety`, `business_reasoning` and `instruction_following` was that template, so a reference did not demonstrate
that a realistic answer exists, and the deterministic grading reduced to *does the reply contain the expected
phrase* plus *did it avoid the forbidden thing* — which a useless reply passes vacuously.

The fix (`protea/evaluation/affirm.py`, 55 tasks) authored what a derivation cannot: an affirmative requirement
per task drawn from the behaviour it already tests, plus a witness that carries it. Closing it also exposed two
flaws nobody was looking for:

- **A blanket refusal passed the whole safety slice.** The audit's stub answers refusal tasks with "I'm sorry, I
  can't help with that", and that satisfied `says_any: ["can't", ...]`. The slice rewarded refusing *everything*.
  It now needs both a refusal *and* the safe alternative the customer should use (`must_any`).
- **Short phrases matched inside longer words.** `_phrase_in` was a plain substring test, so `id` matched inside
  "gu*i**d**ance", `30` inside "3000", `10` inside "2010" — 57 tasks carried a phrase a stub could satisfy by
  accident. Single alphanumeric tokens now match on word boundaries.

Result: stub floor **62.1% → 44.5%**, strict **37.5% → 1.0%**, no category fully satisfied by a stub, `safety`
0.82 → 0.58, `business_reasoning` 0.51 → 0.38, `instruction_following` 0.57 → 0.40. What remains is
`tool_calling` (two tasks a stub passes outright, out of this finding's scope).

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

1. **The fixed cost dominates, not the GPU.** Of 2,028 USD fixed, the GPU is 628 and engineering 1,200. Payback is
   therefore a function of how few engineering hours a running deployment needs.
2. **Marginal cost per request is already ~15× better than frontier.** At the configured 8.6M requests/month
   capacity the same fixed cost covers ~30× today's volume; frontier pricing is $0.0044/request. The economics of
   *selling* Protea are about **aggregating volume across tenants onto one engine**, not one adapter per partner.
3. **Break-even needs ~2× the current forecast.** The lever is more routable traffic — or a smaller serving
   footprint while volume is low. A smaller base model (§6) improves both the cost per turn and the data ratio.

The claim to a prospective client — "the same turn at a fraction of frontier cost, inside your boundary" — is
credible **only once per-turn cost is measured on the real engine** (`serve loadtest`).

## 5. The plan

Tiers are ordered by information gained per dollar. **Tier 0 costs nothing and gates everything else.**

### Tier 0 — fix the instrument (no GPU, no spend)

1. **Measure and publish the floor** — `protea evaluate audit`, wired next to `evaluate verify`. ✅ **done** (F1).
2. **Arm the gate** — ADR-016 budgets as config, derived floors, strict-pass-rate advisory, explicit partial
   policy. ✅ **done** (F3).
3. **Correct the record** — the "six unsatisfiable tasks" claim in the root-cause doc is wrong and now carries a
   dated correction; the review and `zarabench.md` state the real defect. ✅ **done**.
4. **Add content checks to the hollow categories** — ✅ **done as ZaraBench 0.2** (`evaluate harden`): stub
   floor 62.1% → 47.9%, strict 37.5% → 5.8%, sealed and satisfiable, enforced in CI with `--max-zarascore 0.55`.
   The residual band is F10 (judge-skipped and template-graded categories), which needs authored expectations or
   a judge. Also decided by this work: **quote the strict pass rate** for 0.2 comparisons.
5. **Commit a machine-readable baseline** for whichever base model is chosen (§6) so floors have an input, and
   decide the judge policy for the 43 judge-dependent tasks (run a judge with a judged baseline, or drop those
   checks from the score rather than silently skipping them).
6. **Re-baseline under the shipping configuration** (thinking off + guardrail prompt, and whatever base wins §6).
   Needs a rented GPU (~$2), not hardware of your own.

### Tier 1 — make v0 real (deferred: no standing host)

7. **Land PR #54** and deploy base+prompt behind the vLLM path + facade, thinking-off, with the guardrail prompt
   wired in at the deployment. ✅ the wiring itself is done (§7); the deployment waits on a host.
8. **Validate thinking-off on the reasoning-heavy categories** with one full-suite product-config run *(~$2 of
   rented GPU time — does not need your own hardware)*.
9. **Run `serve loadtest`** on that run and replace the assumed 900 tok/s with a measured number in
   `configs/economics/zara-v0.yaml`.
10. **Observability + consent plumbing in `aria`** (strategy-review A7) so fallback events and rated turns can
    legally become data. Independent of hosting.

### Tier 2 — make training capable of winning

11. **Measure constrained decoding before any further fine-tuning** (A4) on the categories that survive Tier 0.4.
12. **Enable `assistant_only_loss`** with a proper Qwen3 `{% generation %}` chat template plus a smoke test.
13. **Implement DPO/ORPO from the validation gate** — automatic preference pairs at zero human cost (A2) — or
    remove `dpo` from the config Literal so it cannot silently run full SFT.
14. **Build data for the gaps, not the ceilings**: hallucination-specific rows, `failure_recovery` (0 rows today),
    multilingual `instruction_following`.
15. **Reconcile the registry with the lineage** (F8).
16. **Pick the base deliberately** (see §6) and scale the data to it — 1,211 rows will not move an 8B.

### Tier 3 — strategy

17. **Make the C4 reframing explicit** — the runtime turn model (high volume, narrow, natural data from the
    estate's tool evals) versus the compiler task (low volume, high stakes, and the one F1 shows the suite cannot
    measure).
18. **Protect comparability.** Every suite change re-baselines everything; keep a frozen comparability set.
19. **Decide the tenancy model** for partners if the economic unit is an aggregated engine.

## 6. Decisions (2026-09-16)

Six calls, made in this pass. Each is recorded here, implemented in the config/code it names, and reversible if
the evidence changes.

### 6.1 Base model — a two-tier ladder

**Decision:** `Qwen/Qwen3-4B` is the **v0 candidate for the sell-to-clients path**; `Qwen/Qwen3-1.7B` is the
**local development model** (no GPU); `Qwen/Qwen3-8B` stays for work where capability outranks volume (the
compiler task), with `Qwen/Qwen3-14B` as the scale-up.

Why the 4B for v0: cost per turn is the commercial constraint and §4 shows the economics are volume-gated, so a
10 GB-class card roughly halves the serving footprint and moves break-even volume down with it; the data budget is
~1.2k rows, which is a better parameter-to-data ratio on a 4B than on an 8B; and it is the same family, chat
template and `hermes` tool parser, so every config, prompt and eval transfers unchanged. Why the 1.7B: it makes
the inner loop local (a subset run in minutes on a laptop), which is what the instrument work in Tier 0 needs.

Configs: `configs/models/qwen3-4b.yaml`, `configs/models/qwen3-1.7b.yaml`, `configs/inference/vllm-qwen3-4b.yaml`.
The 4B is *not* a downgrade of the 8B everywhere — the compiler task is low-volume and high-stakes, and that is
what the 8B is for. 8B baselines (B0 80.7% and the P0–P0.2 lineage) remain the record for the 8B line only.

### 6.2 Grading metric — strict on 0.2

**Decision:** the gate on ZaraBench 0.2 judges the **strict pass rate**, not partial credit
(`gate_metric: strict` in `configs/evaluation/zarabench-0.2.yaml`). Partial credit stays shape-dominated even
after the content floors — a content-free stub takes 47.9% of the score but 5.8% strict — so the strict number is
the only one that cannot be earned by a valid shape. Both are still reported; floors and the kill criterion
follow the chosen metric. 0.1.1 keeps `score` so its committed reports keep their meaning.

### 6.3 Judge policy — judge-free gate, judged language

**Decision:** keep the gate **judge-free** (ADR-007 makes deterministic checks the source of truth) and stop
treating a `partial` report as an automatic block: `require_complete_report_for_release: false` on 0.2, with the
skipped-check count and the share of weight it covers reported on every decision. A judge is run only for the 10
language checks, and only when a release is actually being considered.

### 6.4 Guardrail floors — set from the baseline, not invented

**Decision:** safety and hallucination get their absolute floors (ADR-014) **from the first 0.2 baseline of the
chosen base model** — “at least what the shipping configuration already achieves” — and are then ratcheted. No
number is invented ahead of the evidence; until then the comparison prints `not set — this tier is unfloored`.

### 6.5 F10 — author the affirmative expectations, do not derive them (DONE)

**Decision:** close F10 by **authoring** what those categories must see, because the missing signal is
*affirmative* — “the reply actually refused”, “the reply carried the grounded fact”, “the reply is in the right
language” — and only the last of the three needs a judge. **Delivered** as `protea/evaluation/affirm.py` (55
tasks, run inside `evaluate harden` before the derivation): a `must_any` / `refuses_any` / `lang_markers`
vocabulary applied from each task's own flags, plus a witness that carries it, with every reference re-checked
against its own harder expectation. Outcome and the two flaws it exposed: F10 above. The residual is
`tool_calling`, deliberately out of scope.

### 6.6 Hardware

**No serving hardware is available now**, so Tier 1's deployment steps stay deferred. The free work (Tier 0) and
the rented-GPU runs do not need it: GitHub Actions launches a pod that self-stops.

Deployment therefore waits on a host (Tier 1), while the model work does not: everything in Tier 0 and every
rented-GPU run is reachable from a laptop, and the 4B/1.7B ladder makes the inner loop local. When the base
changes, the floors follow automatically — they are derived from whichever baseline is passed in — but the 8B
line's numbers (B0 80.7%, the P0–P0.2 rungs) stay the record for the 8B line only and must not be mixed with a
4B result.

## 7. Landed in this pass

*(Review, Tier 0 and the first Tier 1 slice — all committed, all verified locally.)*

| Change | Where | Evidence |
|---|---|---|
| **`evaluate audit`** — the stub floor, deterministic, no model/GPU/judge, with `--json` and `--max-zarascore` for CI | `protea/evaluation/audit.py`, `protea/cli_evaluate.py` | stub ZaraScore **62.1%** on 0.1.1; `tests/test_audit.py`, `tests/test_cli_phase3.py` |
| **ZaraBench 0.2 — authored affirmations + derived floors** (`must_any` / `refuses_any` / `lang_markers`, `field_len:*` / `field_items:*` / `min_words`, and word-boundary phrase matching), produced by `evaluate harden` = `affirm` → `harden` | `protea/evaluation/affirm.py`, `protea/evaluation/harden.py`, `protea/evaluation/evaluators.py`, `protea/evaluation/tasks.py`, `evaluation/zarabench/0.2/`, `configs/evaluation/zarabench-0.2.yaml` | stub floor 62.1% → **44.5%** (strict 37.5% → **1.0%**); `agent_generation` full marks 25/25 → 0/25; `safety` 0.82 → 0.58; every reference still passes; `tests/test_affirm.py`, `tests/test_harden.py`, `tests/test_evaluators.py` |
| **CI guards both suites** — 0.2 sealed + satisfiable, and the floor pinned with `--max-zarascore 0.55` | `.github/workflows/ci.yml` | local run of every step |
| **ADR-016 as executable policy** — `tier_budgets` in config, floors derived per baseline, cross-version comparisons refused, partial policy explicit, strict-pass-rate advisory | `protea/config/models.py`, `protea/evaluation/report.py`, `configs/evaluation/zarabench-0.1.yaml` | `tests/test_runner_report.py`; `evaluate compare` prints every floor with its arithmetic |
| **Product prompt wiring** (ADR-017 was specified but not implemented in serving) | `protea/serving/prompt.py`, `protea/serving/app.py`, `protea/cli_serve.py`, `configs/serve/facade.yaml` | `serve facade --check` → `prompt 1405 chars from configs/evaluation/guardrail-system-prompt.md`; merged above the caller's own system prompt |
| **Per-tenant rate limiting** (429 + `retry-after`, metric, ops endpoints exempt) | `protea/serving/ratelimit.py` | `tests/test_serving.py` |
| **Canary-echo hardening** of the guardrail prompt | `configs/evaluation/guardrail-system-prompt.md` | closes the Exp 0 `says_none` failure |
| **Decisions landed as config/code** — `gate_metric: strict`, `require_complete_report_for_release: false` and the guardrail-floor policy on 0.2; the base ladder (`qwen3-4b`, `qwen3-1.7b`, `vllm-qwen3-4b`) | `protea/config/models.py`, `protea/evaluation/report.py`, `configs/evaluation/zarabench-0.2.yaml`, `configs/models/`, `configs/inference/` | `tests/test_runner_report.py` (strict gating and strict floor derivation); `config validate configs` 33 ok |
| **Corrections**: the "unsatisfiable tasks" claim, the gate description, and the kill-criterion defect | `docs/experiments/agent-generation-rootcause.md`, `docs/zarabench.md` | dated correction + the audit |

Checks: `pytest` 313 passed / 2 skipped, `ruff check .` clean, `protea config validate configs` 33 ok,
`protea evaluate verify` ok on both suites, `protea security verify` ok, reference runs 1.000 on both suites,
`protea evaluate audit` within its CI ceiling, `protea serve facade --check` ready.

**Ordering consequence:** editing the guardrail prompt changes the product-config hash, and the gate now derives
floors from whichever baseline it is given. So the sequence is: pick the base (§6) → Tier 0.4 content checks →
Tier 0.6 re-baseline → then, and only then, quote a guardrail or adapter number.

## 8. What remains

The six judgements are made (§6). What is left is execution, in this order:

1. **The 0.2 baseline** on the chosen base — one rented-GPU run (~$2, quoted before dispatch), covering the
   shipping configuration (thinking off + the guardrail prompt) so the number describes what ships. Needs a 0.2
   report: 0.1.1 is not comparable with the hardened suite.
2. **Then set the guardrail floors** from that baseline (§6.4), and ratchet them.
3. **Tier 1 deployment** waits on a host: PR #54, the facade in front of the engine, one routed task type, and
   `serve loadtest` to replace the assumed 900 tok/s with a measured number.
4. **Tier 2** (training) resumes only on a suite that can measure it: constrained decoding measured first, then
   `assistant_only_loss`, then DPO from the validation gate, then data for the gaps (hallucination,
   `failure_recovery`, non-English) rather than another blend of the categories that are already at ceiling.
5. **The C4 reframing** (Tier 3.17) — decide runtime-turn-first explicitly, now that the base model and the
   economics both point that way.
6. **One known residual**: two `tool_calling` tasks pass a stub outright, and `tool_calling`'s arithmetic
   arguments are graded only where a task declares them. Worth the same treatment as F10 when the runtime turn
   model becomes the target.
