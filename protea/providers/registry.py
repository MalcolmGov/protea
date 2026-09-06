"""Build providers by name from settings. Consumers (Zara, Gaslite, …) call build_provider and nothing else."""

from __future__ import annotations

from typing import Any

from protea.config.settings import ProteaSettings, get_settings
from protea.providers.base import ModelProvider, ProviderNotConfigured, UsageSink

PROVIDER_NAMES = ("mock", "anthropic", "openai", "google", "azure_openai", "ollama", "protea", "openai_compatible")


def provider_status(settings: ProteaSettings | None = None) -> dict[str, dict[str, Any]]:
    s = settings or get_settings()
    return {
        "mock": {"configured": True, "model": "mock-1", "needs": "-"},
        "anthropic": {
            "configured": bool(s.anthropic_api_key),
            "model": s.anthropic_model,
            "needs": "ANTHROPIC_API_KEY",
        },
        "openai": {"configured": bool(s.openai_api_key), "model": s.openai_model, "needs": "OPENAI_API_KEY"},
        "google": {"configured": bool(s.google_api_key), "model": s.google_model, "needs": "GOOGLE_API_KEY"},
        "azure_openai": {
            "configured": bool(s.azure_openai_endpoint and s.azure_openai_api_key and s.azure_openai_deployment),
            "model": s.azure_openai_deployment or "-",
            "needs": "AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT",
        },
        "ollama": {"configured": bool(s.ollama_base_url), "model": s.ollama_model, "needs": "OLLAMA_BASE_URL"},
        "protea": {
            "configured": bool(s.protea_inference_url),
            "model": s.protea_inference_model,
            "needs": "PROTEA_INFERENCE_URL",
        },
    }


def build_provider(
    name: str,
    *,
    model: str | None = None,
    settings: ProteaSettings | None = None,
    usage_sink: UsageSink | None = None,
    **overrides: Any,
) -> ModelProvider:
    s = settings or get_settings()
    kw: dict[str, Any] = {"usage_sink": usage_sink}
    if name == "mock":
        from protea.providers.mock import MockProvider

        return MockProvider(model=model or "mock-1", **kw)
    if name == "anthropic":
        from protea.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=model or s.anthropic_model,
            api_key=s.anthropic_api_key,
            timeout_s=s.request_timeout_s,
            **kw,
            **overrides,
        )
    if name == "openai":
        from protea.providers.openai_compatible import OpenAICompatibleProvider

        if not s.openai_api_key:
            raise ProviderNotConfigured("openai", "OPENAI_API_KEY")
        return OpenAICompatibleProvider(
            model=model or s.openai_model,
            base_url=overrides.pop("base_url", s.openai_base_url),
            api_key=s.openai_api_key,
            name="openai",
            timeout_s=s.request_timeout_s,
            **kw,
            **overrides,
        )
    if name == "google":
        from protea.providers.google import GoogleProvider

        if not s.google_api_key:
            raise ProviderNotConfigured("google", "GOOGLE_API_KEY")
        return GoogleProvider(
            model=model or s.google_model, api_key=s.google_api_key, timeout_s=s.request_timeout_s, **kw, **overrides
        )
    if name == "azure_openai":
        from protea.providers.openai_compatible import AzureOpenAIProvider

        if not (s.azure_openai_endpoint and s.azure_openai_api_key and (model or s.azure_openai_deployment)):
            raise ProviderNotConfigured(
                "azure_openai", "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / AZURE_OPENAI_DEPLOYMENT"
            )
        return AzureOpenAIProvider(
            deployment=model or s.azure_openai_deployment,
            endpoint=s.azure_openai_endpoint,
            api_key=s.azure_openai_api_key,
            api_version=s.azure_openai_api_version,
            timeout_s=s.request_timeout_s,
            **kw,
            **overrides,
        )  # type: ignore[arg-type]
    if name == "ollama":
        from protea.providers.openai_compatible import OpenAICompatibleProvider

        if not s.ollama_base_url:
            raise ProviderNotConfigured("ollama", "OLLAMA_BASE_URL")
        return OpenAICompatibleProvider(
            model=model or s.ollama_model,
            base_url=s.ollama_base_url,
            api_key=None,
            name="ollama",
            timeout_s=s.request_timeout_s,
            **kw,
            **overrides,
        )
    if name == "protea":
        from protea.providers.openai_compatible import OpenAICompatibleProvider

        if not s.protea_inference_url:
            raise ProviderNotConfigured("protea", "PROTEA_INFERENCE_URL")
        return OpenAICompatibleProvider(
            model=model or s.protea_inference_model,
            base_url=s.protea_inference_url,
            api_key=s.protea_inference_token,
            name="protea",
            timeout_s=s.request_timeout_s,
            **kw,
            **overrides,
        )
    if name == "openai_compatible":
        from protea.providers.openai_compatible import OpenAICompatibleProvider

        base_url = overrides.pop("base_url", None)
        if not base_url:
            raise ProviderNotConfigured("openai_compatible", "base_url")
        return OpenAICompatibleProvider(
            model=model or "default",
            base_url=base_url,
            api_key=overrides.pop("api_key", None),
            timeout_s=s.request_timeout_s,
            **kw,
            **overrides,
        )
    raise ValueError(f"unknown provider {name!r}; known: {', '.join(PROVIDER_NAMES)}")
