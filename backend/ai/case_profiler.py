"""Fast structured case extraction — drives targeted RAG and focused audit."""

import json
import os
import re

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _parse_json(text: str) -> dict:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def extract_case_profile(case_text: str) -> dict:
    """
    Lightweight pass over case documents before the main audit.
    Returns structured facts used for guideline retrieval and challenge framing.
    """
    excerpt = case_text[:14000]
    prompt = f"""Extract structured facts from these clinical case documents for an insurance medical audit.

Return ONLY JSON:
{{
  "diagnosis": "",
  "age": "",
  "gender": "",
  "procedures": [],
  "admission_type": "",
  "chief_complaint": "",
  "key_labs": [],
  "imaging_mentioned": [],
  "timeline_events": [],
  "billing_flags": [],
  "documentation_weaknesses": []
}}

Rules:
- Extract ONLY what is explicitly stated; use "" or [] if unknown.
- procedures: surgeries, interventions, biopsies, admissions
- imaging_mentioned: X-ray, CT, MRI, ultrasound, ECG, photos, histopath, etc.
- timeline_events: [{{"date":"","event":""}}] for admission, discharge, surgery, consultation
- billing_flags: room upgrade, excluded items, pre-auth mismatch hints from records
- documentation_weaknesses: missing consent, missing reports, illegible sections, gaps noted in records

CASE:
{excerpt}
"""

    response = client.responses.create(model="gpt-4o-mini", input=prompt)
    raw = ""
    if hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text"):
                        raw += c.text

    profile = _parse_json(raw)
    profile.setdefault("diagnosis", "")
    profile.setdefault("age", "")
    profile.setdefault("gender", "")
    profile.setdefault("procedures", [])
    profile.setdefault("imaging_mentioned", [])
    profile.setdefault("timeline_events", [])
    profile.setdefault("documentation_weaknesses", [])
    return profile


def profile_to_audit_context(profile: dict, case_text: str, max_chars: int = 9000) -> str:
    """Compact case bundle for the main audit LLM call."""
    lines = [
        "=== STRUCTURED CASE PROFILE (extracted from documents) ===",
        f"Diagnosis: {profile.get('diagnosis') or 'Not clearly documented'}",
        f"Age: {profile.get('age') or 'Not stated'}",
        f"Gender: {profile.get('gender') or 'Not stated'}",
        f"Admission type: {profile.get('admission_type') or 'Not stated'}",
        f"Chief complaint: {profile.get('chief_complaint') or 'Not stated'}",
        f"Procedures: {', '.join(profile.get('procedures') or []) or 'None documented'}",
        f"Imaging mentioned: {', '.join(profile.get('imaging_mentioned') or []) or 'None documented'}",
        f"Key labs: {', '.join(profile.get('key_labs') or []) or 'None documented'}",
        f"Documentation weaknesses spotted: {', '.join(profile.get('documentation_weaknesses') or []) or 'None flagged yet'}",
        "",
        "=== SOURCE DOCUMENT EXCERPT ===",
        case_text[: max_chars - 800],
    ]
    return "\n".join(lines)
