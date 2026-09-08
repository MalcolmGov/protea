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

**Checkpoint storage.** Checkpoints and the trained adapter must land on a store that outlives the disposable pod. `configs/remote/runpod-a100.yaml` uses Cloudflare R2 (S3-compatible): set `storage.uri` to your bucket, `storage.endpoint` to `https://<account-id>.r2.cloudflarestorage.com`, and put the R2 access key/secret in `PROTEA_STORAGE_CREDENTIALS` at launch (never in the file). The endpoint is passed to the job as `AWS_ENDPOINT_URL`, which `aws s3 sync` and boto3 honour; R2 uses region `auto`. Plain AWS S3 needs no `endpoint`. The `protea storage pull` helper you run afterwards reads the same `AWS_ENDPOINT_URL` from your shell to fetch the adapter back.

**How the pod persists its work.** The training image runs `deployment/protea/entrypoint-train.sh`. It reads everything from the environment the adapter sets, then: parses `PROTEA_STORAGE_CREDENTIALS` (format `<access_key_id>:<secret_access_key>`) into the AWS env vars; pulls the dataset and any prior checkpoints from `PROTEA_STORAGE`; streams checkpoints back every `checkpoint_sync_minutes`; traps `SIGTERM` to checkpoint before exit; wraps the trainer in `timeout` at the runtime limit; and pushes the final adapter before exiting. So the dataset must be **seeded to storage first** (`protea-storage push protea_data <uri>`), and the pod self-terminates after the run only if you also pass it `RUNPOD_API_KEY` (opt-in — the launch does not inject it; otherwise stop the pod yourself once the final sync line prints).

**One-click run (no local setup).** `.github/workflows/run-training.yml` seeds the dataset and launches the run from GitHub Actions, so no clone, Python, or AWS CLI is needed locally. Add four repository secrets — `RUNPOD_API_KEY`, `HF_TOKEN`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` — then run the workflow with your bucket and endpoint as inputs. It defaults to a `dry-run` (prints the plan and cost, rents nothing); choosing `launch` seeds the dataset and rents the A100 (~USD 2). Secrets are read from GitHub and expanded into the launch mutation in memory (`RunPodAdapter._resolved_mutation`); the on-disk artefact keeps `${NAME}` placeholders.

## After a run — measuring the adapter

The whole point of the run is to beat base Qwen on the benchmarks. Once the pod prints its final
`adapter and checkpoints synced to <storage>` line and the adapter is in your bucket, evaluate it:

0. **Fetch the adapter back** from storage (the pod's disk is gone): with the R2 env vars set,
   `protea-storage pull s3://<bucket>/runs <local runs dir>`. `AWS_ENDPOINT_URL` selects R2; region is `auto`.
1. **Serve it.** Stand up the adapter behind the vLLM facade (the `Dockerfile.infer` image, `--adapter <path>`).
   **Execution boundary:** serving an 8B model needs a GPU, so this is a short rental with its own cost — quote it
   and confirm before standing it up, exactly like the training run.
2. **Score it.** `protea evaluate run --provider protea --model <served adapter>` for ZaraBench, and
   `protea evaluate run --config configs/evaluation/citizen-0.1.yaml --provider protea --model <served adapter>`
   for the government slice. Compare against the base-model and frontier reports already in `evaluation/reports/`.
3. **Gate.** `protea evaluate compare candidate.json --base base.json --frontier frontier.json` applies the
   release gate and kill criterion — the fine-tune has to clear the floor and close the gap to frontier.
4. **Register & promote.** `protea train register <run_dir> --version 0.1.0`, then
   `protea registry promote protea-agent-0.1.0 --to candidate` — only with a passing report.

A **free first read** before paying to serve: point the eval at base Qwen on your own machine via Ollama
(`--provider ollama --model qwen3:8b`) to capture the "before" number the fine-tune must beat. CitizenBench is
deterministic (no judge), so that baseline costs nothing.
