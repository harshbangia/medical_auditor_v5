"""OpenAI provider — preserves existing Responses / Chat / Embeddings behavior."""

from __future__ import annotations

import base64
from typing import List, Optional, Sequence

from openai import OpenAI

from backend.config import env
from backend.llm.base import ImageInput
from backend.llm.models import embedding_model


def _to_data_url(b64: str) -> str:
    b64 = (b64 or "").strip()
    if not b64:
        return ""
    if b64.startswith("data:"):
        return b64
    mime = "png" if b64.startswith("iVBOR") else "jpeg"
    return f"data:image/{mime};base64,{b64}"


def _extract_responses_text(response) -> str:
    if response is None:
        return ""
    if hasattr(response, "output_text") and response.output_text:
        return str(response.output_text).strip()
    chunks: List[str] = []
    output = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None) or (
            item.get("type") if isinstance(item, dict) else None
        )
        if item_type and item_type not in ("message", "output_text", None):
            continue
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not content:
            continue
        for part in content:
            ptype = getattr(part, "type", None) or (
                part.get("type") if isinstance(part, dict) else None
            )
            if ptype in ("output_text", "text", "input_text"):
                text = getattr(part, "text", None) or (
                    part.get("text") if isinstance(part, dict) else None
                )
                if text:
                    chunks.append(str(text))
    return "\n".join(chunks).strip()


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: Optional[str] = None) -> None:
        key = api_key or env("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env or switch LLM_PROVIDER=gemini."
            )
        self._client = OpenAI(api_key=key)

    def complete(
        self,
        *,
        model: str,
        text_parts: Sequence[str],
        images: Optional[Sequence[ImageInput]] = None,
        json_mode: bool = False,
        temperature: Optional[float] = None,
    ) -> str:
        content = []
        for t in text_parts:
            if t:
                content.append({"type": "input_text", "text": str(t)})
        for img in images or []:
            url = _to_data_url(img.b64)
            if not url:
                continue
            part = {"type": "input_image", "image_url": url}
            if img.detail:
                part["detail"] = img.detail
            content.append(part)
        if not content:
            return ""

        kwargs = {
            "model": model,
            "input": [{"role": "user", "content": content}],
        }
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}

        try:
            response = self._client.responses.create(**kwargs)
            text = _extract_responses_text(response)
            if text:
                return text
            if not images:
                # Fallback for text-only audit JSON (legacy path)
                return self._chat_fallback(
                    model=model,
                    prompt="\n\n".join(str(t) for t in text_parts if t),
                    json_mode=json_mode,
                    temperature=temperature,
                )
            return ""
        except Exception:
            if images:
                raise
            return self._chat_fallback(
                model=model,
                prompt="\n\n".join(str(t) for t in text_parts if t),
                json_mode=json_mode,
                temperature=temperature if temperature is not None else 0.2,
            )

    def _chat_fallback(
        self,
        *,
        model: str,
        prompt: str,
        json_mode: bool,
        temperature: Optional[float],
    ) -> str:
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = self._client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: Optional[str] = None,
    ) -> List[List[float]]:
        if not texts:
            return []
        model_id = model or embedding_model()
        # OpenAI accepts batch; keep single call
        response = self._client.embeddings.create(
            model=model_id,
            input=list(texts),
        )
        # Ensure order
        data = sorted(response.data, key=lambda d: d.index)
        return [list(d.embedding) for d in data]
