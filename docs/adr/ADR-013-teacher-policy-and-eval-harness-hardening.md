# ADR-013 — Open-weight teacher policy, remote eval/synthesis harness, and the tool-call coverage gap

**Status:** accepted · **Date:** 2026-09-11 · **Scope:** data synthesis (ADR-006), evaluation (ADR-007), inference (ADR-009)

## Context

Protea is a commercial product. During the first end-to-end runs (v0.2 blended model, ZaraBench scored on rented
GPUs) three things had to be decided and several defects fixed. This ADR records the decisions so future sessions
don't relitigate them.

## Decisions

1. **The synthesis teacher is open-weight only.** Protea is a competing commercial LLM, so training it on the
   outputs of Anthropic / OpenAI / Google models conflicts with those providers' terms (strategy-review C3). The
   `dataset synthesize` teacher runs the `local` provider (an open-weight model, e.g. Qwen3-8B) on the same rented
   GPU as everything else, so no proprietary outputs enter the training set and the whole flow bills to one place.
   Proprietary Claude/GPT models may assist engineering and, cautiously, judging — never training-data generation.

2. **CC-BY-NC assets are comparators, not ingredients.** InkubaLM / Inkuba-Mono and any other non-commercial
   corpus/model may be used to benchmark Protea, never as training input — a derivative would inherit the
   non-commercial restriction and taint the weights irreversibly.

3. **Remote eval and synthesis run as baked entrypoints on disposable pods.** `entrypoint-eval.sh` scores an
   existing adapter with the judge-free `local` provider; `entrypoint-synth.sh` completes seeds with an
   open-weight teacher. Both pull inputs from and stream outputs + logs to object storage, and self-terminate.
   Selected via `PROTEA_ENTRYPOINT`; the launcher forwards non-secret `PROTEA_*` knobs into the pod env.

4. **Unattended GPU runs must be crash-resilient and observable, on non-preemptible hardware.** Runs that must
   complete use secure/on-demand GPU targets (community/spot instances get reclaimed mid-run). Long jobs write
   results incrementally and stream partial output + progress to storage every ~45s, so a SIGKILL never loses a
   whole batch and the failure point is visible.

## Defects fixed en route (evidence for the above)

- **In-run ZaraBench produced no score** — the eval task set wasn't COPYied into the training image; the in-run
  eval self-skipped. Fixed by shipping `evaluation/` in the image.
- **Eval OOM-killed on load** — the `local` provider defaulted to CPU/fp32 and loaded the 8B in ~32 GB. Fixed:
  auto-select CUDA + bf16 with `low_cpu_mem_usage`, and free per-generation allocations between calls.
- **Eval produced no report** — scratch was written under root-owned `$WORKDIR`; the unprivileged image user got
  EACCES. Fixed: all scratch under `/tmp`, configs still read from the repo.
- **`<think>` reasoning broke bare-output checks** — Qwen3 emits a `<think>…</think>` block ahead of its answer,
  which failed `json_parsable`. Fixed by stripping the reasoning trace in the provider. This alone moved the v0.2
  sample from **29.2% → 58.7% ZaraScore**.

## The finding that drives the next phase

The remaining v0.2 gap is a **training-data coverage hole, not a capability gap**: of 1,743 training rows only 257
emit a tool call (290 calls total), dominated by `handoff_to_human` (74) with **zero** `book_appointment` /
`log_*` / `capture_*` — exactly the "write-after-confirm" tools ZaraBench probes. The model narrates instead of
calling those tools because it was never shown them. The fix is targeted synthesis (the `--scenario` / `--balance`
selection knobs) to fill the zero-coverage tools, validated by the pilot, before any larger scale-up.

## Consequences

- Per-category `min_score` gates and `release_min_zarascore` in the suite config stay `0.0` until a clean frozen
  baseline (vanilla Qwen3-8B and a true v0.1 adapter) exists to set them from — see the implementation roadmap.
- The eval stays judge-free; the golden-lock hold-out and "never release on training loss" rules are reaffirmed.
