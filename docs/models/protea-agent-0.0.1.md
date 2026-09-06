# protea-agent — run rehearsal-1

**Experiment:** rehearsal-cpu · **Status:** completed · **Created:** 2026-09-06T15:11:31.642213+00:00

## Provenance

| Field | Value |
|---|---|
| Base model | `Qwen/Qwen2.5-0.5B-Instruct` |
| Method | lora (rank 16, alpha 32, dropout 0.05) |
| Training dataset | `agent-training-0.1.0` — sha256 `ddc4821f45fd` |
| Examples / approx tokens | 400 / 589,823 |
| Validation | sha256 `60def3d00ab0` |
| Config hash | `c7cd343bb3ec` |
| Code commit | `7777bca8f080` (dirty tree) |
| Seed | 42 |
| Environment | python=3.11.15, platform=Linux-6.18.44-fc-v24-x86_64-with-glibc2.39, torch=2.14.0+cpu, cuda=False, device=cpu, transformers=5.16.1, trl=1.12.0, peft=0.20.0 |

## Hyperparameters

| Field | Value |
|---|---|
| Epochs / max steps | 1.0 / 150 |
| Learning rate / scheduler / warmup | 0.0002 / cosine / 0.03 |
| Sequence length | 1024 |
| Batch × accumulation | 1 × 2 |
| Precision | fp32 |
| Gradient checkpointing | False |

## Results

| Field | Value |
|---|---|
| Steps completed | 150 |
| Duration | 2786 s |
| epoch | 0.7500 |
| eval_entropy | 0.4457 |
| eval_loss | 0.5472 |
| eval_mean_token_accuracy | 0.8997 |
| eval_num_tokens | 207565.0000 |
| eval_runtime | 194.0185 |
| eval_samples_per_second | 0.4590 |
| eval_steps_per_second | 0.4590 |
| total_flos | 456679929964800.0000 |
| train_loss | 0.7540 |
| train_runtime | 2557.4426 |
| train_samples_per_second | 0.1170 |
| train_steps_per_second | 0.0590 |

ZaraBench: not yet run. A checkpoint without a benchmark report is `experimental` and never promoted.

## Intended use and limits

- Serves the Zara agent platform through the Protea router for the task types where its ZaraBench category score clears the configured threshold; everything else stays on a frontier provider.
- Trained only on sources classified SAFE in the data-risk assessment: PII redacted, secrets blocked, brand and infrastructure names scrubbed, golden families held out.
- Base-model licence applies to the merged weights; the adapter inherits the training data's licence status (no synthetic examples).
- Not a general assistant: no safety tuning beyond the estate's guardrails; must run behind the validation gate and the security pack.

## Data lineage

See the dataset card referenced by the registry entry for sources, commits, scan results and split counts.
