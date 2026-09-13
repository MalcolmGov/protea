# Protea lineage — protea-agent

A running ledger of what each Protea adapter changed relative to its frozen base, measured on the
sealed ZaraBench suite, judged against the ADR-016 regression budgets. Every row is evidence: a
reproducible eval on a pinned task-set, not an impression. Adapters that violate a budget are recorded
here with *why*, so the next iteration is failure-driven rather than speculative.

- **Frozen base:** `Qwen/Qwen3-8B` @ `b968826d9c46dd6066d109eabc6255188de91218`
- **Suite:** ZaraBench 0.1.1, judge-free (`judge_provider: null`), 206 tasks, task-set sha256 `095d5209d265`
- **Budgets (ADR-016):** frontier_gate 0.00 · priority 0.02 · guardrail 0.00 · supporting 0.05
  (max allowed *drop* vs base, per category tier)

## Rungs

| Rung | Adapter | Data | ZaraScore | vs base | Releasable? |
|---|---|---|---|---|---|
| **B0** | *(none — bare pinned base)* | — | **80.7%** | — (reference) | reference |
| **P0** | `protea-agent-0.2` `20260912T140106Z` | `agent-training-0.2.0` (blended, reviewed) | **77.9%** | **−2.8** | **No** — 5 budget breaches (ADR-016) |

Reports: [`B0`](../../evaluation/reports/zarabench-0.1.1/local-Qwen-Qwen3-8B-b968826-20260912T062059Z.md) ·
[`P0`](../../evaluation/reports/zarabench-0.1.1/local-protea-agent-0.2-20260912T232848Z.md)

---

## delta(B0 → P0)

P0 is the deliberately conservative first QLoRA (rank 32 / alpha 64, 3 epochs, lr 1e-4, seed 42) on the
reviewed 0.2 blend. Same base commit, same sealed task-set, same config hash (`e53b9bd474d8`) as B0 — so
the delta is attributable to the adapter alone.

| Category | Tier | Budget | B0 | P0 | Δ | Within budget? |
|---|---|---|---|---|---|---|
| structured_output | frontier_gate | 0.00 | 96.9% | 94.3% | −2.6 | ❌ (see note) |
| agent_generation | priority | 0.02 | 73.7% | 72.0% | −1.7 | ✅ |
| tool_calling | priority | 0.02 | 84.4% | 80.0% | −4.4 | ❌ |
| connector_selection | priority | 0.02 | 84.5% | 65.3% | −19.2 | ❌ |
| workflow_generation | supporting | 0.05 | 89.2% | 96.7% | +7.5 | ✅ |
| business_reasoning | supporting | 0.05 | 85.6% | 90.0% | +4.4 | ✅ |
| failure_recovery | supporting | 0.05 | 72.2% | 16.7% | −55.5 | ❌ |
| safety | guardrail | 0.00 | 72.1% | 83.3% | +11.2 | ✅ |
| hallucination | guardrail | 0.00 | 34.4% | 73.3% | +38.9 | ✅ |
| instruction_following | supporting | 0.05 | 77.5% | 70.8% | −6.7 | ❌ |
| **Overall (ZaraScore)** | — | — | **80.7%** | **77.9%** | **−2.8** | — |

### Verdict: P0 is not releasable

Five budget breaches, including the zero-tolerance frontier gate. The "Protea B0 → P0" milestone exit
criterion — *credible improvement over the base without violating ADR-014 regression thresholds* — is
**not met** by P0.

### What the evidence says — it's the blend composition, not "caution"

The root cause is in the training mix, not the model's temperament. The 0.2 blend (1743 train rows) was:

| task_type | rows | share | source |
|---|---|---|---|
| **tool_calling** | **724** | **42%** | 100% synthetic (one reviewed family) |
| structured_output | 407 | 23% | mined |
| agent_generation | 388 | 22% | mined |
| connector_selection | 142 | 8% | mined |
| routing | 82 | 5% | mined |
| **failure_recovery** | **0** | **0%** | — |

One synthesized family (tool_calling) at **42%** swamped the mined categories, and three epochs on that narrow,
terse distribution (its assistant turns are bare tool-calls) drove **catastrophic forgetting** of abilities the
base had at B0. That is why **connector_selection collapsed −19.2** (only 8% of the mix, swamped) and
**failure_recovery collapsed −55.5** (0% coverage — the model forgot a skill it scored 72% on). The failure
modes corroborate: `tool_called` failures rose to 44 (B0: 20), a new `no_hallucinated_connectors` check fails
12×, `failure_recovery` pass-rate went 66.7% → 0.0%, and P0 emits roughly half the output tokens (103K vs 218K).
Crucially, the heavy tool_calling volume **did not even lift tool_calling** (−4.4, within noise) — so the
experiment's stated hypothesis, *"does tool_calling volume raise the score?"*, is cleanly **falsified**.

The guardrail *gains* are real and traceable: the synthetic tool_calling system prompts carry strong
*"never invent … use tools only when justified"* framing, which transferred — lifting hallucination +38.9 (B0's
worst category, 34% → 73%, more than doubled) and safety +11.2. That framing is worth keeping.

> Note: an earlier revision of this doc described P0 as an over-*conservative* blend that "suppressed
> tool-calling." Tracing the actual composition (42% synthetic tool_calling) falsified that — the blend was
> tool-calling-*heavy*; the regressions come from imbalance and forgetting, not caution. Corrected here.

### Signal vs. noise

- **Load-bearing (large vs n):** hallucination +38.9, failure_recovery −55.5, connector_selection −19.2,
  safety +11.2, workflow_generation +7.5.
- **Within the ~1-task noise band (n = 25–32):** agent_generation −1.7, structured_output −2.6, tool_calling
  −4.4. The frontier-gate breach is one task's worth on n = 32 — it trips the letter of the zero-tolerance
  rule but is not a credible capability loss. The load-bearing regressions to fix are **connector_selection
  and failure_recovery**.

### Next rung (P0.1) — rebalance the blend, one variable

Fix the data, not the model. **Down**-sample the swamping synthetic tool_calling **724 → 250** (~20% of the
mix, peer-sized with the mined categories) so it no longer crowds out the rest and no longer over-trains — while
keeping enough of it to retain the anti-hallucination framing P0 earned. Every hyperparameter stays identical to
P0 (epochs 3 included), so P0.1's delta is attributable to the rebalanced blend alone.

- Config: `configs/training/protea-agent-8b-qlora-0.2.1.yaml`
- Produce the split: blend workflow with `cap: "tool_calling=250"`, or `dataset blend … --cap tool_calling=250`.
- **Hypothesis:** with the over-fit removed, the base's latent connector_selection and failure_recovery survive,
  while the safety/hallucination gains persist.
- **Known gap:** failure_recovery still has *zero* training coverage; the rebalance is expected to recover much
  of the −55.5 by not destroying the base skill, but explicit coverage would need a synthesis run (a later rung).
