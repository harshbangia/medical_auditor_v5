"""Shared OpenAI client — lazy init after backend.config loads .env."""

from typing import Optional

from openai import OpenAI

from backend.config import env

_client: Optional[OpenAI] = None


def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = env("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env in the project root."
            )
        _client = OpenAI(api_key=api_key)
    return _client
