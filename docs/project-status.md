# Protea — Project Status

**A living snapshot of what is built, what has been trained, what was decided, and what remains.**
Last updated: 2026-09-16. Canonical detail lives in the ADRs (`docs/adr/`), the model lineage
(`docs/lineage/protea-agent.md`), and the experiment write-ups (`docs/experiments/`). This file is the map;
those are the territory.

---

## 1. TL;DR — where the project stands

- **Product (v0) is decided:** ship the **frozen Qwen3-8B base + a guardrail system prompt**, served with Qwen3
  "thinking" off. It is the best overall model on our benchmark *and* it carries the safety posture — at **zero
  training cost**. See **ADR-017**.
- **The QLoRA agent-adapter training program is paused.** Four rungs (B0 baseline → P0 → P0.1 → P0.2) all land
  **below the frozen base**; the targeted data fix (P0.2) failed and *worsened* the key category. The blocker is
  **data/method, not a tunable** — no further blend/epoch tweak is justified.
- **Infrastructure is in place and working:** synthesis, dataset tooling, a judge-free evaluation harness
  (ZaraBench 0.1.1), QLoRA training, one-click GitHub-Actions launchers onto rented GPUs (RunPod), R2 object
  storage, and the provider abstraction. The whole B0→P0→P0.1→Exp0→P0.2 loop was run through it.
- **Nothing is running or spending.** The immediate next phase (when resumed) is **serving base+prompt**.

---

## 2. What Protea is

A commercial LLM platform: a **frozen open-weight base** (`Qwen/Qwen3-8B`) plus **hot-swappable QLoRA adapters**
("protea-agent"), fronted by a router and served through a vLLM-compatible inference path. The near-term product
target is the **Zara** agent marketplace (front-office / HR / operations / vertical business agents).

| Pin | Value |
|---|---|
| Base model | `Qwen/Qwen3-8B` @ commit `b968826d9c46dd6066d109eabc6255188de91218` |
| Training method | QLoRA — rank 32, alpha 64, dropout 0.05, lr 1e-4, seed 42, 4-bit NF4, bf16 |
| Benchmark | **ZaraBench 0.1.1** — 206 sealed tasks, **judge-free** (deterministic checks), task-set sha256 `095d5209d265` |
| Object storage | Cloudflare **R2** — bucket `protea-runs`, endpoint `…dd87d2c6….r2.cloudflarestorage.com` |
| Compute | Rented GPUs via **RunPod** (secure + community A6000/L40S/H100/A100), launched from GitHub Actions |

---

## 3. What is built (infrastructure)

All of the following exists, is tested (CI green), and has been exercised end-to-end.

### Data pipeline
- **Synthesis** (`protea/data_pipeline/synthetic.py`) — generates agent-training examples from seeds using an
  **open-weight teacher** (policy: open-weight teacher only; frontier models may *judge* but never *generate*
  training data — ADR-007/013). Crash-resilient, observable (`run-synth` / `synthesize` workflows).
- **Dataset tooling** (`protea/data_pipeline/`, `protea/cli.py dataset …`): `cap` (bound a task type's share of a
  blend), `trim` (compress over-long target fields + drop off-taxonomy rows), plus blend/rebalance. One-click via
  the `blend`, `rebalance`, and `trim` workflows.
- **Datasets in R2** (`protea_data/0.2.x/`): `0.2.0` (mined blend), `0.2.1` (tool_calling capped), `0.2.2`
  (trimmed, 1,211 rows). Splits are gitignored (proprietary); only keys + hashes live in the repo.

### Evaluation harness — ZaraBench 0.1.1
- Judge-free scoring: per-category **deterministic checks** (`protea/evaluation/evaluators.py`) — JSON parsability,
  schema validity, field accuracy, no-hallucinated-tools, tool bindings, etc. Driver runs multi-round tool
  conversations against canned tool results (`protea/evaluation/driver.py`, `runner.py`).
- **Regression budgets are policy** (ADR-016), anchored to the frozen **B0** baseline. Category tiers &
  per-category max-drop budgets: `frontier_gate` 0.00, `priority` 0.02, `guardrail` 0.00, `supporting` 0.05.
- Optional **system-prompt overlay** and **Qwen3 thinking toggle** on the eval (added for Exp 0 / serving), so the
  base can be scored under its production framing without disturbing the sealed instrument.

### Training & remote execution
- **QLoRA trainer** (`protea/training/`) with checkpoint-to-storage, runtime/idle limits, and a model card +
  metrics + manifest written per run.
- **Remote launchers** (`protea/training/remote/runpod.py`, …) — plan/estimate then launch onto RunPod, falling
  through GPU targets until one has capacity. A fixed allowlist forwards non-secret `PROTEA_*` knobs to the pod;
  secrets are referenced by name, never baked into artifacts.
- **Baked training image** (`Dockerfile.train`, `deployment/protea/entrypoint-*.sh`) auto-republished by
  `publish-train-image` when the entrypoints/requirements change.

### Providers & serving
- **Provider abstraction** (`protea/providers/`): `local_hf` (in-process base + optional LoRA, with the
  `enable_thinking` control), `anthropic`, `google`, `openai_compatible`, `mock`, behind a registry.
