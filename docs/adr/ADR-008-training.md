# ADR-008 — Training runs, immutable snapshots and remote GPU jobs

**Status:** accepted · **Date:** 2026-09-06 · **Phase:** 4

## Context

The specification wants training driven entirely by configuration, with runs that can be reproduced and resumed, a model card per checkpoint, and GPU work that never starts without an explicit, priced confirmation (§16–§19, §52, §78). The estate has no GPU: the Zara VPS is a 4 GB box and this repository's CI runs on shared runners. Frameworks move fast (transformers 5, TRL 1.x) and their argument names change between releases.

## Decisions

1. **One trainer, one config.** `protea/training/trainer.py` maps `TrainingConfig` onto TRL's `SFTTrainer` with PEFT for LoRA and bitsandbytes NF4 for QLoRA. Nothing is read from the environment except secrets and the MLflow URI. A compatibility shim keeps only the arguments the installed TRL accepts and maps `warmup_ratio` to the newer fraction form, so version drift fails loudly in one place.
2. **Runs are immutable.** `create_run` writes the validated config as `config.yaml` and its content hash into `manifest.json`. `--resume` reopens the latest run of the experiment and refuses if the hash differs. Hyperparameter changes are new runs, never edits.
3. **Provenance in the manifest, not in a notebook.** Dataset key and sha256 of both split files, base model and revision, method, seed, git commit and dirty flag, library versions, device, steps, duration, final metrics, and the checkpoint the run resumed from. The model card is rendered from the manifest and frozen config; it says plainly when ZaraBench has not been run.
4. **The registry pins the dataset.** The training file must be byte-identical to the registered dataset entry (`check_dataset_pin`); golden files and golden-marked examples are refused. `--allow-unregistered` exists for fixtures and smoke runs and prints a warning.
5. **Offline smoke in CI.** `base_model: tiny-random` builds a two-layer random Qwen2-architecture model and trains a BPE tokenizer on the fixture text, so the full path — render, train, checkpoint, resume, evaluate, save adapter, card, register — runs on a CPU runner in seconds with no download. It proves the plumbing, not model quality.
6. **Remote adapters plan first and launch only on `--confirm`.** SSH, RunPod, Azure and Kubernetes adapters render the exact artefacts they would submit (job script, GraphQL mutation, Bicep + cloud-init, Job YAML) and a plan with provider, GPU, count, tokens, throughput, duration, cost, budget check, VRAM check and safeguards. Prices and throughputs live in `configs/pricing/gpu.yaml` and are estimates to refresh before a paid run, not facts.
7. **Safeguards are in every plan.** A hard runtime limit (`timeout`, `activeDeadlineSeconds`, a DevTestLab shutdown schedule, or the container entrypoint), idle shutdown, checkpoint-before-stop on SIGTERM, periodic checkpoint sync to storage that outlives the instance, and secrets referenced by environment variable name only.
8. **DPO is not implemented.** The config accepts `method: dpo` for schema completeness, but the trainer raises until validator-derived preference pairs exist (strategy review A4).

## Consequences

- The first QLoRA run on Qwen3-8B plans at roughly 1.2 GPU-hours and USD 2 on a RunPod A100-80GB against dataset 0.1.0; that is an estimate printed by `protea train remote`, and launching it remains the user's decision.
- The GPU container image (`ghcr.io/malcolmgov/protea-train`) and the `protea-storage` sync helper referenced by the job scripts are Phase 5 deliverables; until they exist the SSH adapter is the path that needs nothing but a rented box and rsync.
- `assistant_only_loss` needs a chat template with generation markers; the default is off, so loss covers the whole conversation. Turn it on once the base model's template supports it.
- MLflow mirroring is optional and lazy; the JSONL metrics file is always written.
