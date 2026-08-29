import os

import backend.config  # noqa: F401
from backend.llm_client import get_llm_provider, model_for
from backend.services.s3_utils import guidelines_cache


def _list_guidelines():
    try:
        return guidelines_cache.get()
    except Exception:
        pass
    if os.path.isdir("data/guidelines"):
        return [f for f in os.listdir("data/guidelines") if f.lower().endswith(".pdf")]
    return []


def select_guideline(case_text):

    guidelines = _list_guidelines()

    prompt = f"""
You are a medical expert.

Given the case, select the MOST RELEVANT guideline file.

Return ONLY the file name.

Available guidelines:
{guidelines}

CASE:
{case_text[:3000]}
"""

    text = get_llm_provider().complete(
        model=model_for("extract"),
        text_parts=[prompt],
    )
    return (text or "").strip()
