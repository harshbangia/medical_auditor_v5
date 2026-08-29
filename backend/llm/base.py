"""Provider-agnostic LLM interface for Glowix Auditor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, runtime_checkable


@dataclass
class ImageInput:
    """Base64 image (raw or data-URL) for multimodal calls."""

    b64: str
    detail: str = "low"  # OpenAI hint; Gemini may ignore


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal contract used by audit / vision / OCR / embeddings."""

    name: str

    def complete(
        self,
        *,
        model: str,
        text_parts: Sequence[str],
        images: Optional[Sequence[ImageInput]] = None,
        json_mode: bool = False,
        temperature: Optional[float] = None,
    ) -> str:
        """Return assistant text (JSON string when json_mode=True)."""
        ...

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        """Return one embedding vector per input text."""
        ...