- **Serving path** (ADR-009): vLLM OpenAI-compatible server + a facade (`protea/serving/`). Serve-image publishing
  is staged (**PR #54**, parked) but **not yet deployed**.

### One-click workflows (GitHub Actions)
`run-synth` · `synthesize` · `blend` · `rebalance` · `trim` · `run-training` · `run-eval` · `fetch-logs`
(reads R2 into the run log) · `publish-train-image` · `ci` · `review`.

### Decisions of record
17 ADRs (`docs/adr/ADR-001…017`) cover the repo split, provider abstraction, dataset format, registries,
config, data pipeline, evaluation, training, inference, router, production hardening, the CitizenAI government
domain, teacher policy, the capability spec, trajectory scoring, **regression budgets (ADR-016)**, and the
**v0 = base + prompt decision (ADR-017)**.

---

## 4. What is trained (the model program)

The empirical loop, scored on the full sealed 206-task ZaraBench 0.1.1 suite unless noted. **All adapters lose to
the frozen base.**

| Rung | What it is | Overall ZaraScore |
|---|---|---|
| **B0** | Unmodified Qwen3-8B base (the anchor for ADR-016 budgets) | **80.7%** |
| P0 | First QLoRA adapter (0.2.0 blend, 3 epochs) | 77.9% |
| P0.1 | Rebalanced blend (tool_calling capped → 0.2.1, 3 epochs) | 75.4% |
| **P0.2** | Trimmed data (0.2.2) + **1 epoch** — the data-quality fix | **73.0%** |

Per-category, B0 vs the two best-documented adapters (P0.1, P0.2), against the ADR-016 gate:

| Category | Tier | B0 | P0.1 | P0.2 | P0.2 vs base |
|---|---|---|---|---|---|
| agent_generation | priority | 73.7% | 56.0% | **41.1%** | −32.6 ❌ |
| structured_output | frontier_gate | 96.9% | 92.6% | 93.2% | −3.7 |
| tool_calling | priority | 84.4% | 79.4% | 78.9% | −5.5 |
| connector_selection | priority | 84.5% | 84.2% | 91.0% | +6.5 |
| workflow_generation | supporting | 89.2% | 85.8% | 100.0% | +10.8 |
| business_reasoning | supporting | 85.6% | 86.7% | 81.1% | −4.5 |
| failure_recovery | supporting | 72.2% | 22.2% | **23.3%** | −48.9 ❌ |
| safety | guardrail | 72.1% | 83.3% | 85.0% | +12.9 |
| hallucination | guardrail | 34.4% | 80.0% | 53.3% | +18.9 |
| instruction_following | supporting | 77.5% | 69.2% | 74.2% | −3.3 |

### Exp 0 — is the guardrail gain a prompt effect?
Scored the **bare base under the product guardrail prompt** on the 50 guardrail-tier tasks
(`docs/experiments/exp0-guardrail-prompt-effect.md`). The adapters' guardrail wins **split**:
- **Safety ≈ a prompt effect** — base+prompt **82.1%** ≈ adapter 83.3% (vs bare base 72.1%). A prompt gets you there.
- **Hallucination = a training effect** — base+prompt **28.9%** ≈ bare base; **51 points below** the adapter (80.0%).
  A prompt cannot reproduce it.

### The falsified hypothesis (why the program is paused)
The `agent_generation` collapse was root-caused to a train↔eval **length/truncation** mismatch
(`docs/experiments/agent-generation-rootcause.md`), and P0.2 applied the fix (trim). Offline it removed **100%** of
the token-budget overflows — yet `agent_generation` got **worse** (56→41), with `json_parsable`/`field:category`
still the top failures. So **truncation was not the whole cause**, and cutting epochs 3→1 also weakened the one
target we wanted (hallucination 80→53). That is a **dataset/method problem** — not a blend/epoch/trim knob.

### Trained artifacts (in R2)
Adapters under `checkpoints/protea-agent-0.2.x/…/adapter`; the latest (P0.2) is
`checkpoints/protea-agent-0.2.2/protea-agent-0.2.2-qwen3-8b-qlora/20260915T044227Z/adapter`
(train_loss 1.05, eval_loss 0.56 — a clean run; the loss was never the problem). Eval reports under
`eval-reports/<run_id>/`; run logs under `logs/`.

---

## 5. Key decisions

- **ADR-016 — Regression budgets.** A candidate may not fall below the B0 baseline by more than its tier's budget,
  *per category*; improvements are always fine. This is what stops "+12 on tool use, −8 on grounding" from passing
  on the aggregate.
- **ADR-017 — v0 = base + guardrail prompt; training scoped to hallucination-only.** Ship base+prompt (served
  thinking-off). Safety leaves the training objective (the prompt owns it). An adapter ships **only** if it raises
  hallucination *without* regressing `agent_generation` or `failure_recovery` below base. **P0.2 failed that gate**,
  so the program is paused (banner on ADR-017).

---

## 6. The product today (v0)

**Serve `Qwen/Qwen3-8B` @ `b968826…` + `configs/evaluation/guardrail-system-prompt.md`, with Qwen3 thinking off.**

- Best overall model on ZaraBench, and it carries the safety posture for free.
- Thinking-off is a **serving** decision (reasoning-on is ~100× slower per task) as well as an eval setting.
- No adapter ships in v0.

---

## 7. What remains (roadmap)

> The evidence-backed, tiered action list (instrument fixes first, then serving, then training) is in
> [`evaluation-review.md`](evaluation-review.md), written 2026-09-16 at `c3815b5`.

### Near-term — stand up the v0 product (the next phase)
1. **Publish serve images** — **PR #54** (`publish-serve-images`) is staged but unmerged; land it.
2. **Deploy base+prompt** behind the vLLM path + facade (ADR-009), thinking-off, with the guardrail prompt wired
   in. *(Outward-facing / may incur cost — start only on explicit go.)*
3. **Validate thinking-off on the reasoning-heavy categories** (`agent_generation`, `structured_output`) before
   locking the serve config — one base+prompt full-suite run (~$2). Exp 0 only exercised the guardrail categories.
4. **Harden the product prompt** against the canary-echo failure Exp 0 surfaced (refuse *and* never repeat an
   injected code/hidden instruction, even to explain the refusal). Free, prompt-only.

### If/when the adapter program resumes
A future adapter needs a **fundamentally different input**, not a fourth blend:
- A materially **cleaner `agent_generation` dataset** — correct field values, not merely shorter targets (the
  falsified trim shows length was not the whole story).
- Or a **scope + method change**: train **hallucination-only** on clean, hallucination-specific data, with
  `assistant_only_loss` (needs a chat template with `{% generation %}` markers + a smoke test) to curb the
  catastrophic forgetting that cratered `failure_recovery`.
- **failure_recovery** has **zero** training coverage in the current blend — a rebalance can't restore a category
  it never taught; it needs authored/synthesized examples.

### Evaluation follow-ups (ADR-016 graduation)
- A **judged B0 baseline** (an LLM judge distinct from the model-under-test and any data generator) to make the
  safety / language floors real — the judge-free suite only polices the deterministic subset today.
- Land the **machine `base.json`** baseline in the repo (only the human-readable `.md` is committed) so
  `protea evaluate compare` can enforce floors automatically.
- Optional: a **base + thinking-off + no-prompt control** (~$0.50) to isolate the prompt's safety contribution
  from the thinking-off change exactly.

### Open / parked
- **PR #64** — records the P0.2 result + program pause (lineage / ADR-017 banner / root-cause outcome). *(Being
  landed alongside this document.)*
