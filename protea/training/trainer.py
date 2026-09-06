"""TRL/PEFT trainer driven entirely by `TrainingConfig` (spec §16–§18).

torch / transformers / trl / peft are imported lazily so the base package stays CPU-light; `tiny-random` builds a
two-layer random model and a tokenizer from the training text so the whole path runs offline in CI.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from protea.config.models import TrainingConfig
from protea.training.data import render_chatml
from protea.training.run import MetricsLog, RunDir, RunManifest

CHATML_TEMPLATE = (
    "{% for message in messages %}{{ '<|im_start|>' + message['role'] + '\\n' + message['content'] + '<|im_end|>\\n' }}"
    "{% endfor %}{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}{% endif %}"
)
TINY_RANDOM = "tiny-random"
UNK_TOKEN = "<unk>"


class TrainingUnavailable(RuntimeError):
    pass


class TrainOptions(BaseModel):
    resume: bool = False
    max_steps: int | None = None
    mlflow_uri: str | None = None


def _require_stack() -> None:
    try:
        import peft  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
        import trl  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise TrainingUnavailable(
            f"training stack missing ({exc.name}); install with `pip install -e '.[train-cpu]'` "
            "(plus torch from the CPU or CUDA index) or `.[train]` on a GPU host"
        ) from exc


def build_tiny_random(records: list[dict[str, Any]], vocab_size: int = 2048):
    """A 2-layer random causal LM plus a BPE tokenizer trained on the records: no download, seconds on a CPU."""
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

    specials = ["<|endoftext|>", "<|im_start|>", "<|im_end|>", UNK_TOKEN]
    tok = Tokenizer(models.BPE(unk_token=UNK_TOKEN))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.train_from_iterator(
        (render_chatml(r) for r in records), trainers.BpeTrainer(vocab_size=vocab_size, special_tokens=specials)
    )
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=tok, eos_token="<|im_end|>", pad_token="<|endoftext|>", unk_token=UNK_TOKEN
    )
    tokenizer.chat_template = CHATML_TEMPLATE
    config = Qwen2Config(
        vocab_size=len(tokenizer),
        hidden_size=64,
        intermediate_size=128,
        num_hidden_layers=2,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=2048,
        tie_word_embeddings=True,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.pad_token_id,
    )
    return Qwen2ForCausalLM(config), tokenizer


def load_model_and_tokenizer(cfg: TrainingConfig, records: list[dict[str, Any]]):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if cfg.model.base_model == TINY_RANDOM:
        return build_tiny_random(records)
    kwargs: dict[str, Any] = {
        "trust_remote_code": cfg.model.trust_remote_code,
        "revision": cfg.model.revision,
        "dtype": torch.bfloat16 if cfg.training.bf16 and torch.cuda.is_available() else torch.float32,
    }
    if cfg.model.load_in_4bit:
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(cfg.model.base_model, **kwargs)
    tokenizer = AutoTokenizer.from_pretrained(
        cfg.model.tokenizer or cfg.model.base_model,
        trust_remote_code=cfg.model.trust_remote_code,
        revision=cfg.model.revision,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    if not tokenizer.chat_template:
        tokenizer.chat_template = CHATML_TEMPLATE
    return model, tokenizer


def peft_config(cfg: TrainingConfig):
    if cfg.training.method not in ("lora", "qlora") or cfg.lora is None:
        return None
    from peft import LoraConfig

    return LoraConfig(
        r=cfg.lora.rank,
        lora_alpha=cfg.lora.alpha,
        lora_dropout=cfg.lora.dropout,
        target_modules=cfg.lora.target_modules,
        task_type="CAUSAL_LM",
    )


def _supported(config_cls, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields the installed TRL/transformers accept; map warmup_ratio to warmup_steps (a fraction) if needed."""
    fields = set(config_cls.__dataclass_fields__)
    if "warmup_ratio" not in fields and "warmup_ratio" in kwargs:
        kwargs["warmup_steps"] = kwargs.pop("warmup_ratio")  # transformers>=5: a float in [0, 1) is a fraction of steps
    return {k: v for k, v in kwargs.items() if k in fields}


