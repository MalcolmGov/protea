# Root cause of the agent_generation collapse — and the offline fix (Phase A/B)

After three rungs (B0 80.7% → P0 77.9% → P0.1 75.4%) **both adapters lost to the frozen base**, and the single
biggest driver of P0.1's loss was **agent_generation** — the highest-weighted category (0.20) — falling
73.7% (B0) → 72.0% (P0) → **56.0%** (P0.1). This doc records the root cause and the no-GPU fix that removes it.

## Root cause: a train↔eval length/shape mismatch (not the blend ratio)

The mined `agent_generation` targets embed multi-thousand-token `guardrails` / `system_prompt` prose:

- 388 mined rows (`protea_data/agent-training/0.2.0/train.jsonl`), target length **p50 3,268 tok, p90 5,108,
  max 6,355** (chars/4). `guardrails` + `system_prompt` alone are ~1,983 of ~3,229 avg tokens per target.
- **~35% of targets exceed the eval's own `max_tokens: 4000`** (`configs/evaluation/zarabench-0.1.yaml`).

But AgentSpecLite (the schema `schema_valid` checks) types `guardrails` and `system_prompt` as bare `string`
with **no minimum length** (`evaluation/zarabench/0.1/tasks.jsonl`), and none of agent_generation's 7 deterministic
checks (`json_parsable`, `schema_valid`, `field:category/tier/channels/languages`, `no_hallucinated_tools` —
`protea/evaluation/evaluators.py`) inspect that prose. A 400-token concise spec scores **identically** to a
5,000-token one — but only the concise one fits the generation budget.

So SFT teaches the model to emit long specs → at eval they **truncate mid-JSON** → `json_parsable` fails → and
because `_structured_checks` returns immediately on a parse failure, the whole task scores **0** instead of ~6/7.
That all-or-nothing cliff also explains P0.1's signature anomaly — **pass-rate rose (8%→16%) while mean fell
(72→56)**: the specs the model finished came out more faithful, while the long ones went to zero.

**Why capping tool_calling made agent_generation worse (−16):** the cap never touched the agent_gen rows, but it
shrank the blend (1743→1269) and **raised agent_gen's share 22%→31%**, so the model imitated the long-spec style
harder → more truncations. The blend was rebalanced by *row count*; agent_generation dominates by *tokens*. We
moved the wrong lever.

Two secondary defects confirmed in the same data:
- **Off-taxonomy categories:** 58/388 rows label `category` as `commerce`/`sales`, which are not in the eval's
  allowed set (`front-office`/`hr`/`operations`/`vertical`) — guaranteed-wrong `field:category` on generalization.
- **failure_recovery has zero training rows** yet 15 sealed eval tasks — the base's 72% washes out to ~22% by
  pure forgetting (a separate fix: needs open-weight synthesis coverage, not addressed here).

Deferred (needs its own validated change, not in this pass):
- **`assistant_only_loss` is off** (`protea/config/models.py:63`, defaults False, unset in both configs) → loss
  runs over the whole prompt, not just the completion. Enabling it needs a chat template with `{% generation %}`
  markers + a training smoke test; recommended as the next config lever after trim is measured.
- **Eval `max_tokens` is arguably too low** (6 of 25 golden references themselves exceed 4000 tokens, so those
  tasks are unsatisfiable for any model). Raising it changes the measuring instrument and would require
  re-establishing B0, so it is *not* changed here; the trim fixes our model's side at the current budget.

## The fix (Phase A) — `dataset trim`, offline, no GPU

`dataset trim` (`protea/data_pipeline/trim.py`, `protea dataset trim`) rewrites a split so training stops
teaching un-parseable output: it caps named target string fields to a max length (every eval-scored field left
byte-identical) and drops rows carrying off-taxonomy field values. Applied to the 0.2.1 blend with the defaults
(`guardrails`/`system_prompt` capped at 800 chars; `category ∈ {commerce, sales}` dropped) it produces the 0.2.2
split:

```
read 1269 rows; trimmed fields {guardrails: 300, system_prompt: 330} (−2,064,177 chars); dropped off-taxonomy 58
kept 1211 rows   (tool_calling 250, structured_output 407, agent_generation 330, connector_selection 142, routing 82)
```

Target length after trim: **p50 1,370 tok, max 2,269 tok — 0% over the 4000-token budget** (was 35%).

## The proof (Phase B) — replay through the real evaluators, no inference

Each agent_generation target was replayed through the actual deterministic checks
(`protea/evaluation/evaluators.py:_structured_checks`) with an `Expect` built from its own scored fields + the
AgentSpecLite schema:

| Check | Result |
|---|---|
| Trimmed targets that still score **1.0** (compression broke no scored field) | **330 / 330** |
| Original targets that **fail `json_parsable`** when clipped at the 4000-tok eval budget (the collapse) | **98 / 388 (25%)** |
| Trimmed targets that fail `json_parsable` when clipped at 4000 tok | **0 / 330** |

The fix is sound before a cent is spent: it eliminates the truncation-collapse mechanism (25% → 0%) while leaving
every scored field intact (330/330 score 1.0).

## Recommended next experiment (needs approval — it spends)

**P0.2:** train `configs/training/protea-agent-8b-qlora-0.2.2.yaml` (the trimmed 0.2.2 blend, **1 epoch** instead
of 3 to curb forgetting), then **smoke-eval on a per-category subset**, not the full 206. Predicted: agent_generation
and structured_output snap back toward base while the hallucination/safety gains hold. Estimated ~$2 + one smoke
eval. If it clears the gates, it is the first candidate that could beat base; if not, the agent_generation data is
net-negative and should be dropped from the blend (the base is already best there).

Do **not** run another full 206-task eval per iteration, and do **not** rebalance by row count again.