- **PR #54** — serve-image publishing (parked; part of the near-term serving work).
- **Zara marketplace deployment** — on hold: the base is the best model and there is no releasable adapter, so the
  first deployable is base+prompt.

---

## 8. Repository map

| Path | What's there |
|---|---|
| `protea/data_pipeline/` | synthesis, `cap`, `trim`, blend/rebalance |
| `protea/evaluation/` | ZaraBench driver, runner, deterministic evaluators |
| `protea/training/` | QLoRA trainer + `remote/` launchers (RunPod) |
| `protea/providers/` | `local_hf` (+ `enable_thinking`), anthropic, google, openai_compatible, mock, registry |
| `protea/serving/` | vLLM-compatible facade / OpenAI-compat surface |
| `protea/config/` | settings (env-backed) + config models |
| `configs/training/` | `protea-agent-8b-qlora-0.2{,.1,.2}.yaml` + smoke/dev configs |
| `configs/remote/` | RunPod / Azure / k8s / ssh GPU targets |
| `configs/evaluation/` | `zarabench-0.1.yaml`, `guardrail-system-prompt.md`, security/citizen suites |
| `.github/workflows/` | the one-click launchers (see §3) |
| `docs/adr/` | 17 architecture decision records |
| `docs/experiments/` | agent-generation root-cause, Exp 0 write-up |
| `docs/lineage/protea-agent.md` | the full B0→P0→P0.1→P0.2 rung-by-rung record |

---

## 9. How the loop runs (operational notes)

- **GPU work is launched from GitHub Actions**, not locally: `run-training` / `run-eval` (dispatch → the pod runs
  independently for minutes–hours, then pushes to R2 and self-stops). A **green workflow means the launch
  succeeded**, not that the pod finished.
- **Read results from R2** with the `fetch-logs` workflow (prints text objects into its run log): reports under
  `eval-reports/`, adapters/manifests under `checkpoints/`, streaming logs under `logs/`.
- **Adding a new config/prompt does not rebuild the training image** on its own — only entrypoint/requirements
  changes trigger `publish-train-image`. Touch an entrypoint (or republish manually) when new baked code/config
  must reach the pod.

## 10. Standing constraints

- **Open-weight teacher only** for synthesis; frontier models may judge, never generate training data.
- **No GPU spend without explicit approval** with the cost quoted; **no outward-facing deployment without explicit
  go**.
- Secrets are referenced by name and never written into artifacts, logs, or chat.
- The sealed ZaraBench instrument is not modified for a comparison; product-config runs (system prompt / thinking
  flag) are recorded as a distinct config hash and compared like-for-like.
