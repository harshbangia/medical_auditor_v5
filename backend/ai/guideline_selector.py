import os

import backend.config  # noqa: F401
from backend.llm_client import get_openai_client
from backend.services.s3_utils import guidelines_cache


def _list_guidelines():
    try:
        return guidelines_cache.get()
    except Exception:
        pass
    if os.path.isdir("data/guidelines"):
        return [f for f in os.listdir("data/guidelines") if f.lower().endswith(".pdf")]
    return []

def extract_text(response):
    text = ""
    if hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text"):
                        text += c.text
    return text.strip()


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

    response = get_openai_client().responses.create(
        model="gpt-4o-mini",
        input=prompt
    )

    return extract_text(response)