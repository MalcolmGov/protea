"""YAML configuration schemas for models, training, inference and evaluation (spec §7, §16, §23, §26)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ServingSpec(BaseModel):
    dtype: str = "bfloat16"
    quantization: Literal["none", "awq", "fp8", "gptq", "bitsandbytes"] = "none"
    max_model_len: int = 8192
    tool_call_parser: str | None = None  # vLLM parser name, e.g. "hermes"
    min_vram_gb: float | None = None


class ModelConfig(BaseModel):
    """A base-model candidate."""

    id: str
    hf_repo: str
    family: str
    license: str
    params_b: float
    context_length: int
    gated: bool = False
    languages: list[str] = Field(default_factory=list)
    supports_tools: bool = True
    chat_template: str = "native"
    serving: ServingSpec = Field(default_factory=ServingSpec)
    notes: str = ""


class LoraSpec(BaseModel):
    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    target_modules: list[str] = Field(
        default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    )


class TrainingSection(BaseModel):
    method: Literal["sft", "lora", "qlora", "dpo"]
    epochs: float = 3
    learning_rate: float = 1e-4
    max_sequence_length: int = 4096
    per_device_batch_size: int = 2
    gradient_accumulation_steps: int = 8
    gradient_checkpointing: bool = True
    warmup_ratio: float = 0.03
    lr_scheduler: str = "cosine"
    seed: int = 42
    bf16: bool = True
    max_steps: int | None = None  # for dry runs / tiny models
    save_steps: int = 200
    eval_steps: int = 200
    logging_steps: int = 10


class DatasetSection(BaseModel):
    train: str
    validation: str
    dataset_key: str  # registry key, e.g. agent-training-0.1.0
    max_examples: int | None = None


class BudgetSection(BaseModel):
    max_runtime_minutes: int = 240
    max_cost_usd: float = 50.0
    idle_shutdown_minutes: int = 15
    checkpoint_before_shutdown: bool = True


class ModelSection(BaseModel):
    base_model: str
    trust_remote_code: bool = False
    load_in_4bit: bool = False


class OutputSection(BaseModel):
    dir: str = "checkpoints"
    experiment_name: str
    model_family: str = "protea-agent"
    tags: dict[str, str] = Field(default_factory=dict)


class TrainingConfig(BaseModel):
    model: ModelSection
    training: TrainingSection
    lora: LoraSpec | None = None
    dataset: DatasetSection
    output: OutputSection
    budget: BudgetSection = Field(default_factory=BudgetSection)

    @model_validator(mode="after")
    def _method_rules(self) -> TrainingConfig:
        if self.training.method in ("lora", "qlora") and self.lora is None:
            raise ValueError(f"training.method={self.training.method} requires a lora section")
        if self.training.method == "qlora" and not self.model.load_in_4bit:
            raise ValueError("qlora requires model.load_in_4bit: true")
        if self.dataset.train == self.dataset.validation:
            raise ValueError("train and validation must be different files")
        if "golden" in self.dataset.train or "golden" in self.dataset.validation:
            raise ValueError("golden sets are never training or validation inputs")
        return self


class InferenceConfig(BaseModel):
    engine: Literal["vllm"] = "vllm"
    model: str  # HF repo or local path
    adapter: str | None = None
    served_model_name: str = "protea-agent"
    quantization: str = "none"
    dtype: str = "bfloat16"
    max_model_len: int = 8192
    gpu_memory_utilization: float = 0.9
    tool_call_parser: str | None = None
    guided_decoding_backend: str = "xgrammar"
    port: int = 8000
    require_token: bool = True


class CategoryWeight(BaseModel):
    name: str
    weight: float
    min_score: float = 0.0  # release gate threshold for this category


class EvaluationConfig(BaseModel):
    suite: str
    version: str
    categories: list[CategoryWeight]
    judge_model: str | None = None
    judge_must_differ_from_generator: bool = True
    release_min_zarascore: float = 0.0
    latency_budget_ms: int | None = None

    @model_validator(mode="after")
    def _weights(self) -> EvaluationConfig:
        total = sum(c.weight for c in self.categories)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"category weights must sum to 1.0 (got {total:.4f})")
        return self

    def zarascore(self, scores: dict[str, float]) -> float:
        return sum(c.weight * scores.get(c.name, 0.0) for c in self.categories)

    def failed_gates(self, scores: dict[str, float]) -> list[str]:
        failed = [c.name for c in self.categories if scores.get(c.name, 0.0) < c.min_score]
        if self.zarascore(scores) < self.release_min_zarascore:
            failed.append("zarascore")
        return failed


CONFIG_TYPES: dict[str, type[BaseModel]] = {
    "model": ModelConfig,
    "training": TrainingConfig,
    "inference": InferenceConfig,
    "evaluation": EvaluationConfig,
}


def as_dict(cfg: BaseModel) -> dict[str, Any]:
    return cfg.model_dump(mode="json")
