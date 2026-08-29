"""Fast structured case extraction — drives targeted RAG and focused audit."""

import json
import re
from typing import Any, List

import backend.config  # noqa: F401 — load .env before LLM client
from backend.llm_client import get_llm_provider, model_for


def stringify_item(item: Any) -> str:
    """Coerce LLM JSON values (sometimes dicts) into a single display string."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in (
            "name", "procedure", "event", "test", "finding", "description",
            "value", "text", "type", "imaging", "flag",
        ):
            if item.get(key):
                base = str(item[key]).strip()
                extras = [
                    f"{k}: {stringify_item(v)}"
                    for k, v in item.items()
                    if k != key and v is not None and stringify_item(v)
                ]
                return f"{base} ({'; '.join(extras)})" if extras else base
        parts = [f"{k}: {stringify_item(v)}" for k, v in item.items() if v is not None]
        return "; ".join(parts)
    if isinstance(item, list):
        return ", ".join(s for s in (stringify_item(x) for x in item) if s)
    return str(item).strip()


def normalize_str_list(items: Any) -> List[str]:
    """Ensure list fields are join-safe strings (LLM often returns dicts in arrays)."""
    if not items:
        return []
    if isinstance(items, str):
        return [items.strip()] if items.strip() else []
    if not isinstance(items, list):
        s = stringify_item(items)
        return [s] if s else []
    out: List[str] = []
    for item in items:
        s = stringify_item(item)
        if s:
            out.append(s)
    return out


def normalize_case_profile(profile: dict) -> dict:
    profile = dict(profile or {})
    for key in ("diagnosis", "age", "gender", "admission_type", "chief_complaint"):
        val = profile.get(key)
        profile[key] = stringify_item(val) if val is not None else ""
    for key in (
        "procedures", "key_labs", "imaging_mentioned",
        "billing_flags", "documentation_weaknesses",
    ):
        profile[key] = normalize_str_list(profile.get(key))
    if not isinstance(profile.get("timeline_events"), list):
        profile["timeline_events"] = []
    profile.setdefault("billing_flags", [])
    return profile


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
- procedures: list of plain strings (e.g. ["Appendectomy", "ICU admission"])
- imaging_mentioned: list of plain strings (e.g. ["Chest X-ray", "CT abdomen"])
- key_labs: list of plain strings (e.g. ["Hb 10.2 g/dL", "WBC 12000"])
- documentation_weaknesses: list of plain strings
- timeline_events: [{{"date":"","event":""}}] for admission, discharge, surgery, consultation
- billing_flags: room upgrade, excluded items, pre-auth mismatch hints from records
- documentation_weaknesses: missing consent, missing reports, illegible sections, gaps noted in records

CASE:
{excerpt}
"""

    provider = get_llm_provider()
    raw = provider.complete(
        model=model_for("extract"),
        text_parts=[prompt],
        json_mode=True,
    )

    profile = _parse_json(raw)
    profile.setdefault("diagnosis", "")
    profile.setdefault("age", "")
    profile.setdefault("gender", "")
    profile.setdefault("procedures", [])
    profile.setdefault("imaging_mentioned", [])
    profile.setdefault("timeline_events", [])
    profile.setdefault("documentation_weaknesses", [])
    return normalize_case_profile(profile)


def profile_to_audit_context(profile: dict, case_text: str, max_chars: int = 9000) -> str:
    """Compact case bundle for the main audit LLM call."""
    profile = normalize_case_profile(profile)
    proc = ", ".join(profile.get("procedures") or []) or "None documented"
    imaging = ", ".join(profile.get("imaging_mentioned") or []) or "None documented"
    labs = ", ".join(profile.get("key_labs") or []) or "None documented"
    weaknesses = ", ".join(profile.get("documentation_weaknesses") or []) or "None flagged yet"
    lines = [
        "=== STRUCTURED CASE PROFILE (extracted from documents) ===",
        f"Diagnosis: {profile.get('diagnosis') or 'Not clearly documented'}",
        f"Age: {profile.get('age') or 'Not stated'}",
        f"Gender: {profile.get('gender') or 'Not stated'}",
        f"Admission type: {profile.get('admission_type') or 'Not stated'}",
        f"Chief complaint: {profile.get('chief_complaint') or 'Not stated'}",
        f"Procedures: {proc}",
        f"Imaging mentioned: {imaging}",
        f"Key labs: {labs}",
        f"Documentation weaknesses spotted: {weaknesses}",
        "",
        "=== SOURCE DOCUMENT EXCERPT ===",
        case_text[: max_chars - 800],
    ]
    return "\n".join(lines)
