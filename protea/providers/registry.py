"""Build providers by name from settings. Consumers (Zara, Gaslite, …) call build_provider and nothing else."""

from __future__ import annotations

from typing import Any

from protea.config.settings import ProteaSettings, get_settings
from protea.providers.base import ModelProvider, ProviderNotConfigured, UsageSink

PROVIDER_NAMES = (
    "mock",
    "anthropic",
    "openai",
    "google",
    "azure_openai",
    "ollama",
    "protea",
    "local",
    "openai_compatible",
)


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
        "local": {
            "configured": True,
            "model": s.local_model + (f" + {s.local_adapter}" if s.local_adapter else ""),
            "needs": "transformers/torch installed (PROTEA_LOCAL_MODEL, PROTEA_LOCAL_ADAPTER)",
        },
    }


def _build_mock(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    from protea.providers.mock import MockProvider

    return MockProvider(model=model or "mock-1", **kw)


def _build_anthropic(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    from protea.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(
        model=model or s.anthropic_model, api_key=s.anthropic_api_key, timeout_s=s.request_timeout_s, **kw, **ov
    )


def _build_openai_compatible(
    name: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    s: ProteaSettings,
    kw: dict[str, Any],
    ov: dict[str, Any],
) -> ModelProvider:
    from protea.providers.openai_compatible import OpenAICompatibleProvider

    if not base_url:
        raise ProviderNotConfigured(name, _NEEDS[name])
    return OpenAICompatibleProvider(
        model=model, base_url=base_url, api_key=api_key, name=name, timeout_s=s.request_timeout_s, **kw, **ov
    )


def _build_openai(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    if not s.openai_api_key:
        raise ProviderNotConfigured("openai", _NEEDS["openai"])
    base_url = ov.pop("base_url", s.openai_base_url)
    return _build_openai_compatible("openai", model or s.openai_model, base_url, s.openai_api_key, s, kw, ov)


def _build_ollama(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    return _build_openai_compatible("ollama", model or s.ollama_model, s.ollama_base_url, None, s, kw, ov)


def _build_protea(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    return _build_openai_compatible(
        "protea", model or s.protea_inference_model, s.protea_inference_url, s.protea_inference_token, s, kw, ov
    )


def _build_local(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    from protea.providers.local_hf import LocalHFProvider

    return LocalHFProvider(
        model or s.local_model,
        ov.pop("adapter", s.local_adapter),
        served_as=ov.pop("served_as", s.local_served_as),
        threads=ov.pop("threads", s.local_threads),
        **kw,
        **ov,
    )


def _build_generic(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    return _build_openai_compatible(
        "openai_compatible", model or "default", ov.pop("base_url", None), ov.pop("api_key", None), s, kw, ov
    )


def _build_google(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    from protea.providers.google import GoogleProvider

    if not s.google_api_key:
        raise ProviderNotConfigured("google", _NEEDS["google"])
    return GoogleProvider(
        model=model or s.google_model, api_key=s.google_api_key, timeout_s=s.request_timeout_s, **kw, **ov
    )


def _build_azure(s: ProteaSettings, model: str | None, kw: dict[str, Any], ov: dict[str, Any]) -> ModelProvider:
    from protea.providers.openai_compatible import AzureOpenAIProvider

    deployment = model or s.azure_openai_deployment
    if not (s.azure_openai_endpoint and s.azure_openai_api_key and deployment):
        raise ProviderNotConfigured("azure_openai", _NEEDS["azure_openai"])
    return AzureOpenAIProvider(
        deployment=deployment,
        endpoint=s.azure_openai_endpoint,
        api_key=s.azure_openai_api_key,
        api_version=s.azure_openai_api_version,
        timeout_s=s.request_timeout_s,
        **kw,
        **ov,
    )


_NEEDS = {
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "azure_openai": "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY / AZURE_OPENAI_DEPLOYMENT",
    "ollama": "OLLAMA_BASE_URL",
    "protea": "PROTEA_INFERENCE_URL",
    "openai_compatible": "base_url",
}

_BUILDERS = {
    "mock": _build_mock,
    "anthropic": _build_anthropic,
    "openai": _build_openai,
    "google": _build_google,
    "azure_openai": _build_azure,
    "ollama": _build_ollama,
    "protea": _build_protea,
    "local": _build_local,
    "openai_compatible": _build_generic,
}


def build_provider(
    name: str,
    *,
    model: str | None = None,
    settings: ProteaSettings | None = None,
    usage_sink: UsageSink | None = None,
    **overrides: Any,
) -> ModelProvider:
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"unknown provider {name!r}; known: {', '.join(PROVIDER_NAMES)}")
    return builder(settings or get_settings(), model, {"usage_sink": usage_sink}, dict(overrides))
