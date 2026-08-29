"""Model / provider resolution from environment."""

from __future__ import annotations

from typing import Dict, Optional

from backend.config import env


def get_provider_name() -> str:
    raw = (env("LLM_PROVIDER") or "gemini").strip().lower()
    if raw in {"openai", "gpt", "oai"}:
        return "openai"
    if raw in {"gemini", "google", "genai"}:
        return "gemini"
    # Unknown → gemini (production target) but log once via caller
    return raw or "gemini"


def resolve_models(provider: str | None = None) -> Dict[str, str]:
    """Return model IDs for audit / vision / OCR / extract / embedding."""
    p = (provider or get_provider_name()).lower()
    if p == "openai":
        return {
            "audit": env("AUDIT_MODEL") or "gpt-4o",
            "vision": env("VISION_MODEL") or "gpt-4o-mini",
            "vision_ocr": env("VISION_OCR_MODEL") or "gpt-4o",
            "extract": env("EXTRACT_MODEL") or env("VISION_MODEL") or "gpt-4o-mini",
            "embedding": env("EMBEDDING_MODEL") or "text-embedding-3-small",
        }

    def _gemini(raw: Optional[str], default: str) -> str:
        # Ignore leftover OpenAI model names when provider=gemini
        if not raw or raw.lower().startswith("gpt-") or raw.lower().startswith("text-embedding-3"):
            return default
        return raw

    return {
        "audit": _gemini(env("AUDIT_MODEL"), "gemini-2.5-pro"),
        "vision": _gemini(env("VISION_MODEL"), "gemini-2.5-flash"),
        "vision_ocr": _gemini(env("VISION_OCR_MODEL"), "gemini-2.5-pro"),
        "extract": _gemini(
            env("EXTRACT_MODEL") or env("VISION_MODEL"),
            "gemini-2.5-flash",
        ),
        "embedding": _gemini(env("EMBEDDING_MODEL"), "gemini-embedding-001"),
    }


def model_for(role: str) -> str:
    models = resolve_models()
    if role not in models:
        raise KeyError(f"Unknown model role: {role}")
    return models[role]


def extract_model() -> str:
    return model_for("extract")


def embedding_model() -> str:
    return model_for("embedding")
