"""Render the vLLM OpenAI-compatible server command from `InferenceConfig` (spec §28). Used by the container
entrypoint and `protea serve vllm --print`; running it needs a GPU and is an execution boundary."""

from __future__ import annotations

import shlex

from protea.config.models import InferenceConfig


def vllm_args(
    cfg: InferenceConfig, *, adapter_path: str | None = None, token_env: str = "PROTEA_INFERENCE_TOKEN"
) -> list[str]:
    args = [
        "vllm",
        "serve",
        cfg.model,
        "--served-model-name",
        cfg.served_model_name,
        "--dtype",
        cfg.dtype,
        "--max-model-len",
        str(cfg.max_model_len),
        "--gpu-memory-utilization",
        str(cfg.gpu_memory_utilization),
        "--guided-decoding-backend",
        cfg.guided_decoding_backend,
        "--port",
        str(cfg.port),
        "--host",
        "0.0.0.0",  # noqa: S104 — container bind; the facade or an ingress fronts it
    ]
    if cfg.quantization and cfg.quantization != "none":
        args += ["--quantization", cfg.quantization]
    if cfg.tool_call_parser:
        args += ["--enable-auto-tool-choice", "--tool-call-parser", cfg.tool_call_parser]
    adapter = adapter_path or cfg.adapter
    if adapter:
        args += ["--enable-lora", "--lora-modules", f"{cfg.served_model_name}={adapter}"]
    if cfg.require_token:
        args += ["--api-key", f"${token_env}"]
    return args


def vllm_command(cfg: InferenceConfig, **kw: str | None) -> str:
    parts = vllm_args(cfg, **kw)  # type: ignore[arg-type]
    return " ".join(p if p.startswith("$") else shlex.quote(p) for p in parts)
