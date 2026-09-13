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

### What the evidence says

P0 traded **action for caution**. The guardrail tier improved sharply — hallucination +38.9 (B0's worst
category, 34% → 73%, more than doubled) and safety +11.2 — but every capability that requires the model to
*act* regressed. The mechanism is visible in the failure modes: P0's `tool_called` failures rose to 44
(B0: 20), a new `no_hallucinated_connectors` check fails 12×, and `failure_recovery` pass-rate collapsed
66.7% → 0.0% (the model stopped emitting the `handoff_to_human` binding). P0 also emits roughly half the
output tokens (103K vs 218K). The conservative 0.2 blend **over-suppressed tool-calling assertiveness**:
it generalized "don't hallucinate / be safe" into "don't call tools," which is fatal for an agent.

### Signal vs. noise

- **Load-bearing (large vs n):** hallucination +38.9, failure_recovery −55.5, connector_selection −19.2,
  safety +11.2, workflow_generation +7.5.
- **Within the ~1-task noise band (n = 25–32):** agent_generation −1.7, structured_output −2.6. The
  frontier-gate breach is one task's worth on n = 32 — it trips the letter of the zero-tolerance rule but
  is not a credible capability loss. The real regressions to fix are **tool_calling, connector_selection,
  and failure_recovery**.

### Next rung (P0.1) — failure-driven, not architectural

Fix the data, not the model. Rebalance the 0.2 blend to protect tool-calling / connector-selection /
failure-recovery trajectories (up-weight examples where the correct behavior is to *call a tool* or *hand
off*), while preserving the hallucination/safety gains P0 earned. No architecture change.
