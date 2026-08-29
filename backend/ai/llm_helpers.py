"""Helpers retained for legacy OpenAI Responses shapes.

New code should use ``backend.llm_client.get_llm_provider().complete(...)``.
These helpers remain for any residual OpenAI-specific tooling.
"""

import base64
from typing import Any


def to_image_data_url(b64: str) -> str:
    """Responses API requires input_image.image_url as a data URI, not raw base64."""
    b64 = (b64 or "").strip()
    if not b64:
        return ""
    if b64.startswith("data:"):
        return b64
    mime = "png" if b64.startswith("iVBOR") else "jpeg"
    return f"data:image/{mime};base64,{b64}"


def image_input_part(b64: str, detail: str = "low") -> dict:
    url = to_image_data_url(b64)
    part: dict = {"type": "input_image", "image_url": url}
    if detail:
        part["detail"] = detail
    return part


def extract_response_text(response: Any) -> str:
    """Extract assistant text from a Responses API result."""
    if response is None:
        return ""

    if hasattr(response, "output_text") and response.output_text:
        return str(response.output_text).strip()

    chunks = []
    output = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type and item_type not in ("message", "output_text", None):
            continue
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if not content:
            continue
        for part in content:
            ptype = getattr(part, "type", None) or (part.get("type") if isinstance(part, dict) else None)
            if ptype in ("output_text", "text", "input_text"):
                text = getattr(part, "text", None) or (part.get("text") if isinstance(part, dict) else None)
                if text:
                    chunks.append(str(text))
    return "\n".join(chunks).strip()
