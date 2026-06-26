"""Extract claim dates and hospital from insurance letters and clinical documents.

Classifies each uploaded document (query letter, pre-auth, clinical note, discharge
summary, bills) and pulls the right date into the right field:
  - consultation_date  → OPD / consult note header dates
  - date_of_admission  → pre-auth admission line or query proposed hospitalization
  - date_of_discharge  → discharge summary only (never boilerplate)
"""

import re
from datetime import datetime
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
    r"consultation note|handwritten consult|clinical document|opd",
    re.I,
)
_DISCHARGE_MARKERS = re.compile(
    r"discharge summary|date of discharge|d\.o\.d",
    re.I,
)

_DATE_NUMERIC = r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})"
_DATE_TEXT = r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})"
_DATE_TEXT_ORDINAL = (
    r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})"
)

_CONSULT_PATTERNS = [
    re.compile(rf"date\s*of\s*first\s*consultation\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"first\s*consultation\s*(?:date|on)?\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"consultation\s*date\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"date\s*of\s*consultation\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"date\s*[:.]?\s*{_DATE_NUMERIC}\s*name\s*[:.]", re.I),
    re.compile(rf"body\s*:\s*date\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

_CLINICAL_CONSULT_BLOCK = re.compile(
    rf"consultation note.*?date\s*[:.]?\s*{_DATE_NUMERIC}",
    re.I | re.S,
)

_DISCHARGE_PATTERNS = [
    re.compile(rf"date\s*of\s*discharge\s*[:.]?\s*{_DATE_TEXT_ORDINAL}", re.I),
    re.compile(rf"date\s*of\s*discharge\s*[:.]?\s*{_DATE_TEXT}", re.I),
    re.compile(rf"date\s*of\s*discharge\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"(?:dod|d\.o\.d\.?)\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

_HOSPITAL_PATTERNS = [
    re.compile(r"(Kokilaben\s+Dhirubh?ai\s+Ambani\s+Hospital[^\n]{0,80})", re.I),
    re.compile(r"name\s*of\s*the\s+hospital\s*[:.]?\s*([^\n]{5,80})", re.I),
]

_PROPOSED_ADMISSION_PATTERNS = [
    re.compile(rf"proposed\s*date\s*of\s*hospitalization\s*{_DATE_TEXT}", re.I),
    re.compile(rf"proposed\s*date\s*of\s*hospitalization\s*{_DATE_NUMERIC}", re.I),
]

_EXPLICIT_ADMISSION_PATTERNS = [
    re.compile(rf"date\s*of\s*admission\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"date\s*of\s*admission\s*[:.]?\s*{_DATE_TEXT_ORDINAL}", re.I),
    re.compile(rf"date\s*of\s*hospitalization\s*[:.]?\s*{_DATE_TEXT}", re.I),
    re.compile(rf"date\s*of\s*hospitalization\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"(?:doa|d\.o\.a\.?)\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"admitted\s*(?:on|date)?\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

_ADMISSION_PATTERNS = _PROPOSED_ADMISSION_PATTERNS + _EXPLICIT_ADMISSION_PATTERNS

_ROOM_CATEGORY_PATTERNS = [
    re.compile(r"type\s*of\s*room\s*(?:required|requested)?\s*[:.]?\s*([^\n]{3,40})", re.I),
    re.compile(r"room\s*category\s*[:.]?\s*([^\n]{3,40})", re.I),
    re.compile(r"\b((?:single|twin|double)\s+deluxe)\b", re.I),
    re.compile(r"\b(prince\s+suite)\b", re.I),
]

_BAD_DATE_VALUES = {
    "", "-", "—", "na", "n/a", "not available", "unknown", "not provided", "not specified",
    "__/__/__", "not legible",
}
_UNKNOWN_ADMISSION = {"", "-", "—", "unknown", "not available", "not specified", "not provided"}
_BOILERPLATE_DISCHARGE = re.compile(
    r"discharge summary.{0,80}(?:will be sent|settled by us|as and when|signed by)",
    re.I | re.S,
)


def _norm_date(val: str) -> str:
    return " ".join(str(val or "").strip().split())


def _is_bad_date(val: str) -> bool:
    low = _norm_date(val).lower()
    if low in _BAD_DATE_VALUES or "*" in low:
        return True
    if re.search(r"_+|not\s+legible|illegible", low):
        return True
    return False


def _parse_date_year(val: str) -> Optional[int]:
    val = _norm_date(val)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(val, fmt).year
        except ValueError:
            continue
    m = re.search(r"(\d{4})", val)
    return int(m.group(1)) if m else None


def _claim_reference_year(text: str) -> Optional[int]:
    years: List[int] = []
    m = re.search(r"claim\s*incident\s*[:.]?\s*(20\d{2})", text or "", re.I)
    if m:
        years.append(int(m.group(1)))
    for pat in (
        r"proposed\s*date\s*of\s*hospitalization\s*\d{1,2}\s+\w+\s+(20\d{2})",
        r"query\s*letter\s*date\s*[:.]?\s*\d{1,2}\s+\w+\s+(20\d{2})",
        r"date\s*[:.]?\s*\d{1,2}\s+\w+\s+(20\d{2})",
    ):
        for match in re.finditer(pat, text or "", re.I):
            years.append(int(match.group(1)))
    return max(years) if years else None


def _is_plausible_for_claim(date_val: str, reference_year: Optional[int]) -> bool:
    year = _parse_date_year(date_val)
    if year is None or reference_year is None:
        return True
    return abs(year - reference_year) <= 2


def _classify_document(fname: str, text: str) -> str:
    name = (fname or "").lower()
    blob = text or ""
    if "query" in name or "querr" in name or _QUERY_LETTER_MARKERS.search(blob):
        return "query_letter"
    if "pre auth" in name or "preauth" in name or _PREAUTH_MARKERS.search(blob):
        return "pre_auth"
    if "discharge" in name or (
        _DISCHARGE_MARKERS.search(blob) and re.search(r"date\s*of\s*discharge\s*[:.]", blob, re.I)
    ):
        return "discharge"
    if "clinical" in name or "consult" in name or _CLINICAL_MARKERS.search(blob):
        return "clinical"
    if "bill" in name or "receipt" in name or "invoice" in name:
        return "bill"
    return "other"


def _first_match(patterns: List[re.Pattern], text: str) -> str:
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            val = _norm_date(m.group(1))
            if not _is_bad_date(val):
                return val
    return ""


def _clinical_consult_dates(text: str) -> List[str]:
    dates: List[str] = []
    if not _CLINICAL_MARKERS.search(text or "") and "consultation note" not in (text or "").lower():
        return dates
    for m in _CLINICAL_CONSULT_BLOCK.finditer(text or ""):
        val = _norm_date(m.group(1))
        if val and not _is_bad_date(val):
            dates.append(val)
    for pat in _CONSULT_PATTERNS:
        for m in pat.finditer(text or ""):
            val = _norm_date(m.group(1))
            if val and not _is_bad_date(val):
                dates.append(val)
    # de-dupe preserve order
    return list(dict.fromkeys(dates))


def _extract_discharge_date(text: str) -> str:
    if _BOILERPLATE_DISCHARGE.search(text or ""):
        snippet = text or ""
        if not re.search(r"date\s*of\s*discharge\s*[:.]\s*\d", snippet, re.I):
            return ""
    return _first_match(_DISCHARGE_PATTERNS, text)


def _infer_nature_of_admission(text: str, doc_type: str) -> str:
    blob = text or ""
    if re.search(r"\b(?:emergency|casualty|trauma|walk[\s-]?in)\b", blob, re.I):
        return "Emergency"
    if doc_type == "query_letter" or re.search(r"proposed\s*date\s*of\s*hospitalization", blob, re.I):
        return "Planned / Elective"
    if doc_type == "pre_auth" or re.search(r"pre[\s-]?auth|elective|planned\s*admission", blob, re.I):
        return "Planned / Elective"
    return ""


def _extract_room_category(text: str) -> str:
    for pat in _ROOM_CATEGORY_PATTERNS:
        m = pat.search(text or "")
        if m:
            val = re.sub(r"\s+", " ", m.group(1).strip()).strip(" .[]")
            if val and len(val) >= 3 and "hospital" not in val.lower():
                return val.title() if val.islower() else val
    return ""


def _extract_hospital(text: str) -> str:
    for pat in _HOSPITAL_PATTERNS:
        m = pat.search(text or "")
        if m:
            name = re.sub(r"\s+", " ", m.group(1).strip())
            low = name.lower()
            if len(name) >= 8 and "patient" not in low and "request for cashless" not in low:
                return name
    return ""


def _extract_admission_date(text: str, doc_type: str, reference_year: Optional[int]) -> str:
    if doc_type == "query_letter":
        patterns = _PROPOSED_ADMISSION_PATTERNS
    elif doc_type == "pre_auth":
        patterns = _EXPLICIT_ADMISSION_PATTERNS
    else:
        patterns = _ADMISSION_PATTERNS
    val = _first_match(patterns, text)
    if val and not _is_plausible_for_claim(val, reference_year):
        return ""
    return val


def _extract_facts_for_document(
    fname: str,
    text: str,
    reference_year: Optional[int],
) -> Dict[str, str]:
    doc_type = _classify_document(fname, text)
    clinical_dates = _clinical_consult_dates(text) if doc_type == "clinical" else []
    consult = clinical_dates[0] if clinical_dates else ""
    if not consult and doc_type == "pre_auth":
        consult = _first_match(_CONSULT_PATTERNS, text)

    admission = _extract_admission_date(text, doc_type, reference_year)

    discharge = _extract_discharge_date(text) if doc_type in ("discharge", "pre_auth", "clinical") else ""

    if consult and not _is_plausible_for_claim(consult, reference_year):
        consult = ""
    if discharge and not _is_plausible_for_claim(discharge, reference_year):
        discharge = ""

    return {
        "consultation_date": consult,
        "date_of_admission": admission,
        "date_of_discharge": discharge,
        "hospital": _extract_hospital(text),
        "nature_of_admission": _infer_nature_of_admission(text, doc_type),
        "room_category_eligible": _extract_room_category(text),
        "_doc_type": doc_type,
    }


def _field_priority(doc_type: str, field: str) -> int:
    table = {
        "consultation_date": {
            "clinical": 100,
            "pre_auth": 55,
            "query_letter": 40,
            "other": 30,
        },
        "date_of_admission": {
            "query_letter": 95,
            "pre_auth": 85,
            "bill": 70,
            "clinical": 50,
            "other": 30,
        },
        "date_of_discharge": {
            "discharge": 100,
            "pre_auth": 75,
            "clinical": 40,
            "other": 20,
        },
        "hospital": {
            "query_letter": 100,
            "pre_auth": 80,
            "clinical": 70,
            "bill": 60,
        },
        "nature_of_admission": {
            "query_letter": 100,
            "pre_auth": 80,
            "clinical": 20,
        },
        "room_category_eligible": {
            "pre_auth": 90,
            "policy": 85,
            "query_letter": 40,
        },
    }
    return table.get(field, {}).get(doc_type, 35)


def extract_claim_details_from_text(text: str, source: str = "") -> Dict[str, str]:
    """Regex extraction of claim dates and hospital from document text."""
    ref_year = _claim_reference_year(text)
    facts = _extract_facts_for_document(source or "combined", text, ref_year)
    facts.pop("_doc_type", None)
    return facts


def _pick_best_field(candidates: List[Tuple[str, int]]) -> str:
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


def _split_case_text_blocks(case_text: str) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    if not case_text:
        return blocks
    parts = re.split(r"=== Page \d+ — vision transcription \(([^)]+)\) ===", case_text)
    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            fname = parts[i].strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""
            blocks.append((fname, body))
    return blocks


def enrich_claim_facts(
    case_text: str,
    pdf_paths: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, str]:
    """Build best-effort claim details from combined case text and per-file sources."""
    reference_year = _claim_reference_year(case_text)
    per_source: List[Tuple[str, Dict[str, str], str]] = []

    combined = _extract_facts_for_document("combined", case_text, reference_year)
    per_source.append(("combined", combined, combined.get("_doc_type", "other")))

    for fname, body in _split_case_text_blocks(case_text):
        facts = _extract_facts_for_document(fname, body, reference_year)
        per_source.append((fname, facts, facts.get("_doc_type", "other")))

    if pdf_paths:
        try:
            import fitz
        except ImportError:
            fitz = None

        seen = {src for src, _, _ in per_source}
        for pdf_path, fname in pdf_paths:
            if fname in seen:
                continue
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
            facts = _extract_facts_for_document(fname, text, reference_year)
            per_source.append((fname, facts, facts.get("_doc_type", "other")))

    result = {
        "consultation_date": "",
        "date_of_admission": "",
        "date_of_discharge": "",
        "hospital": "",
        "nature_of_admission": "",
        "room_category_eligible": "",
    }
    for field in result:
        candidates = [
            (facts.get(field, ""), _field_priority(doc_type, field))
            for _src, facts, doc_type in per_source
        ]
        result[field] = _pick_best_field(candidates)

    if not result["nature_of_admission"] and result["date_of_admission"]:
        result["nature_of_admission"] = "Planned / Elective"

    return result


def _should_overwrite_claim_field(current: str, extracted: str) -> bool:
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

    if claim.get("date_of_admission") and not claim.get("date_of_discharge"):
        gaps = result.setdefault("documentation_gaps", [])
        gap_msg = (
            "Date of discharge not documented in uploaded files "
            "(discharge summary may not yet be available for a planned admission)."
        )
        if gap_msg not in gaps and not any("discharge" in str(g).lower() for g in gaps):
            gaps.append(gap_msg)

    return result
