"""Model / provider resolution from environment."""

from __future__ import annotations

from typing import Dict, Optional

from backend.config import env

# Defaults for new Gemini API keys (2.5 family is blocked for new users).
GEMINI_AUDIT_DEFAULT = "gemini-3.1-pro-preview"
GEMINI_FLASH_DEFAULT = "gemini-3.6-flash"
GEMINI_EMBEDDING_DEFAULT = "gemini-embedding-001"

# Retired / unavailable → current replacements (also remaps stale .env values).
_GEMINI_MODEL_ALIASES = {
    "gemini-2.5-flash": GEMINI_FLASH_DEFAULT,
    "gemini-2.5-flash-lite": GEMINI_FLASH_DEFAULT,
    "gemini-2.5-pro": GEMINI_AUDIT_DEFAULT,
    "gemini-2.0-flash": GEMINI_FLASH_DEFAULT,
    "gemini-2.0-flash-001": GEMINI_FLASH_DEFAULT,
    "gemini-1.5-flash": GEMINI_FLASH_DEFAULT,
    "gemini-1.5-pro": GEMINI_AUDIT_DEFAULT,
    "gemini-pro": GEMINI_AUDIT_DEFAULT,
    "gemini-flash-latest": GEMINI_FLASH_DEFAULT,
}


def get_provider_name() -> str:
    raw = (env("LLM_PROVIDER") or "gemini").strip().lower()
    if raw in {"openai", "gpt", "oai"}:
        return "openai"
    if raw in {"gemini", "google", "genai"}:
        return "gemini"
    # Unknown → gemini (production target) but log once via caller
    return raw or "gemini"


def normalize_gemini_model(raw: Optional[str], default: str) -> str:
    """Resolve a Gemini model id; ignore OpenAI leftovers; remap retired ids."""
    if not raw:
        return default
    name = raw.strip()
    if name.lower().startswith("models/"):
        name = name[7:]
    lower = name.lower()
    if lower.startswith("gpt-") or lower.startswith("text-embedding-3"):
        return default
    return _GEMINI_MODEL_ALIASES.get(lower, name)


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

    return {
        "audit": normalize_gemini_model(env("AUDIT_MODEL"), GEMINI_AUDIT_DEFAULT),
        "vision": normalize_gemini_model(env("VISION_MODEL"), GEMINI_FLASH_DEFAULT),
        "vision_ocr": normalize_gemini_model(
            env("VISION_OCR_MODEL"), GEMINI_FLASH_DEFAULT
        ),
        "extract": normalize_gemini_model(
            env("EXTRACT_MODEL") or env("VISION_MODEL"),
            GEMINI_FLASH_DEFAULT,
        ),
        "embedding": normalize_gemini_model(
            env("EMBEDDING_MODEL"), GEMINI_EMBEDDING_DEFAULT
        ),
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
