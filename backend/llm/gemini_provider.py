"""Google Gemini provider via official google-genai SDK."""

from __future__ import annotations

import base64
from typing import Any, List, Optional, Sequence

from backend.config import env
from backend.llm.base import ImageInput
from backend.llm.models import embedding_model


def _raw_b64(b64: str) -> tuple[str, str]:
    """Return (mime, raw_base64) from raw or data-URL input."""
    b64 = (b64 or "").strip()
    if not b64:
        return "image/jpeg", ""
    if b64.startswith("data:"):
        # data:image/png;base64,....
        try:
            header, data = b64.split(",", 1)
            mime = "image/jpeg"
            if "image/png" in header:
                mime = "image/png"
            elif "image/webp" in header:
                mime = "image/webp"
            elif "image/gif" in header:
                mime = "image/gif"
            return mime, data
        except ValueError:
            return "image/jpeg", b64
    mime = "image/png" if b64.startswith("iVBOR") else "image/jpeg"
    return mime, b64


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is not installed. Run: pip install google-genai"
            ) from exc

        key = api_key or env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. "
                "Add it to .env or use LLM_PROVIDER=openai."
            )
        # Avoid indefinite hangs that block the single-flight audit lock.
        timeout_ms = int(env("GEMINI_HTTP_TIMEOUT_MS") or "300000")  # 5 min
        try:
            from google.genai import types as genai_types

            self._client = genai.Client(
                api_key=key,
                http_options=genai_types.HttpOptions(timeout=timeout_ms),
            )
        except Exception:
            self._client = genai.Client(api_key=key)

    def complete(
        self,
        *,
        model: str,
        text_parts: Sequence[str],
        images: Optional[Sequence[ImageInput]] = None,
        json_mode: bool = False,
        temperature: Optional[float] = None,
    ) -> str:
        contents: List[Any] = []
        for t in text_parts:
            if t:
                contents.append(str(t))
        if images:
            from google.genai import types

            for img in images:
                mime, raw = _raw_b64(img.b64)
                if not raw:
                    continue
                try:
                    data = base64.b64decode(raw)
                except Exception:
                    continue
                contents.append(
                    types.Part.from_bytes(data=data, mime_type=mime)
                )
        if not contents:
            return ""

        # Gemini 3.x ignores / may reject temperature, top_p, top_k.
        model_id = (model or "").strip()
        if model_id.lower().startswith("models/"):
            model_id = model_id[7:]

        # Dict config is supported (GenerateContentConfigOrDict).
        config: Optional[dict] = {}
        if json_mode:
            config["response_mime_type"] = "application/json"
        if temperature is not None and not model_id.lower().startswith("gemini-3"):
            config["temperature"] = temperature
        if not config:
            config = None

        response = self._client.models.generate_content(
            model=model_id,
            contents=contents,
            config=config,
        )
        text = getattr(response, "text", None)
        if text:
            return str(text).strip()
        # Fallback: concatenate any text parts
        chunks: List[str] = []
        cands = getattr(response, "candidates", None) or []
        for cand in cands:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                t = getattr(part, "text", None)
                if t:
                    chunks.append(str(t))
        return "\n".join(chunks).strip()

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        if not texts:
            return []
        from google.genai import types

        model_id = model or embedding_model()
        out: List[List[float]] = []
        # Embed one-by-one for broad SDK compatibility; batch if available
        for text in texts:
            result = self._client.models.embed_content(
                model=model_id,
                contents=text,
            )
            embeddings = getattr(result, "embeddings", None)
            if embeddings:
                values = getattr(embeddings[0], "values", None)
                if values is not None:
                    out.append([float(x) for x in values])
                    continue
            # Older shape: result.embedding.values
            emb = getattr(result, "embedding", None)
            values = getattr(emb, "values", None) if emb is not None else None
            if values is not None:
                out.append([float(x) for x in values])
                continue
            raise RuntimeError(f"Unexpected Gemini embedding response for model={model_id}")
        return out
