"""Provider comparison helper — same prompt against OpenAI and Gemini.

Usage (local, with both keys set):

  LLM_PROVIDER=openai python -c "..."  # normal path

  from backend.llm.compare import compare_complete
  print(compare_complete(prompt="Return JSON {\\"ok\\": true}", json_mode=True))
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Sequence

from backend.llm.base import ImageInput
from backend.llm.models import resolve_models


def compare_complete(
    *,
    prompt: str,
    json_mode: bool = False,
    role: str = "extract",
    images: Optional[Sequence[ImageInput]] = None,
    openai_api_key: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the same completion on both providers; return side-by-side results."""
    from backend.llm.openai_provider import OpenAIProvider
    from backend.llm.gemini_provider import GeminiProvider

    oai_models = resolve_models("openai")
    gem_models = resolve_models("gemini")
    oai = OpenAIProvider(api_key=openai_api_key or os.getenv("OPENAI_API_KEY"))
    gem = GeminiProvider(api_key=gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    text_parts = [prompt]
    out: Dict[str, Any] = {"role": role, "json_mode": json_mode}

    try:
        out["openai"] = {
            "model": oai_models[role],
            "text": oai.complete(
                model=oai_models[role],
                text_parts=text_parts,
                images=images,
                json_mode=json_mode,
                temperature=0.2,
            ),
            "error": None,
        }
    except Exception as exc:
        out["openai"] = {"model": oai_models[role], "text": "", "error": str(exc)}

    try:
        out["gemini"] = {
            "model": gem_models[role],
            "text": gem.complete(
                model=gem_models[role],
                text_parts=text_parts,
                images=images,
                json_mode=json_mode,
                temperature=0.2,
            ),
            "error": None,
        }
    except Exception as exc:
        out["gemini"] = {"model": gem_models[role], "text": "", "error": str(exc)}

    return out
