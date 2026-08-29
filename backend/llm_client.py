"""Shared LLM client — provider factory (Gemini default, OpenAI rollback)."""

from __future__ import annotations

from typing import Optional

from backend.llm.base import ImageInput, LLMProvider
from backend.llm.models import get_provider_name, model_for, resolve_models

_provider: Optional[LLMProvider] = None
_provider_name: Optional[str] = None


def reset_llm_provider() -> None:
    """Clear cached provider (tests / provider switch)."""
    global _provider, _provider_name
    _provider = None
    _provider_name = None


def get_llm_provider() -> LLMProvider:
    """Return the configured LLM provider (cached)."""
    global _provider, _provider_name
    name = get_provider_name()
    if _provider is not None and _provider_name == name:
        return _provider

    if name == "openai":
        from backend.llm.openai_provider import OpenAIProvider
        _provider = OpenAIProvider()
    elif name == "gemini":
        from backend.llm.gemini_provider import GeminiProvider
        _provider = GeminiProvider()
    else:
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER={name!r}. Use 'gemini' or 'openai'."
        )
    _provider_name = name
    print(f"🔌 LLM provider: {name} | models={resolve_models(name)}")
    return _provider


def get_openai_client():
    """Deprecated compatibility shim — prefer get_llm_provider().

    Only works when LLM_PROVIDER=openai. Kept so older imports fail loudly
    under Gemini instead of silently using the wrong SDK.
    """
    if get_provider_name() != "openai":
        raise RuntimeError(
            "get_openai_client() is deprecated and only available when "
            "LLM_PROVIDER=openai. Use backend.llm_client.get_llm_provider()."
        )
    from backend.llm.openai_provider import OpenAIProvider
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)
    return provider._client


# Re-exports for call sites
__all__ = [
    "ImageInput",
    "get_llm_provider",
    "get_openai_client",
    "model_for",
    "reset_llm_provider",
    "resolve_models",
]
