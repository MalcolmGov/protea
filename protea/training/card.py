"""Model card generation (spec §78): everything a reviewer needs to trust or reject a checkpoint."""

from __future__ import annotations

from typing import Any

from protea.config.models import TrainingConfig
from protea.training.run import RunManifest


def _kv(rows: list[tuple[str, Any]]) -> list[str]:
    return ["| Field | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]


def model_card(manifest: RunManifest, cfg: TrainingConfig, *, zarabench: dict[str, Any] | None = None) -> str:
    t = cfg.training
    lora = cfg.lora
    train = manifest.train
    lines = [
        f"# {manifest.model_family} — run {manifest.run_id}",
        "",
        f"**Experiment:** {manifest.experiment_name} · **Status:** {manifest.status} · **Created:** {manifest.created_at}",
        "",
        "## Provenance",
        "",
        *_kv(
            [
                ("Base model", f"`{manifest.base_model}`" + (f" @ {cfg.model.revision}" if cfg.model.revision else "")),
                (
                    "Method",
                    manifest.method
                    + (f" (rank {lora.rank}, alpha {lora.alpha}, dropout {lora.dropout})" if lora else ""),
                ),
                ("Training dataset", f"`{manifest.dataset_key}` — sha256 `{train.sha256[:12] if train else 'n/a'}`"),
                ("Examples / approx tokens", f"{train.examples:,} / {train.approx_tokens:,}" if train else "n/a"),
                ("Validation", f"sha256 `{manifest.validation.sha256[:12]}`" if manifest.validation else "none"),
                ("Config hash", f"`{manifest.config_hash[:12]}`"),
                (
                    "Code commit",
                    f"`{(manifest.git_commit or 'unknown')[:12]}`" + (" (dirty tree)" if manifest.git_dirty else ""),
                ),
                ("Seed", manifest.seed),
                ("Environment", ", ".join(f"{k}={v}" for k, v in manifest.environment.items() if v is not None)),
            ]
        ),
        "",
        "## Hyperparameters",
        "",
        *_kv(
            [
                ("Epochs / max steps", f"{t.epochs} / {t.max_steps if t.max_steps is not None else 'all'}"),
                ("Learning rate / scheduler / warmup", f"{t.learning_rate} / {t.lr_scheduler} / {t.warmup_ratio}"),
                ("Sequence length", t.max_sequence_length),
                ("Batch × accumulation", f"{t.per_device_batch_size} × {t.gradient_accumulation_steps}"),
                ("Precision", ("bf16" if t.bf16 else "fp32") + (", 4-bit NF4 base" if cfg.model.load_in_4bit else "")),
                ("Gradient checkpointing", t.gradient_checkpointing),
            ]
        ),
        "",
        "## Results",
        "",
        *_kv([("Steps completed", manifest.steps), ("Duration", f"{manifest.duration_s:.0f} s")]),
        *[f"| {k} | {v:.4f} |" for k, v in sorted(manifest.final_metrics.items())],
        "",
    ]
    if zarabench:
        lines += ["### ZaraBench", "", *_kv([(k, v) for k, v in zarabench.items()]), ""]
    else:
        lines += [
            "ZaraBench: not yet run. A checkpoint without a benchmark report is `experimental` and never promoted.",
            "",
        ]
    lines += [
        "## Intended use and limits",
        "",
        "- Serves the Zara agent platform through the Protea router for the task types where its ZaraBench category "
        "score clears the configured threshold; everything else stays on a frontier provider.",
        "- Trained only on sources classified SAFE in the data-risk assessment: PII redacted, secrets blocked, brand and "
        "infrastructure names scrubbed, golden families held out.",
        "- Base-model licence applies to the merged weights; the adapter inherits the training data's licence status "
        f"({'synthetic examples present' if train and train.synthetic else 'no synthetic examples'}).",
        "- Not a general assistant: no safety tuning beyond the estate's guardrails; must run behind the validation gate and the security pack.",
        "",
        "## Data lineage",
        "",
        "See the dataset card referenced by the registry entry for sources, commits, scan results and split counts.",
    ]
    return "\n".join(lines) + "\n"
