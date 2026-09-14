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
