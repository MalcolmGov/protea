"""Environment-backed settings. Secrets are read from the environment only, never from YAML."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProteaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Cloud sandboxes reserve ANTHROPIC_API_KEY for the session's own account; the PROTEA_ prefix is the alias
    # operators set there (docs/inference.md, docs/zarabench.md).
    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "PROTEA_ANTHROPIC_API_KEY")
    )
    anthropic_model: str = "claude-opus-5"
    openai_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "PROTEA_OPENAI_API_KEY")
    )
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    google_api_key: str | None = None
    google_model: str = "gemini-2.5-flash"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    ollama_base_url: str | None = None  # e.g. http://localhost:11434/v1
    ollama_model: str = "qwen3:8b"
    protea_inference_url: str | None = None  # vLLM OpenAI-compatible base, e.g. http://gpu:8000/v1
    protea_inference_token: str | None = None
    protea_inference_model: str = "protea-agent"
    protea_facade_token: str | None = None  # bearer token the facade requires from callers
    local_model: str = Field(default="Qwen/Qwen2.5-0.5B-Instruct", validation_alias=AliasChoices("PROTEA_LOCAL_MODEL"))
    local_adapter: str | None = Field(default=None, validation_alias=AliasChoices("PROTEA_LOCAL_ADAPTER"))
    local_served_as: str | None = Field(default=None, validation_alias=AliasChoices("PROTEA_LOCAL_SERVED_AS"))
    local_threads: int | None = Field(default=None, validation_alias=AliasChoices("PROTEA_LOCAL_THREADS"))
    hf_token: str | None = None
    runpod_api_key: str | None = None
    mlflow_tracking_uri: str | None = None
    registry_dir: str = Field(
        default="registry",
        validation_alias=AliasChoices("PROTEA_REGISTRY_DIR", "REGISTRY_DIR"),
        description="Where datasets.json / models.json live",
    )
    request_timeout_s: float = Field(
        default=60.0, validation_alias=AliasChoices("PROTEA_REQUEST_TIMEOUT_S", "REQUEST_TIMEOUT_S")
    )


@lru_cache(maxsize=1)
def get_settings() -> ProteaSettings:
    return ProteaSettings()
