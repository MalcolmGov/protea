# Protea — Engineering Operating Contract

Protea is Zara's sovereign agent-intelligence layer. Qwen3-8B is the first foundation model beneath it, adapted
with QLoRA. Think of Protea as **capability spec + datasets + ZaraBench + training recipes + versioned adapters**
— not "Qwen + LoRA weights". The base model can change (Qwen3-8B → 14B → another open-weight base) without
redefining what Protea is.

The binding decisions live in `docs/adr/`. This file is the day-to-day operating contract; when it and an ADR
disagree, the ADR wins — fix this file.

## Architecture
- Weights encode stable **behaviour**, not customer knowledge. Customer data lives in RAG / tools / databases
  (ADR-006, `docs/data-governance.md`).
- Protea **proposes** actions; the Zara runtime **decides and executes** them — identity, permissions, secrets,
  tool execution, audit. Protea never performs a privileged action itself.
- All model access goes through the provider abstraction (ADR-002). `local_hf` is for eval/dev/CPU rehearsal;
  vLLM is the production serving path (ADR-009). Never hardcode a provider.
- Datasets, adapters, configs and evaluations are versioned and reproducible from config + seed (ADR-004, ADR-005).

## Commercial & licensing constraints (binding)
- Protea is a **commercial** product, so training data must be commercially clean.
- The synthesis **teacher is open-weight only**. Never train on outputs of Anthropic / OpenAI / Google models —
  their terms restrict using outputs to build a competing model (strategy-review C3). Claude/GPT may assist
  *engineering* and, cautiously, *judging*, but never generate training data.
- CC-BY-NC sources (e.g. InkubaLM / Inkuba-Mono) are **eval comparators only, never training inputs** — a model
  trained on them becomes a derivative bound by the non-commercial restriction.
- Every training row carries `license_status`; the review lane (`review.yml`) is the gate. Never stamp an
  unverified row `approved`.

## The evaluation is the source of truth
- ZaraBench is **judge-free / deterministic** by default (ADR-007). Keep it that way. If an LLM judge is ever
  used it is a *supplementary* signal, never the sole gate, and must differ from both the model under test and
  the synthetic-data generator (`judge_must_differ_from_generator`).
- The `golden.lock` hold-out is sacred: sealed families never enter train/validation.
- **Never alter an eval to make a model pass it. Never release on training loss.**
- Judge every change against the frozen baseline **and** the previous Protea release, **per category** — an
  aggregate score hides regressions (e.g. a headline number that masks a 0% category or a safety regression).

## How to make a change
1. Inspect the relevant code and ADR before claiming anything.
2. Name the evaluation that proves the change helps; add one if it is missing.
3. Make the smallest correct change. Don't widen scope; don't add services/abstractions ahead of need.
4. Run the repo's fast checks (`ruff check .`, `pytest`) **and** the relevant eval before opening a PR — a push
   that turns CI red costs a cycle and reviewer trust.
5. Keep adapters/datasets/configs reproducible from config + seed.

## Unattended GPU runs
- Community/spot GPUs are preemptible — use **secure/on-demand** targets for any run that must complete.
- Any long GPU job must stream progress **and** partial results to storage as it goes; a crash must never lose
  everything (see `deployment/protea/entrypoint-synth.sh`). Prefer `aws s3 cp` for single files —
  `protea-storage push` wraps `aws s3 sync`, which is directory-only.
