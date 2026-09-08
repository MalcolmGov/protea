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
    save_total_limit: int = 3
    packing: bool = False
    assistant_only_loss: bool = False  # needs a chat template with {% generation %} markers


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
    base_model: str  # Hub id, local path, or "tiny-random" (a 2-layer random model for offline smoke runs)
    revision: str | None = None
    tokenizer: str | None = None  # defaults to base_model
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


class TokenPrice(BaseModel):
    """USD per million tokens; used only to estimate benchmark spend before and after a run."""

    input: float
    output: float


class EvaluationConfig(BaseModel):
    suite: str
    version: str
    categories: list[CategoryWeight]
    tasks_path: str | None = None  # JSONL of EvalTask, relative to the repository root
    lock_path: str | None = None  # golden lock (sha256 + held-out families) written by `evaluate seal`
    judge_provider: str | None = None
    judge_model: str | None = None
    judge_must_differ_from_generator: bool = True
    release_min_zarascore: float = 0.0
    latency_budget_ms: int | None = None
    priority_categories: list[str] = Field(
        default_factory=lambda: ["agent_generation", "structured_output", "tool_calling", "connector_selection"]
    )
    frontier_gate_categories: list[str] = Field(default_factory=lambda: ["structured_output"])
    kill_fraction_of_frontier: float = 0.8  # strategy-review A1: stop training below this share of the frontier score
    concurrency: int = 4
    max_tool_rounds: int = 3
    max_tokens: int = 800
    temperature: float = 0.0
    prices: dict[str, TokenPrice] = Field(default_factory=dict)  # model id (or prefix) -> price

    def price_for(self, model: str) -> TokenPrice | None:
        for key in sorted(self.prices, key=len, reverse=True):
            if model.startswith(key):
                return self.prices[key]
        return None

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


class StorageSpec(BaseModel):
    """Where datasets, checkpoints and adapters live — never only on the instance's disk."""

    kind: Literal["s3", "azure_blob", "pvc", "rsync"]
    uri: str  # s3://bucket/prefix, https://account.blob.core.windows.net/container, pvc name, or user@host:/path
    endpoint: str | None = None  # S3-compatible endpoint for non-AWS stores (Cloudflare R2, MinIO); None => AWS S3


class SshSection(BaseModel):
    host: str
    user: str = "ubuntu"
    port: int = 22
    workdir: str = "/opt/protea"
    key_path: str | None = None
    shutdown_when_done: bool = True


class RunPodSection(BaseModel):
    cloud_type: Literal["SECURE", "COMMUNITY"] = "SECURE"
    volume_gb: int = 100
    container_disk_gb: int = 50
    template_id: str | None = None


class AzureSection(BaseModel):
    resource_group: str
    location: str = "swedencentral"
    vm_size: str = "Standard_NC24ads_A100_v4"
    admin_user: str = "protea"
    ssh_public_key_path: str = "~/.ssh/id_ed25519.pub"


class KubernetesSection(BaseModel):
    namespace: str = "protea"
    pvc: str = "protea-data"
    node_selector: dict[str, str] = Field(default_factory=dict)
    service_account: str | None = None


class RemoteJobConfig(BaseModel):
    provider: Literal["ssh", "runpod", "azure", "kubernetes"]
    gpu: str  # key in configs/remote/gpu-pricing.yaml
    gpu_count: int = 1
    image: str = "ghcr.io/malcolmgov/protea-train:latest"
    spot: bool = False
    storage: StorageSpec
    max_runtime_minutes: int | None = None  # defaults to the training budget
    idle_shutdown_minutes: int | None = None
    checkpoint_sync_minutes: int = 10
    ssh: SshSection | None = None
    runpod: RunPodSection | None = None
    azure: AzureSection | None = None
    kubernetes: KubernetesSection | None = None

    @model_validator(mode="after")
    def _provider_section(self) -> RemoteJobConfig:
        if getattr(self, self.provider) is None:
            raise ValueError(f"provider={self.provider} requires a `{self.provider}` section")
        if self.gpu_count < 1:
            raise ValueError("gpu_count must be at least 1")
        return self


class GpuPrice(BaseModel):
    vram_gb: int
    usd_per_hour: dict[str, float]  # provider -> on-demand USD/hour ("generic" is the fallback)
    spot_discount: float = 0.5
    tokens_per_second: dict[str, float]  # size bucket (small | 8b | 14b | large) -> tokens/s per GPU

    @model_validator(mode="after")
    def _buckets(self) -> GpuPrice:
        missing = {"small", "8b", "14b", "large"} - set(self.tokens_per_second)
        if missing:
            raise ValueError(f"tokens_per_second missing buckets {sorted(missing)}")
        return self


class GpuCatalogue(BaseModel):
    """configs/pricing/gpu.yaml — indicative prices and throughputs for pre-flight estimates only."""

    gpus: dict[str, GpuPrice]


CONFIG_TYPES: dict[str, type[BaseModel]] = {
    "model": ModelConfig,
    "training": TrainingConfig,
    "inference": InferenceConfig,
    "evaluation": EvaluationConfig,
    "remote": RemoteJobConfig,
    "pricing": GpuCatalogue,
}


def _serve_config():
    from protea.serving.config import ServeConfig

    return ServeConfig


def _routing_policy():
    from protea.router.policy import RoutingPolicy

    return RoutingPolicy


def _release_config():
    from protea.release.config import ReleaseConfig

    return ReleaseConfig


def _economics_config():
    from protea.economics import EconomicsConfig

    return EconomicsConfig


CONFIG_TYPES["serve"] = _serve_config()
CONFIG_TYPES["routing"] = _routing_policy()
CONFIG_TYPES["release"] = _release_config()
CONFIG_TYPES["economics"] = _economics_config()


def as_dict(cfg: BaseModel) -> dict[str, Any]:
    return cfg.model_dump(mode="json")