def sft_config(cfg: TrainingConfig, run: RunDir, *, max_steps: int | None = None):
    import torch
    from trl import SFTConfig

    t = cfg.training
    steps = max_steps if max_steps is not None else t.max_steps
    cuda = torch.cuda.is_available()
    kwargs: dict[str, Any] = {
        "output_dir": str(run.checkpoints),
        "num_train_epochs": t.epochs,
        "max_steps": steps if steps is not None else -1,
        "learning_rate": t.learning_rate,
        "max_length": t.max_sequence_length,
        "per_device_train_batch_size": t.per_device_batch_size,
        "per_device_eval_batch_size": t.per_device_batch_size,
        "gradient_accumulation_steps": t.gradient_accumulation_steps,
        "gradient_checkpointing": t.gradient_checkpointing,
        "warmup_ratio": t.warmup_ratio,
        "lr_scheduler_type": t.lr_scheduler,
        "seed": t.seed,
        "data_seed": t.seed,
        "bf16": t.bf16 and cuda,
        "logging_steps": t.logging_steps,
        "save_steps": t.save_steps,
        "save_total_limit": t.save_total_limit,
        "eval_strategy": "steps",
        "eval_steps": t.eval_steps,
        "save_strategy": "steps",
        "packing": t.packing,
        "assistant_only_loss": t.assistant_only_loss,
        "report_to": [],
        "dataset_num_proc": 1,
        "use_cpu": not cuda,
    }
    return SFTConfig(**_supported(SFTConfig, kwargs))


def _metrics_callback(log: MetricsLog):
    from transformers import TrainerCallback

    class _Callback(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                log.log(state.global_step, {k: float(v) for k, v in logs.items() if isinstance(v, int | float)})

    return _Callback()


def train(
    cfg: TrainingConfig,
    run: RunDir,
    manifest: RunManifest,
    train_records: list[dict[str, Any]],
    eval_records: list[dict[str, Any]],
    options: TrainOptions | None = None,
) -> RunManifest:
    """Fine-tune, checkpoint, save the adapter, and record everything in the manifest."""
    _require_stack()
    from datasets import Dataset
    from trl import SFTTrainer

    opts = options or TrainOptions()
    started = time.perf_counter()
    manifest.status = "running"
    run.save(manifest)
    log = MetricsLog(run, manifest, opts.mlflow_uri)
    try:
        model, tokenizer = load_model_and_tokenizer(cfg, train_records)
        trainer = SFTTrainer(
            model=model,
            args=sft_config(cfg, run, max_steps=opts.max_steps),
            train_dataset=Dataset.from_list(train_records),
            eval_dataset=Dataset.from_list(eval_records) if eval_records else None,
            processing_class=tokenizer,
            peft_config=peft_config(cfg),
            callbacks=[_metrics_callback(log)],
        )
        checkpoint = run.latest_checkpoint() if opts.resume else None
        manifest.resumed_from = str(checkpoint) if checkpoint else None
        result = trainer.train(resume_from_checkpoint=str(checkpoint) if checkpoint else None)
        trainer.save_model(str(run.adapter))
        tokenizer.save_pretrained(str(run.adapter))
        manifest.steps = int(trainer.state.global_step)
        manifest.final_metrics = {k: float(v) for k, v in result.metrics.items() if isinstance(v, int | float)}
        eval_metrics = trainer.evaluate() if eval_records else {}
        manifest.final_metrics.update({k: float(v) for k, v in eval_metrics.items() if isinstance(v, int | float)})
        manifest.artifacts = {"adapter": str(run.adapter), "checkpoints": str(run.checkpoints)}
        manifest.status = "completed"
    except Exception:
        manifest.status = "failed"
        raise
    finally:
        manifest.duration_s = round(time.perf_counter() - started, 1)
        run.save(manifest)
        log.close()
    return manifest


def adapter_sha256(adapter_dir: Path) -> str | None:
    """Hash of the adapter weights file if present (safetensors preferred)."""
    from protea.training.data import sha256_file

    for name in ("adapter_model.safetensors", "model.safetensors", "adapter_model.bin"):
        p = adapter_dir / name
        if p.exists():
            return sha256_file(p)
    return None
