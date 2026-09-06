# Training — local runs, remote GPU jobs, model cards

Design decisions are in `adr/ADR-008-training.md`. Everything below is CPU-safe except the two commands marked as boundaries.

## Install

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu   # or the CUDA index on a GPU host
pip install -e ".[dev,train-cpu]"                                     # transformers, peft, trl, datasets, accelerate
pip install -e ".[train]"                                             # GPU host: adds bitsandbytes (QLoRA) and mlflow
```

## Commands

| Command | What it does | Spends money |
|---|---|---|
| `protea train local --config C --dry-run` | load and check both splits, refuse golden examples, verify the registry pin, print stats | no |
| `protea train local --config C` | create a run directory with the frozen config, train, checkpoint, save the adapter, write the card | no on CPU |
| `protea train local --config C --resume` | resume the latest run of the experiment from its last checkpoint; refuses a changed config | no |
| `protea train remote --config C --remote R` | estimate duration and cost, render the job artefacts, print the plan; launches nothing | no |
| `protea train remote --config C --remote R --confirm` | submit the job to the provider | **yes** |
| `protea train card <run_dir>` | regenerate the model card from the frozen config and manifest | no |
| `protea train runs` | list runs with status, steps and final loss | no |
| `protea train register <run_dir> --version V` | create an `experimental` model-registry entry with the adapter hash | no |

## Run directory

```
checkpoints/<experiment>/<run_id>/
  config.yaml      frozen config (hash in the manifest; resume refuses a different hash)
  manifest.json    provenance, environment, status, steps, final metrics, resumed_from
  metrics.jsonl    one line per logging step (mirrored to MLflow when MLFLOW_TRACKING_URI is set)
  checkpoints/     checkpoint-N directories (save_total_limit keeps the last few)
  adapter/         LoRA adapter (or full weights for method: sft) plus tokenizer
  model-card.md    generated card; says "ZaraBench: not yet run" until a report exists
```

## Configs

- `configs/training/ci-smoke.yaml` — `tiny-random` base, six steps, fixture data; the CI smoke and a good template for local experiments.
- `configs/training/dev-tiny.yaml` — Qwen2.5-0.5B-Instruct LoRA, 20 steps on the real dataset; downloads a ~1 GB model, runs on a laptop CPU in minutes.
- `configs/training/protea-agent-8b-qlora.yaml` — the first real experiment (Qwen3-8B, QLoRA rank 32, 3 epochs, 6k context). GPU only.
- `configs/remote/*.yaml` — one file per provider: `runpod-a100`, `ssh-generic`, `azure-nc24`, `kubernetes`.
- `configs/pricing/gpu.yaml` — indicative USD/hour and tokens/s per GPU class. Refresh before a paid run.

## Data rendering

Each ADR-003 example becomes `{"messages": [{role, content}]}`. Tool schemas are folded into the system turn in the Hermes format (`<tools>…</tools>`), assistant tool calls become `<tool_call>{json}</tool_call>` blocks, and tool results keep role `tool`. Qwen chat templates render this natively; other templates see plain text. Examples marked `split: golden` abort the run.

## Remote plan (execution boundary)

`protea train remote --config configs/training/protea-agent-8b-qlora.yaml --remote configs/remote/runpod-a100.yaml` prints, for the registered dataset 0.1.0:

| | |
|---|---|
| tokens × epochs | ≈7.0 M |
| estimated duration | ≈1.2 h including setup |
| estimated cost | ≈USD 2 on an A100-80GB (RunPod list price) |
| safeguards | 300 min hard limit, 15 min idle shutdown, checkpoint sync every 10 min to S3, SIGTERM checkpoint, secrets by name |

Artefacts are written under `runs/remote/<experiment>-<provider>/` for review: `job.sh` + `launch.sh` (SSH), `runpod-deploy.graphql`, `main.bicep` + `cloud-init.yaml` + `deploy.sh` (Azure), `job.yaml` (Kubernetes). Nothing is submitted without `--confirm`, and `--confirm` still refuses when the estimate exceeds `budget.max_cost_usd` or the GPU cannot fit the model.

Before the first paid run you also need: the training image (Phase 5), the `protea-storage` sync helper or an rsync target, `HF_TOKEN` in the host environment, and a ZaraBench baseline of the unmodified base model to compare against (Phase 3 boundary).

## After a run

1. `protea evaluate run --provider protea --model <served adapter>` once the adapter is served (Phase 5).
2. `protea evaluate compare candidate.json --base base.json --frontier frontier.json` for the release gate and kill criterion.
3. `protea train register <run_dir> --version 0.1.0`, then `protea registry promote protea-agent-0.1.0 --to candidate` only with a passing report.
