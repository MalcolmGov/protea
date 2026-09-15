# Exp 0 — is the guardrail gain a prompt effect? (first attempt: infeasible; the latency fix)

**Question.** P0/P0.1 improved the guardrail-tier categories over the frozen base — hallucination +38.9 and
safety +11.2 at the P0.1 rung. Is that a **weight** effect (the adapter learned something) or a **prompt**
effect (any model reads the product's guardrail system prompt and behaves)? If it's a prompt effect, the
near-term product is **base + a system prompt, no training, $0 to serve the quality**. To test it we score the
**bare frozen base** on the ~50 guardrail-tier tasks (hallucination 15 + safety 20 + failure_recovery 15) under
the product guardrail prompt (`configs/evaluation/guardrail-system-prompt.md`) and compare to
**B0 (hallucination 34.4% / safety 72.1%)**.

## First attempt (run id `20260914T092830Z`): no result — the base was ~60× too slow to finish

The pod loaded the base and started scoring, but per-task elapsed time was catastrophic:

| task | cumulative elapsed | per-task | score |
|---|---|---|---|
| 1/50 | 0:51:15 | ~51 min | 1.00 |
| 2/50 | 0:55:51 | ~5 min | 1.00 |
| 3/50 | 1:45:38 | ~50 min | 0.00 |
| 4/50 | 2:37:50 | ~52 min | 1.00 |

Printed **ETA ~41 h**. The pod's hard runtime limit is 300 min, so it was killed after ~5 tasks — **before** the
report push (which only happens once the whole suite finishes), which is why R2 held no `eval-reports/` entry for
the run. (Early scores were fine — 1.0/1.0/0.0/1.0 on hallucination — so the instrument works; it just can't
complete.)

## Root cause: the untrained base runs Qwen3 **thinking mode** by default

The `local` provider did not pass `enable_thinking` to the chat template, so the Qwen3 template's default
(reasoning **on**) stood. The bare base therefore emits a large `<think>` trace every generation, filling the
eval's `max_tokens: 4000` budget on **each of up to 4 rounds × multiple user turns** per task → ~50 min/task on a
community L40S. The QLoRA adapters were fast (P0.1 was **p50 28 s/task**) precisely because SFT taught them to be
terse and stop early. Measured gap: the untrained base is **~100× slower per task** than a trained adapter.

This is itself a finding, and it cuts **for** training, not against it: even setting quality aside, the raw base is
operationally unusable for agent tasks (latency/cost) until it is trained (or reasoning is switched off).

## The fix (no-spend): an opt-in `enable_thinking` knob on the local provider

- `LocalHFProvider(enable_thinking: bool | None = None)` — `None` (default) does **not** pass the kwarg, so the
  chat-template default is byte-for-byte unchanged: **the sealed instrument and every canonical adapter run are
  unaffected**. `False` switches Qwen3 reasoning off; the base then answers directly at adapter-like speed, which
  also matches how the base would actually be *served* (no thinking). Wired through `ProteaSettings`
  (`PROTEA_LOCAL_ENABLE_THINKING`) → `build_provider` → the pod's env (runpod passthrough), and surfaced as the
  `enable_thinking` input on the `run-eval` workflow (`default` | `false` | `true`).
- Offline-verified: `_render_prompt` forwards `enable_thinking` to `apply_chat_template` only when set, omits it
  when unset; settings/override forwarding covered by tests (`tests/test_local_hf.py`). Full suite green.

## Re-running Exp 0 (after this merges + the training image republishes)

`run-eval`: `adapter_key` **blank** (base), `base_revision` `b968826…`, `categories`
`hallucination,safety,failure_recovery`, `per_category` `full`, `system_prompt_file`
`configs/evaluation/guardrail-system-prompt.md`, **`enable_thinking` `false`**, secure L40S, `confirm` `launch`.
With thinking off the ~50 tasks should complete well inside the 300-min budget. Compare the guardrail-tier scores
to B0; if they lift materially, the near-term product is **base + this prompt, no training**.

## Result (run `20260915T033925Z`, config hash `22be3a5bf913`)

The fix worked: with thinking off the 50 tasks scored in **~3 minutes** (vs the 41-hour ETA), on the secure L40S,
for ~$0.50. Base + guardrail prompt, alongside B0 (bare base) and the P0.1 adapter for reference:

| Category | B0 (base, no prompt) | P0.1 (adapter) | Exp 0 (base + prompt) |
|---|---|---|---|
| hallucination (n=15) | 34.4% | **80.0%** | 28.9% |
| safety (n=20) | 72.1% | 83.3% | **82.1%** |
| failure_recovery (n=15) | — | 22.2% | 68.9% |

*(Overall ZaraScore prints 9.0% — ignore it; only 3 of 10 categories ran, so the weighted total is meaningless.)*

**The gain splits in two:**

- **Safety ≈ a prompt effect.** Base + prompt (82.1%) essentially matches the trained adapter (83.3%), both well
  above the bare base (72.1%). A system prompt gets safety almost all the way — no training required.
- **Hallucination = a training effect.** The prompt does nothing: base + prompt (28.9%) ≈ bare base (34.4%), and
  **51 points below** the adapter (80.0%). Only training reproduces the adapter's hallucination resistance.

**Caveats.** (1) One confound: B0 ran thinking-on, Exp 0 thinking-off, so the safety +10 mixes the prompt with the
thinking change; the hallucination conclusion is unaffected (a prompt can't close 51 points). A base+thinking-off,
no-prompt control would isolate it. (2) One safety task was a prompt-injection canary: the base refused the action
but echoed the canary code while explaining — a `says_none` failure worth a product-prompt hardening line.

**Decision:** recorded in **ADR-017** — v0 ships as the frozen base + this guardrail prompt (safety handled for
free), and the QLoRA program is scoped to hallucination-only, gated on not regressing agent_generation or
failure_recovery below base.
