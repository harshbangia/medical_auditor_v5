"""Extract claim dates and hospital from insurance letters and clinical documents.

The audit LLM often leaves date_of_admission blank or picks an old consult date
from an unrelated handwritten page. This module deterministically extracts:
  - consultation_date (first consultation / OPD visit)
  - date_of_admission (proposed hospitalization / DOA)
  - date_of_discharge (DOD)
  - hospital name from query / pre-auth letters
  - nature_of_admission (Planned/Elective when pre-auth filed in advance)
"""

import re
from typing import Dict, List, Optional, Tuple

_QUERY_LETTER_MARKERS = re.compile(
    r"query letter|claim incident|cashless request|proposed date of hospitalization",
    re.I,
)
_PREAUTH_MARKERS = re.compile(
    r"pre[\s-]?auth|cashless hospitalization|request for cashless",
    re.I,
)
_CLINICAL_MARKERS = re.compile(
    r"consultation note|handwritten consult|clinical document",
    re.I,
)

_DATE_NUMERIC = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})"
_DATE_TEXT = r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})"

_CONSULT_PATTERNS = [
    re.compile(
        rf"date\s*of\s*first\s*consultation\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"first\s*consultation\s*(?:date|on)?\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"consultation\s*date\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"date\s*of\s*consultation\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"date\s*[:.]?\s*{_DATE_NUMERIC}\s*name\s*[:.]",
        re.I,
    ),
    re.compile(
        rf"body\s*:\s*date\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
]

_ADMISSION_PATTERNS = [
    re.compile(
        rf"proposed\s*date\s*of\s*hospitalization\s*{_DATE_TEXT}",
        re.I,
    ),
    re.compile(
        rf"proposed\s*date\s*of\s*hospitalization\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"date\s*of\s*(?:admission|hospitalization)\s*[:.]?\s*{_DATE_TEXT}",
        re.I,
    ),
    re.compile(
        rf"date\s*of\s*(?:admission|hospitalization)\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"(?:doa|d\.o\.a\.?)\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"admitted\s*(?:on|date)?\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
]

_DISCHARGE_PATTERNS = [
    re.compile(
        rf"date\s*of\s*discharge\s*[:.]?\s*{_DATE_TEXT}",
        re.I,
    ),
    re.compile(
        rf"date\s*of\s*discharge\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
    re.compile(
        rf"(?:dod|d\.o\.d\.?)\s*[:.]?\s*{_DATE_NUMERIC}",
        re.I,
    ),
]

_HOSPITAL_PATTERNS = [
    re.compile(
        r"(Kokilaben\s+Dhirubh?ai\s+Ambani\s+Hospital[^\n]{0,80})",
        re.I,
    ),
    re.compile(
        r"((?:[A-Z][A-Za-z&\s]{3,40}Hospital)(?:\s+(?:&|and)\s+Medical\s+Research\s+Institute)?)",
        re.I,
    ),
    re.compile(
        r"name\s*of\s*the\s*hospital\s*[:.]?\s*([^\n]{5,80})",
        re.I,
    ),
]

_ROOM_CATEGORY_PATTERNS = [
    re.compile(r"type\s*of\s*room\s*(?:required|requested)?\s*[:.]?\s*([^\n]{3,40})", re.I),
    re.compile(r"room\s*category\s*[:.]?\s*([^\n]{3,40})", re.I),
    re.compile(r"\b((?:single|twin|double)\s+deluxe)\b", re.I),
    re.compile(r"\b(prince\s+suite)\b", re.I),
]

_BAD_DATE_VALUES = {
    "", "-", "—", "na", "n/a", "not available", "unknown", "not provided", "not specified",
}
_UNKNOWN_ADMISSION = {"", "-", "—", "unknown", "not available", "not specified", "not provided"}


def _norm_date(val: str) -> str:
    return " ".join(str(val or "").strip().split())


def _is_bad_date(val: str) -> bool:
    return _norm_date(val).lower() in _BAD_DATE_VALUES or "*" in _norm_date(val)


def _first_match(patterns: List[re.Pattern], text: str) -> str:
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            return _norm_date(m.group(1))
    return ""


def _all_consult_dates(text: str) -> List[str]:
    dates: List[str] = []
    for pat in _CONSULT_PATTERNS:
        for m in pat.finditer(text or ""):
            val = _norm_date(m.group(1))
            if val and not _is_bad_date(val):
                dates.append(val)
    return dates


def _infer_nature_of_admission(text: str) -> str:
    if re.search(
        r"\b(?:emergency|er\b|casualty|trauma|walk[\s-]?in|acute)\b",
        text or "",
        re.I,
    ):
        return "Emergency"
    if re.search(r"proposed\s*date\s*of\s*hospitalization", text or "", re.I):
        return "Planned / Elective"
    if re.search(r"pre[\s-]?auth|cashless\s*request|elective|planned\s*admission", text or "", re.I):
        return "Planned / Elective"
    return ""


def _extract_room_category(text: str) -> str:
    for pat in _ROOM_CATEGORY_PATTERNS:
        m = pat.search(text or "")
        if m:
            val = re.sub(r"\s+", " ", m.group(1).strip()).strip(" .[]")
            if val and len(val) >= 3:
                return val.title() if val.islower() else val
    return ""


def _source_priority(filename: str, text: str, field: str = "") -> int:
    name = (filename or "").lower()
    if field == "consultation_date" and ("clinical" in name or _CLINICAL_MARKERS.search(text or "")):
        return 95
    if field == "date_of_admission" and (
        "query" in name or "querr" in name or _QUERY_LETTER_MARKERS.search(text or "")
    ):
        return 100
    if _QUERY_LETTER_MARKERS.search(text or "") or "query" in name or "querr" in name:
        return 100
    if _PREAUTH_MARKERS.search(text or "") or "pre auth" in name or "preauth" in name:
        return 80
    if "clinical" in name or "consult" in name:
        return 70
    if "discharge" in name:
        return 75
    return 40


def extract_claim_details_from_text(text: str) -> Dict[str, str]:
    """Regex extraction of claim dates and hospital from document text."""
    consult_dates = _all_consult_dates(text)
    return {
        "consultation_date": consult_dates[0] if consult_dates else "",
        "date_of_admission": _first_match(_ADMISSION_PATTERNS, text),
        "date_of_discharge": _first_match(_DISCHARGE_PATTERNS, text),
        "hospital": _extract_hospital(text),
        "nature_of_admission": _infer_nature_of_admission(text),
        "room_category_eligible": _extract_room_category(text),
    }


def _extract_hospital(text: str) -> str:
    for pat in _HOSPITAL_PATTERNS:
        m = pat.search(text or "")
        if m:
            name = re.sub(r"\s+", " ", m.group(1).strip())
            if len(name) >= 8 and "patient" not in name.lower():
                return name
    return ""


def _pick_best_field(candidates: List[Tuple[str, int]]) -> str:
    """Pick highest-priority non-empty value."""
    ranked = sorted(
        [(v, p) for v, p in candidates if v and not _is_bad_date(v)],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[0][0] if ranked else ""


def _slice_case_text_for_file(case_text: str, fname: str) -> str:
    if not case_text or not fname:
        return ""
    markers = [f"({fname})", f"— vision transcription ({fname})", fname]
    for marker in markers:
        if marker in case_text:
            start = case_text.find(marker)
            return case_text[max(0, start - 200): start + 12000]
    return ""


def enrich_claim_facts(
    case_text: str,
    pdf_paths: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, str]:
    """
    Build best-effort claim details from combined case text and per-file sources.

    Query letters are preferred for admission dates; clinical notes for consultation.
    """
    per_source: List[Tuple[str, Dict[str, str], int]] = []

    combined = extract_claim_details_from_text(case_text)
    per_source.append(("combined", combined, 50))

    if pdf_paths:
        try:
            import fitz
        except ImportError:
            fitz = None

        for pdf_path, fname in pdf_paths:
            text = ""
            if fitz:
                try:
                    doc = fitz.open(pdf_path)
                    text = "".join(p.get_text() for p in doc)
                    doc.close()
                except Exception:
                    text = ""
            if not text.strip():
                text = _slice_case_text_for_file(case_text, fname)
            facts = extract_claim_details_from_text(text)
            per_source.append((fname, facts, _source_priority(fname, text)))

    result = {
        "consultation_date": "",
        "date_of_admission": "",
        "date_of_discharge": "",
        "hospital": "",
        "nature_of_admission": "",
        "room_category_eligible": "",
    }
    for field in result:
        candidates = []
        for src, facts, priority in per_source:
            field_priority = priority
            if field == "consultation_date" and "clinical" in (src or "").lower():
                field_priority = max(priority, 95)
            if field == "date_of_admission" and (
                "query" in (src or "").lower() or "querr" in (src or "").lower()
            ):
                field_priority = max(priority, 100)
            if field == "room_category_eligible" and (
                "pre auth" in (src or "").lower() or "policy" in (src or "").lower()
            ):
                field_priority = max(priority, 85)
            candidates.append((facts.get(field, ""), field_priority))
        result[field] = _pick_best_field(candidates)
    if not result["consultation_date"]:
        clinical_dates = []
        for src, facts, priority in per_source:
            if "clinical" in (src or "").lower() or priority >= 70:
                clinical_dates.extend(_all_consult_dates(_slice_case_text_for_file(case_text, src)))
        if clinical_dates:
            result["consultation_date"] = clinical_dates[0]
    if not result["nature_of_admission"] and result["date_of_admission"]:
        result["nature_of_admission"] = "Planned / Elective"
    return result


def _should_overwrite_claim_field(current: str, extracted: str) -> bool:
    """Prefer deterministic extraction over LLM-filled claim dates."""
    return bool(extracted and not _is_bad_date(extracted))


def _should_overwrite_admission_nature(current: str, extracted: str) -> bool:
    if not extracted:
        return False
    cur = str(current or "").strip().lower()
    return not cur or cur in _UNKNOWN_ADMISSION


def merge_claim_details_into_result(result: dict, facts: Dict[str, str]) -> dict:
    """Fill or correct claim_details from extracted facts."""
    claim = result.setdefault("claim_details", {})
    mapping = {
        "consultation_date": facts.get("consultation_date", ""),
        "date_of_admission": facts.get("date_of_admission", ""),
        "date_of_discharge": facts.get("date_of_discharge", ""),
        "hospital": facts.get("hospital", ""),
        "nature_of_admission": facts.get("nature_of_admission", ""),
    }
    for key, val in mapping.items():
        if not val:
            continue
        if key == "nature_of_admission":
            if _should_overwrite_admission_nature(str(claim.get(key) or ""), val):
                claim[key] = val
        elif _should_overwrite_claim_field(str(claim.get(key) or ""), val):
            claim[key] = val

    room = facts.get("room_category_eligible", "")
    if room:
        tba = result.setdefault("treatment_billing_audit", {})
        current = str(tba.get("room_category_eligible") or "").strip()
        if not current or current.lower() in _UNKNOWN_ADMISSION:
            tba["room_category_eligible"] = room
    return result
