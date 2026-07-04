"""Grounded, confidence-scored guideline selection.

v6 change: instead of asking a small model to blurt one filename from raw
(often failed-OCR) text, we (1) extract a structured diagnosis, (2) rank the
available guidelines with an LLM that must justify and score each candidate,
(3) cross-check against filename specialty keywords, and (4) return a
confidence. Low confidence is surfaced to the caller so a human can confirm
rather than silently auditing against the wrong protocol.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

import backend.config  # noqa: F401
from backend.ai.llm_helpers import extract_response_text
from backend.llm_client import get_openai_client
from backend.services.s3_utils import guidelines_cache
from backend.utils.guideline_alignment import detect_specialties

_SELECTOR_MODEL = os.getenv("GUIDELINE_SELECTOR_MODEL", "gpt-4o-mini")


def _list_guidelines() -> List[str]:
    try:
        names = guidelines_cache.get()
        if names:
            return names
    except Exception:
        pass
    if os.path.isdir("data/guidelines"):
        return [f for f in os.listdir("data/guidelines") if f.lower().endswith(".pdf")]
    return []


def _parse_json(text: str) -> dict:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _keyword_specialty_overlap(diagnosis_hint: str, case_text: str, filename: str) -> bool:
    """Does the guideline filename's specialty appear in the case diagnosis/text?"""
    file_specs = detect_specialties(filename)
    if not file_specs:
        return False
    case_specs = detect_specialties(f"{diagnosis_hint} {case_text[:4000]}")
    return bool(file_specs & case_specs)


def select_guideline_ranked(
    case_text: str,
    diagnosis_hint: str = "",
    guidelines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return {best, confidence (0..1), candidates, diagnosis, specialty, source}.

    `confidence` blends the model's own score with a filename-specialty
    cross-check. Callers should treat < 0.6 as "ask the human to confirm".
    """
    guideline_files = guidelines or _list_guidelines()
    if not guideline_files:
        return {
            "best": "", "confidence": 0.0, "candidates": [],
            "diagnosis": diagnosis_hint, "specialty": "", "source": "auto",
            "reason": "No guidelines available in the catalogue.",
        }
    if len(guideline_files) == 1:
        return {
            "best": guideline_files[0], "confidence": 0.9,
            "candidates": [{"file": guideline_files[0], "score": 90}],
            "diagnosis": diagnosis_hint, "specialty": "", "source": "auto",
            "reason": "Only one guideline available.",
        }

    prompt = f"""You are a medical guideline-matching expert for an insurance audit.

First, state the patient's PRIMARY diagnosis and clinical specialty from the case.
Then rank the available guideline files by how well each fits THAT diagnosis.

Available guideline files:
{json.dumps(guideline_files, indent=2)}

Return ONLY JSON:
{{
  "diagnosis": "primary diagnosis in a few words",
  "specialty": "single clinical specialty, e.g. cardiology / orthopedics / hematology",
  "ranked": [
    {{"file": "<exact filename from the list>", "score": 0-100, "reason": "why it fits or not"}}
  ]
}}

Rules:
- Use ONLY filenames from the list above, spelled exactly.
- score = how well the guideline's specialty matches the case diagnosis (100 = perfect).
- If NO file is a good match, give the best one a LOW score (<50) — do not inflate.
- Base the diagnosis on explicit clinical content, not on the guideline names.

DIAGNOSIS HINT (may be empty): {diagnosis_hint or "none"}

CASE:
{case_text[:4000]}
"""
    try:
        response = get_openai_client().responses.create(
            model=_SELECTOR_MODEL, input=prompt
        )
        data = _parse_json(extract_response_text(response))
    except Exception as exc:  # pragma: no cover - network guard
        return {
            "best": guideline_files[0], "confidence": 0.3, "candidates": [],
            "diagnosis": diagnosis_hint, "specialty": "", "source": "auto",
            "reason": f"Selector call failed ({exc}); defaulted to first guideline.",
        }

    ranked = data.get("ranked") or []
    valid = [
        r for r in ranked
        if isinstance(r, dict) and r.get("file") in guideline_files
    ]
    valid.sort(key=lambda r: float(r.get("score") or 0), reverse=True)

    diagnosis = str(data.get("diagnosis") or diagnosis_hint or "").strip()
    specialty = str(data.get("specialty") or "").strip()

    if not valid:
        return {
            "best": guideline_files[0], "confidence": 0.3, "candidates": ranked,
            "diagnosis": diagnosis, "specialty": specialty, "source": "auto",
            "reason": "Model returned no valid ranking; defaulted to first guideline.",
        }

    top = valid[0]
    best = top["file"]
    model_score = float(top.get("score") or 0) / 100.0

    # Confidence penalties: weak lead over #2, and no filename-specialty overlap.
    second = float(valid[1].get("score") or 0) / 100.0 if len(valid) > 1 else 0.0
    margin = model_score - second
    confidence = model_score
    if margin < 0.15:
        confidence -= 0.15
    if not _keyword_specialty_overlap(diagnosis, case_text, best):
        confidence -= 0.20
    confidence = max(0.0, min(1.0, confidence))

    return {
        "best": best,
        "confidence": round(confidence, 2),
        "candidates": valid[:5],
        "diagnosis": diagnosis,
        "specialty": specialty,
        "source": "auto",
        "reason": top.get("reason", ""),
    }


def select_guideline(case_text: str) -> str:
    """Backward-compatible wrapper: returns just the best filename."""
    return select_guideline_ranked(case_text).get("best", "")
