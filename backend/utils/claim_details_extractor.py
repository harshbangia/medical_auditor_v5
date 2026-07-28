"""Extract claim dates with document-type awareness, source attribution, and discrepancy flags.

Handwritten pre-authorization forms and clinical consult notes take priority over
computer-generated query letters. Proposed hospitalization dates from query letters
are recorded separately and never used as the actual admission date when a pre-auth
or clinical source provides one.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_QUERY_LETTER_MARKERS = re.compile(
    r"query\s*letter|claim\s*incident|member\s*code|proposed\s*date\s*of\s*hospitalization",
    re.I,
)
_PREAUTH_MARKERS = re.compile(
    r"pre[\s-]?auth(?:orization)?|request\s+for\s+cashless",
    re.I,
)
_INDOOR_CASE_MARKERS = re.compile(
    r"treatment\s+sheet|indoor\s+case(?:\s+papers?)?|\bicps\b|"
    r"ward\s*/\s*bed\s+no|icu[\s-]?\d",
    re.I,
)
_LAB_REPORT_MARKERS = re.compile(
    r"department\s+of\s+(?:biochemistry|haematology|immunology|pathology)|"
    r"receiving\s+date|reporting\s+date|end\s+of\s+report",
    re.I,
)
_RADIOLOGY_MARKERS = re.compile(
    r"ct\s+scan|hrct|echocardiography|consultant\s+radiologist|impression\s*:-",
    re.I,
)
_CLINICAL_MARKERS = re.compile(
    r"consultation\s+note|handwritten\s+consult|clinical\s+document|opd|prescription",
    re.I,
)
_DISCHARGE_MARKERS = re.compile(
    r"discharge\s+summary|date\s+of\s+discharge|d\.o\.d",
    re.I,
)

# Day/month with optional year — handles handwritten forms like 30/6 or 30/6/26
_DATE_NUMERIC = r"(\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?)"
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

# Treatment-sheet timestamps — valid for admission inference, not first consultation.
_INDOOR_SHEET_TIMESTAMP_PATTERNS = [
    re.compile(rf"date\s*&\s*time\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

_LAB_RECEIVING_DATE_PATTERNS = [
    re.compile(rf"receiving\s*date\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"reporting\s*date\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

_CARE_DOCUMENT_TYPES = frozenset({"pre_auth", "indoor_case", "clinical", "discharge"})

_TREATMENT_SHEET_DATE_PATTERNS = _INDOOR_SHEET_TIMESTAMP_PATTERNS + [
    re.compile(rf"^date\s*[:.]?\s*{_DATE_NUMERIC}", re.I | re.M),
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
    re.compile(r"(Gangapada\s+Super\s+Speciality\s+Hospital[^\n]{0,60})", re.I),
    re.compile(r"(Nibedita\s+Health\s+Care[^\n]{0,60})", re.I),
    re.compile(r"(Gokuldas\s+Hospital(?:\s+Pvt\.?\s*Ltd\.?)?[^\n]{0,40})", re.I),
    re.compile(r"(Charak\s+Hospital[^\n]{0,40})", re.I),
    re.compile(
        r"((?:L\.?\s*N\.?\s*)?Medical\s+College\s*(?:&|and)\s*J\.?\s*K\.?\s*Hospital[^\n]{0,40})",
        re.I,
    ),
    # IFFCO-style query letter: Member Code : H1509554-1-1 <Hospital Name>
    re.compile(
        r"member\s*(?:code|id|no|number)?\s*[:.]?\s*[A-Z0-9][A-Z0-9\-/]{4,}\s+"
        r"([A-Za-z0-9][A-Za-z0-9.&'()/\- ]{4,90}?"
        r"(?:Hospital|Clinic|Nursing\s+Home|Medical\s+Centre|Medical\s+Center|"
        r"Institute|Healthcare|Health\s+Care)[A-Za-z0-9.&'()/\- ]{0,40})",
        re.I,
    ),
    re.compile(
        r"(?:hospital\s*name|name\s*of\s*(?:the\s+)?hospital|treating\s+hospital|"
        r"provider\s*name|name\s*of\s*provider|hospital\s*/\s*nursing\s*home)\s*[:.]?\s*"
        r"([^\n]{5,100})",
        re.I,
    ),
    re.compile(r"name\s*of\s*the\s+hospital\s*[:.]?\s*([^\n]{5,80})", re.I),
    # Proper hospital names — require at least one real word before Hospital (not address crumbs)
    re.compile(
        r"\b((?:[A-Z][A-Za-z.&'()-]{2,}(?:\s+[A-Z][A-Za-z.&'()-]{1,}){0,6})\s+"
        r"(?:Hospital|Clinic|Nursing\s+Home|Medical\s+(?:Centre|Center|Institute)|Institute)"
        r"(?:\s+(?:&|and)\s+[A-Za-z.&'() \-]{3,60})?)\b",
    ),
]

_HOSPITAL_BOILERPLATE = re.compile(
    r"arising\s+out\s+of|unless\s+arising|hospitalization\s+for|"
    r"subject\s+to|excluding|policy\s+wording|insured\s+person|"
    r"definition|period\s+of\s+insurance|cashless\s+facility|"
    r"pre[\s-]?existing|waiting\s+period|sum\s+insured|"
    r"certified\s+hospital|accredited\s+hospital|iso\s*9001|"
    r"\bnabh\b|\bnabl\b|mci\s+approved",
    re.I,
)

# Address / location crumbs that OCR often mistakes for hospital names.
_HOSPITAL_ADDRESS_PREFIXES = re.compile(
    r"^(?:near|opp\.?|opposite|behind|adjacent|beside|next\s+to|plot|sector|road|street|"
    r"village|dist\.?|district|tehsil|taluka|pin|pincode|address|location|"
    r"[A-Z]\.|[A-Z]\s*\)|[0-9]+[./\-])",
    re.I,
)
_HOSPITAL_ADDRESS_TOKENS = {
    "near", "opp", "opposite", "behind", "adjacent", "beside", "plot", "sector",
    "road", "street", "village", "dist", "district", "tehsil", "taluka", "pin",
    "pincode", "address", "location", "civil", "bus", "stand", "market", "chowk",
}

_TOTAL_BILL_PATTERNS = [
    re.compile(
        r"(?:sum\s*total\s*(?:expected\s*)?(?:cost|amount)|"
        r"total\s*(?:hospital\s+)?(?:bill|amount|charges?)|grand\s*total|"
        r"net\s*(?:amount|payable|bill)|amount\s*(?:claimed|payable|due))\s*[:.]?\s*"
        r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        re.I,
    ),
    re.compile(
        r"(?:estimated\s+(?:cost|amount|expense)|total\s+(?:cost|amount)\s+(?:of\s+)?"
        r"(?:hospitalization|treatment|package)|package\s+(?:cost|amount|charges?))"
        r"\s*[:.]?\s*(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
        re.I,
    ),
]

_MIN_HOSPITAL_BILL_INR = 5000

_QUERY_LETTER_HEADER_DATE = re.compile(
    r"query\s*letter.*?date\s*[:.]?\s*(\d{1,2}\s+\w+\s+\d{4})",
    re.I | re.S,
)

_ICU_IMAGING_DATE_PATTERNS = [
    re.compile(
        rf"date\s*[:.]?\s*{_DATE_NUMERIC}.{{0,200}}?bed\s*no\s*[:.]?\s*icu",
        re.I | re.S,
    ),
]

_PROPOSED_HOSPITALIZATION_PATTERNS = [
    re.compile(rf"proposed\s*date\s*of\s*hospitalization\s*{_DATE_TEXT}", re.I),
    re.compile(rf"proposed\s*date\s*of\s*hospitalization\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"proposed\s*date\s*of\s*hospitalization\s*[:.]?\s*{_DATE_TEXT}", re.I),
    re.compile(rf"proposed\s*date\s*of\s*hospitalization\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

_EXPLICIT_ADMISSION_PATTERNS = [
    re.compile(rf"date\s*of\s*admission\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"date\s*of\s*admission\s*[:.]?\s*{_DATE_TEXT_ORDINAL}", re.I),
    # Exclude "Proposed Date of Hospitalization …" (query-letter planned date)
    re.compile(rf"(?<!proposed\s)date\s*of\s*hospitalization\s*[:.]?\s*{_DATE_TEXT}", re.I),
    re.compile(rf"(?<!proposed\s)date\s*of\s*hospitalization\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"(?:doa|d\.o\.a\.?)\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
    re.compile(rf"admitted\s*(?:on|date)?\s*[:.]?\s*{_DATE_NUMERIC}", re.I),
]

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

_FIELD_LABELS = {
    "consultation_date": "Consultation date",
    "date_of_admission": "Date of admission",
    "proposed_hospitalization_date": "Proposed hospitalization date",
    "date_of_discharge": "Date of discharge",
}

_PRIMARY_DATE_FIELDS = ("consultation_date", "date_of_admission", "date_of_discharge")
_ALL_DATE_FIELDS = _PRIMARY_DATE_FIELDS + ("proposed_hospitalization_date",)

_SOURCE_DOC_MARKER = re.compile(r"=== Source document:\s*(.+?)\s*===", re.I)
_VISION_BLOCK_MARKER = re.compile(
    r"=== Page \d+ — vision transcription \(([^)]+)\) ===",
    re.I,
)


def _norm_text(val: str) -> str:
    return " ".join(str(val or "").strip().lower().split())


def _norm_date(val: str) -> str:
    return " ".join(str(val or "").strip().split())


def _is_bad_date(val: str) -> bool:
    low = _norm_date(val).lower()
    if low in _BAD_DATE_VALUES or "*" in low:
        return True
    if re.search(r"_+|not\s+legible|illegible", low):
        return True
    return False


def _strip_ordinal(val: str) -> str:
    return re.sub(r"(\d{1,2})(?:st|nd|rd|th)\b", r"\1", val, flags=re.I)


def _infer_year_for_partial(val: str, reference_year: Optional[int]) -> str:
    """Turn handwritten partial dates like 30/6 into 30/06/2026 when year is omitted."""
    val = _strip_ordinal(_norm_date(val))
    if re.search(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", val):
        return val
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})$", val)
    if m and reference_year:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{day:02d}/{month:02d}/{reference_year}"
    return val


def _parse_flexible_date(val: str, reference_year: Optional[int] = None) -> Optional[datetime]:
    val = _infer_year_for_partial(_strip_ordinal(_norm_date(val)), reference_year)
    parsed = None
    for fmt in (
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y",
        "%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y",
    ):
        try:
            parsed = datetime.strptime(val, fmt)
            break
        except ValueError:
            continue
    if not parsed:
        return None
    return _clamp_implausible_year(parsed, reference_year)


def _clamp_implausible_year(dt: datetime, reference_year: Optional[int] = None) -> datetime:
    """Fix OCR year errors like 18/07/36 → 2036 or 2026 misread as 2036.

    Two-digit years 00–68 map to 2000–2068 in strptime; OCR often turns 2026→2036.
    """
    now_year = datetime.utcnow().year
    anchor = reference_year if reference_year else now_year
    year = dt.year

    def _ok(y: int) -> bool:
        return 2018 <= y <= now_year + 1 and abs(y - anchor) <= 2

    if year <= now_year + 1 and (reference_year is None or abs(year - anchor) <= 2):
        if year >= 2018:
            return dt

    candidates: List[int] = []
    if reference_year:
        candidates.append(reference_year)
        # Same 2-digit year in reference century (36 with ref 2026 → try 2036 already bad; use ref)
        yy = year % 100
        candidates.append((reference_year // 100) * 100 + yy)
    # Common OCR digit flip: 2036 → 2026
    if year >= 2030:
        candidates.append(year - 10)
    candidates.append(now_year)
    candidates.append(anchor)

    for cand in candidates:
        if _ok(cand):
            try:
                return dt.replace(year=cand)
            except ValueError:
                continue
    # Last resort: clamp to anchor year
    try:
        return dt.replace(year=min(max(anchor, 2018), now_year + 1))
    except ValueError:
        return dt


def _canonical_date_key(val: str, reference_year: Optional[int] = None) -> str:
    parsed = _parse_flexible_date(val, reference_year)
    return parsed.strftime("%Y-%m-%d") if parsed else _norm_date(val).lower()


def _parse_date_year(val: str, reference_year: Optional[int] = None) -> Optional[int]:
    parsed = _parse_flexible_date(val, reference_year)
    if parsed:
        return parsed.year
    m = re.search(r"(20\d{2})", val or "")
    return int(m.group(1)) if m else None


def _is_plausible_for_claim(date_val: str, reference_year: Optional[int]) -> bool:
    year = _parse_date_year(date_val, reference_year)
    if year is None:
        return True
    now_year = datetime.utcnow().year
    # Never accept years far in the future (OCR 2036, etc.)
    if year > now_year + 1:
        return False
    if reference_year is None:
        return year >= 2018
    return abs(year - reference_year) <= 2


def _format_date_display(val: str, reference_year: Optional[int] = None) -> str:
    """Normalize extracted dates to DD/MM/YYYY with clamped year."""
    parsed = _parse_flexible_date(val, reference_year)
    if parsed:
        return parsed.strftime("%d/%m/%Y")
    return (val or "").strip()


def _claim_reference_year(text: str) -> Optional[int]:
    years: List[int] = []
    now_year = datetime.utcnow().year
    m = re.search(r"claim\s*incident\s*[:.]?\s*(20\d{2})", text or "", re.I)
    if m:
        years.append(int(m.group(1)))
    # Bare claim incident numbers like 2026071800281
    for m in re.finditer(r"\b(20\d{2})\d{6,}\b", text or ""):
        years.append(int(m.group(1)))
    for pat in (
        r"receiving\s*date\s*[:.]?\s*\d{1,2}[/.-]\d{1,2}[/.-](20\d{2})",
        r"reporting\s*date\s*[:.]?\s*\d{1,2}[/.-]\d{1,2}[/.-](20\d{2})",
        r"proposed\s*date\s*of\s*hospitalization\s*\d{1,2}\s+\w+\s+(20\d{2})",
        r"query\s*letter\s*date\s*[:.]?\s*\d{1,2}\s+\w+\s+(20\d{2})",
        r"date\s*[:.]?\s*\d{1,2}\s+\w+\s+(20\d{2})",
        r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b",
    ):
        for match in re.finditer(pat, text or "", re.I):
            y = int(match.group(match.lastindex or 1))
            if 2018 <= y <= now_year + 1:
                years.append(y)
    # Prefer years close to now; drop far-future OCR junk
    years = [y for y in years if 2018 <= y <= now_year + 1]
    if not years:
        return now_year
    # Mode-ish: most common, else max near now
    return max(set(years), key=lambda y: (years.count(y), -abs(y - now_year)))


def _classify_document(fname: str, text: str) -> str:
    """Classify by document content; filename is only a weak hint."""
    blob = text or ""
    name = (fname or "").lower()
    scores: Dict[str, int] = {}

    def bump(doc_type: str, amount: int) -> None:
        scores[doc_type] = scores.get(doc_type, 0) + amount

    if _QUERY_LETTER_MARKERS.search(blob):
        bump("query_letter", 12)
    if re.search(r"policy\s*no|member\s*code|claim\s*incident", blob, re.I):
        bump("query_letter", 4)
    if _PREAUTH_MARKERS.search(blob):
        bump("pre_auth", 12)
    if re.search(r"request\s+for\s+cashless|cashless\s+hospitalization", blob, re.I):
        bump("pre_auth", 6)
    if _LAB_REPORT_MARKERS.search(blob):
        bump("lab_report", 14)
    if _RADIOLOGY_MARKERS.search(blob):
        bump("radiology", 14)
    if _INDOOR_CASE_MARKERS.search(blob):
        bump("indoor_case", 14)
    if _DISCHARGE_MARKERS.search(blob) and re.search(r"date\s*of\s*discharge\s*[:.]", blob, re.I):
        bump("discharge", 14)
    if _CLINICAL_MARKERS.search(blob) or re.search(r"consultation\s+note", blob, re.I):
        bump("clinical", 10)
    if re.search(r"bill|invoice|receipt|amount\s+paid", blob, re.I):
        bump("bill", 8)
    if re.search(r"policy\s+wording|schedule\s+of\s+benefits|general\s+terms", blob, re.I):
        bump("policy", 14)

    if re.search(r"query|querr|reply", name):
        bump("query_letter", 2)
    if re.search(r"pre[\s-]?auth|preauth", name):
        bump("pre_auth", 2)
    if re.search(r"indoor\s+case|treatment|\bicps\b", name):
        bump("indoor_case", 4)
    if re.search(r"investigation|lab", name):
        bump("lab_report", 4)
    if re.search(r"ct\s*scan|echo|ecg|mri|x-?ray|radiol", name):
        bump("radiology", 4)
    if "discharge" in name:
        bump("discharge", 2)
    if re.search(r"clinical|consult", name):
        bump("clinical", 2)
    if re.search(r"bill|receipt|invoice", name):
        bump("bill", 2)

    if not scores:
        return "other"
    # Indoor case charts often mention cashless/insurance — do not label them pre-auth.
    if scores.get("indoor_case", 0) >= 10 and scores.get("pre_auth", 0) < 18:
        scores.pop("pre_auth", None)
    if not scores:
        return "other"
    return max(scores.items(), key=lambda item: item[1])[0]


def _document_medium(doc_type: str) -> str:
    if doc_type == "query_letter":
        return "computer-generated"
    if doc_type in ("pre_auth", "clinical", "indoor_case"):
        return "handwritten/scanned"
    if doc_type in ("lab_report", "radiology"):
        return "typed report"
    if doc_type == "discharge":
        return "typed or handwritten"
    return "document"


def _source_label(fname: str, doc_type: str) -> str:
    medium = _document_medium(doc_type)
    type_name = {
        "query_letter": "Query Letter",
        "pre_auth": "Pre-Authorization Form",
        "indoor_case": "Indoor Case Paper / Treatment Sheet",
        "lab_report": "Lab / Investigation Report",
        "radiology": "Radiology / Imaging Report",
        "clinical": "Clinical Document",
        "discharge": "Discharge Summary",
        "bill": "Bill / Receipt",
    }.get(doc_type, "Document")
    name = fname if fname and fname not in ("combined", "unknown") else type_name
    return f"{name} — {type_name} ({medium})"


def _first_match(
    patterns: List[re.Pattern],
    text: str,
    reference_year: Optional[int] = None,
) -> str:
    for pat in patterns:
        m = pat.search(text or "")
        if m:
            val = _infer_year_for_partial(_norm_date(m.group(1)), reference_year)
            if _is_bad_date(val):
                continue
            formatted = _format_date_display(val, reference_year)
            if formatted and _is_plausible_for_claim(formatted, reference_year):
                return formatted
    return ""


def _clinical_consult_dates(text: str, reference_year: Optional[int] = None) -> List[str]:
    dates: List[str] = []
    if not _CLINICAL_MARKERS.search(text or "") and "consultation note" not in (text or "").lower():
        return dates
    for m in _CLINICAL_CONSULT_BLOCK.finditer(text or ""):
        val = _format_date_display(
            _infer_year_for_partial(_norm_date(m.group(1)), reference_year),
            reference_year,
        )
        if val and not _is_bad_date(val) and _is_plausible_for_claim(val, reference_year):
            dates.append(val)
    for pat in _CONSULT_PATTERNS:
        for m in pat.finditer(text or ""):
            val = _format_date_display(
                _infer_year_for_partial(_norm_date(m.group(1)), reference_year),
                reference_year,
            )
            if val and not _is_bad_date(val) and _is_plausible_for_claim(val, reference_year):
                dates.append(val)
    return list(dict.fromkeys(dates))


def _extract_discharge_date(text: str, reference_year: Optional[int] = None) -> str:
    if _BOILERPLATE_DISCHARGE.search(text or ""):
        snippet = text or ""
        if not re.search(r"date\s*of\s*discharge\s*[:.]\s*\d", snippet, re.I):
            return ""
    return _first_match(_DISCHARGE_PATTERNS, text, reference_year)


def _extract_proposed_hospitalization(text: str, reference_year: Optional[int] = None) -> str:
    return _first_match(_PROPOSED_HOSPITALIZATION_PATTERNS, text, reference_year)


def _infer_nature_of_admission(text: str, doc_type: str) -> str:
    blob = text or ""
    if re.search(
        r"\b(?:emergency|casualty|trauma|walk[\s-]?in)\b|unstable\s+angina|\bacs\b|"
        r"trop-?[ti]\s*\+?|troponin|chest\s+discomfort|"
        r"compression\s+fracture|vertebral\s+fracture|fall\s+(?:from|at|down)|"
        r"unable\s+to\s+walk|cannot\s+walk",
        blob,
        re.I,
    ):
        return "Emergency"
    if doc_type == "query_letter" or re.search(
        r"proposed\s*date\s*of\s*hospitalization|elective|planned\s*admission",
        blob,
        re.I,
    ):
        return "Planned / Elective"
    return ""


def _looks_like_ocr_garbage(val: str) -> bool:
    v = (val or "").strip()
    if not v:
        return True
    if re.search(r"[|\[\]{}\\^=`~]", v):
        return True
    clean = sum(c.isalnum() or c.isspace() or c in ".&'/-()," for c in v)
    if clean / max(len(v), 1) < 0.82:
        return True
    words = re.findall(r"[A-Za-z]{3,}", v)
    return len(words) == 0


def _looks_like_address_not_hospital(val: str) -> bool:
    """Reject OCR crumbs like 'A. Near Civil Hospital' or 'Opp. Bus Stand Hospital'."""
    v = (val or "").strip()
    if not v:
        return True
    if _HOSPITAL_ADDRESS_PREFIXES.search(v):
        return True
    words = [w.lower() for w in re.findall(r"[A-Za-z]{2,}", v)]
    if not words:
        return True
    # Single meaningful word + Hospital (e.g. "Civil Hospital") is weak; address tokens worse
    address_hits = sum(1 for w in words if w in _HOSPITAL_ADDRESS_TOKENS)
    if address_hits >= 1 and len(words) <= 4:
        return True
    # Leading single-letter token ("A Near...", "B Hospital")
    if re.match(r"^[A-Z]\b", v) and len(words) <= 4:
        return True
    return False


def _score_hospital_name(val: str) -> int:
    """Higher is better. Address crumbs and garbage score <= 0."""
    v = (val or "").strip()
    if not _is_plausible_hospital_name(v):
        return -1
    score = 10
    low = v.lower()
    if re.search(r"\bhospital\b", low):
        score += 20
    if re.search(r"\b(?:clinic|nursing\s+home|institute|medical\s+centre|medical\s+center)\b", low):
        score += 15
    if re.search(r"\b(?:super\s+speciality|multispeciality|multi[\s-]?specialty)\b", low):
        score += 8
    words = re.findall(r"[A-Za-z]{3,}", v)
    score += min(len(words), 6) * 3
    if len(v) >= 20:
        score += 5
    if _looks_like_address_not_hospital(v):
        return -1
    if _looks_like_ocr_garbage(v):
        return -1
    return score


def _is_plausible_hospital_name(val: str) -> bool:
    v = (val or "").strip()
    if len(v) < 8 or len(v) > 120:
        return False
    if _looks_like_ocr_garbage(v):
        return False
    if _looks_like_address_not_hospital(v):
        return False
    low = v.lower()
    if any(tok in low for tok in ("patient", "request for cashless", "policy", "member code")):
        return False
    if _HOSPITAL_BOILERPLATE.search(v):
        return False
    if not any(tok in low for tok in ("hospital", "clinic", "nursing", "medical", "health", "institute")):
        return False
    words = re.findall(r"[A-Za-z]{3,}", v)
    # Need a real proper name, not just "Civil Hospital" / "Near Hospital"
    content_words = [w for w in words if w.lower() not in _HOSPITAL_ADDRESS_TOKENS | {"hospital", "clinic", "nursing", "home", "medical", "centre", "center", "institute", "healthcare", "health", "care"}]
    return len(content_words) >= 1 and len(words) >= 2


def _clean_hospital_candidate(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    name = re.split(
        r"\s+(?:where|date|policy|member|claim|proposed|patient|age|sex|gender|"
        r"unless|arising|hospitalization)\b",
        name,
        maxsplit=1,
        flags=re.I,
    )[0]
    name = name.strip(" .-|([,:;")
    return name


def _extract_hospital(text: str) -> str:
    """Pick the best hospital name from all pattern matches (never address crumbs)."""
    candidates: List[Tuple[str, int]] = []
    for pat in _HOSPITAL_PATTERNS:
        for m in pat.finditer(text or ""):
            name = _clean_hospital_candidate(m.group(1))
            score = _score_hospital_name(name)
            if score > 0:
                candidates.append((name, score))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _format_inr_amount(raw: str) -> str:
    val = (raw or "").replace(",", "").strip()
    if not val or not re.fullmatch(r"\d+(?:\.\d{1,2})?", val):
        return ""
    num = float(val)
    if num < 100:
        return ""
    formatted = f"{num:,.2f}".rstrip("0").rstrip(".")
    if "." in formatted:
        parts = formatted.split(".")
        parts[0] = parts[0].replace(",", "")
        parts[0] = f"{int(parts[0]):,}"
        formatted = ".".join(parts)
    else:
        formatted = f"{int(num):,}"
    return f"Rs. {formatted}"


def _extract_bill_amount(text: str) -> str:
    """Best-effort total from hospital bill / invoice / pre-auth estimate text."""
    if not text:
        return ""
    has_billing_context = bool(re.search(
        r"bill|invoice|receipt|charges|payable|estimated\s+(?:cost|amount)|"
        r"package\s+(?:cost|amount)|total\s+(?:cost|amount)\s+of",
        text,
        re.I,
    ))
    if not has_billing_context:
        return ""
    best = ""
    best_val = 0.0
    for pat in _TOTAL_BILL_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            if val > best_val:
                best_val = val
                best = raw
    if best_val < _MIN_HOSPITAL_BILL_INR:
        return ""
    return _format_inr_amount(best) if best else ""


def _extract_room_category(text: str) -> str:
    for pat in _ROOM_CATEGORY_PATTERNS:
        m = pat.search(text or "")
        if m:
            val = re.sub(r"\s+", " ", m.group(1).strip()).strip(" .[]")
            if val and len(val) >= 3 and "hospital" not in val.lower():
                return val.title() if val.islower() else val
    return ""


def _extract_admission_date(text: str, doc_type: str, reference_year: Optional[int]) -> str:
    """Actual admission only — never from lab receiving/reporting dates."""
    if doc_type == "lab_report":
        return ""
    # Explicit DOA is allowed even on query letters (mixed/misclassified packets).
    # Proposed hospitalization is extracted separately and must not populate this field.
    val = _first_match(_EXPLICIT_ADMISSION_PATTERNS, text, reference_year)
    if not val and doc_type == "query_letter":
        return ""
    if not val and doc_type == "indoor_case":
        val = _first_match(_TREATMENT_SHEET_DATE_PATTERNS, text, reference_year)
    if not val and doc_type == "radiology" and re.search(r"icu", text or "", re.I):
        val = _first_match(_ICU_IMAGING_DATE_PATTERNS, text, reference_year)
    if val and not _is_plausible_for_claim(val, reference_year):
        return ""
    return val


def _extract_lab_report_dates(text: str, reference_year: Optional[int]) -> List[str]:
    dates: List[str] = []
    for pat in _LAB_RECEIVING_DATE_PATTERNS:
        for m in pat.finditer(text or ""):
            val = _infer_year_for_partial(_norm_date(m.group(1)), reference_year)
            if val and not _is_bad_date(val):
                dates.append(val)
    return list(dict.fromkeys(dates))


def _extract_consultation_date(
    text: str,
    doc_type: str,
    reference_year: Optional[int],
) -> str:
    """First consultation only — never lab receiving/reporting or sheet timestamps."""
    if doc_type == "lab_report":
        return ""
    clinical_dates = _clinical_consult_dates(text, reference_year) if doc_type == "clinical" else []
    if clinical_dates:
        return clinical_dates[0]
    if doc_type in ("pre_auth", "clinical", "other"):
        return _first_match(_CONSULT_PATTERNS, text, reference_year)
    if doc_type == "indoor_case":
        return _first_match(_CONSULT_PATTERNS, text, reference_year)
    return ""


def _extract_facts_for_document(
    fname: str,
    text: str,
    reference_year: Optional[int],
) -> Dict[str, str]:
    doc_type = _classify_document(fname, text)
    consult = _extract_consultation_date(text, doc_type, reference_year)

    admission = _extract_admission_date(text, doc_type, reference_year)
    proposed = _extract_proposed_hospitalization(text, reference_year) if doc_type == "query_letter" else ""
    discharge = _extract_discharge_date(text, reference_year) if doc_type in ("discharge", "pre_auth", "clinical") else ""

    if consult and not _is_plausible_for_claim(consult, reference_year):
        consult = ""
    if discharge and not _is_plausible_for_claim(discharge, reference_year):
        discharge = ""

    return {
        "consultation_date": consult,
        "date_of_admission": admission,
        "proposed_hospitalization_date": proposed,
        "date_of_discharge": discharge,
        "hospital": _extract_hospital(text),
        "total_hospital_bill": _extract_bill_amount(text),
        "nature_of_admission": _infer_nature_of_admission(text, doc_type),
        "room_category_eligible": _extract_room_category(text),
        "_doc_type": doc_type,
        "_fname": fname,
    }


def _field_priority(doc_type: str, field: str) -> int:
    """Handwritten pre-auth and clinical notes beat computer-generated query letters."""
    table = {
        "consultation_date": {
            "pre_auth": 100,
            "clinical": 95,
            "indoor_case": 70,
            "query_letter": 40,
            "lab_report": 0,
            "radiology": 5,
            "other": 30,
        },
        "date_of_admission": {
            "pre_auth": 100,
            "indoor_case": 95,
            "clinical": 88,
            "radiology": 75,
            "bill": 70,
            "discharge": 65,
            "query_letter": 20,
            "lab_report": 5,
            "other": 30,
        },
        "proposed_hospitalization_date": {
            "query_letter": 100,
            "pre_auth": 30,
            "other": 10,
        },
        "date_of_discharge": {
            "discharge": 100,
            "pre_auth": 80,
            "clinical": 40,
            "other": 20,
        },
        "hospital": {
            "query_letter": 100,
            "pre_auth": 95,
            "indoor_case": 90,
            "clinical": 88,
            "discharge": 85,
            "radiology": 50,
            "lab_report": 30,
            "bill": 85,
        },
        "total_hospital_bill": {
            "bill": 100,
            "pre_auth": 40,
            "discharge": 70,
            "other": 20,
        },
        "nature_of_admission": {
            "indoor_case": 100,
            "pre_auth": 95,
            "clinical": 80,
            "query_letter": 90,
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
    facts.pop("_fname", None)
    return facts


def _pick_best_candidate(
    candidates: List[Tuple[str, int, str, str]],
) -> Tuple[str, str]:
    ranked = sorted(
        [(v, p, fname, doc_type) for v, p, fname, doc_type in candidates if v and not _is_bad_date(v)],
        key=lambda x: x[1],
        reverse=True,
    )
    if not ranked:
        return "", ""
    value, _prio, fname, doc_type = ranked[0]
    return value, _source_label(fname, doc_type)


def _build_date_provenance(
    per_source: List[Tuple[str, Dict[str, str], str]],
) -> Dict[str, List[Dict[str, str]]]:
    provenance: Dict[str, List[Dict[str, str]]] = {f: [] for f in _ALL_DATE_FIELDS}
    seen: Dict[str, set] = {f: set() for f in _ALL_DATE_FIELDS}

    for fname, facts, doc_type in per_source:
        for field in _ALL_DATE_FIELDS:
            val = facts.get(field, "")
            if not val or _is_bad_date(val):
                continue
            key = (fname, val, doc_type)
            if key in seen[field]:
                continue
            seen[field].add(key)
            provenance[field].append({
                "value": val,
                "source_file": fname,
                "document_type": doc_type,
                "medium": _document_medium(doc_type),
                "source_label": _source_label(fname, doc_type),
                "field": field,
                "field_label": _FIELD_LABELS.get(field, field),
            })
    return provenance


def _flatten_all_document_dates(provenance: Dict[str, List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """All labeled dates found across every uploaded document."""
    rows: List[Dict[str, str]] = []
    seen: set = set()
    for field in _ALL_DATE_FIELDS:
        for entry in provenance.get(field) or []:
            key = (field, entry.get("value"), entry.get("source_file"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(dict(entry))
    return rows


def _detect_date_discrepancies(
    provenance: Dict[str, List[Dict[str, str]]],
    reference_year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Flag conflicting dates for the same field across documents.

    Proposed hospitalization date (query letter) is intentionally NOT compared
    against actual admission date — both are shown in claim details separately.
    """
    discrepancies: List[Dict[str, Any]] = []
    compare_fields = ("consultation_date", "date_of_admission", "date_of_discharge")

    for field in compare_fields:
        entries = list(provenance.get(field) or [])
        # Do NOT merge proposed_hospitalization_date into date_of_admission.

        by_canonical: Dict[str, List[Dict[str, str]]] = {}
        for entry in entries:
            if field == "date_of_admission" and entry.get("document_type") == "lab_report":
                continue
            canon = _canonical_date_key(entry["value"], reference_year)
            if not canon:
                continue
            by_canonical.setdefault(canon, []).append(entry)
        if len(by_canonical) <= 1:
            continue

        parts = []
        for group in by_canonical.values():
            sample = group[0]
            label = sample.get("field_label") or _FIELD_LABELS.get(field, field)
            parts.append(f"{label}: {sample['value']} in {sample['source_label']}")

        message = f"{_FIELD_LABELS[field]} differs across documents: " + "; ".join(parts) + "."
        discrepancies.append({
            "field": field,
            "label": _FIELD_LABELS[field],
            "entries": entries,
            "message": message,
        })
    return discrepancies


def filter_actionable_date_discrepancies(
    discrepancies: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Discrepancies that should trigger fraud/risk flags (excludes proposed vs actual admission)."""
    actionable: List[Dict[str, Any]] = []
    for item in discrepancies or []:
        if not isinstance(item, dict):
            continue
        if item.get("field") == "date_of_admission":
            entries = item.get("entries") or []
            if any(
                isinstance(e, dict)
                and e.get("field") == "proposed_hospitalization_date"
                for e in entries
            ):
                continue
        actionable.append(item)
    return actionable


def _gather_text_for_file(case_text: str, pdf_path: str, fname: str) -> str:
    """Collect all text belonging to one uploaded PDF (native + vision blocks)."""
    parts: List[str] = []

    if pdf_path:
        try:
            import fitz
            doc = fitz.open(pdf_path)
            native = "\n".join((p.get_text() or "") for p in doc)
            doc.close()
            if native.strip():
                parts.append(native.strip())
        except Exception:
            pass

    if case_text and fname:
        source_pat = re.compile(
            rf"=== Source document:\s*{re.escape(fname)}\s*===\s*(.*?)(?=\n=== Source document:|\Z)",
            re.I | re.S,
        )
        m = source_pat.search(case_text)
        if m:
            parts.append(m.group(1).strip())

        vision_pat = re.compile(
            rf"=== Page \d+ — vision transcription \({re.escape(fname)}\) ===\s*(.*?)(?=\n=== Page |\n=== Source document:|\Z)",
            re.I | re.S,
        )
        for vm in vision_pat.finditer(case_text):
            block = vm.group(1).strip()
            if block:
                parts.append(block)

    return "\n\n".join(dict.fromkeys(p for p in parts if p.strip()))


def _split_case_text_blocks(case_text: str) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    if not case_text:
        return blocks

    source_parts = _SOURCE_DOC_MARKER.split(case_text)
    if len(source_parts) > 1:
        for i in range(1, len(source_parts), 2):
            fname = source_parts[i].strip()
            body = source_parts[i + 1] if i + 1 < len(source_parts) else ""
            if fname and body.strip():
                blocks.append((fname, body.strip()))
        if blocks:
            return blocks

    parts = re.split(r"=== Page \d+ — vision transcription \(([^)]+)\) ===", case_text)
    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            fname = parts[i].strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""
            if fname and body.strip():
                blocks.append((fname, body.strip()))
    return blocks


def _sanitize_consultation_date(
    result: Dict[str, Any],
    per_source: List[Tuple[str, Dict[str, str], str]],
    case_text: str,
    reference_year: Optional[int],
) -> None:
    """Drop consult dates that mirror lab receiving/reporting or post-admission sheet stamps."""
    consult = result.get("consultation_date", "")
    if not consult:
        return

    lab_anchors: set = set()
    for fname, _facts, doc_type in per_source:
        if doc_type != "lab_report":
            continue
        for block_fname, body in _split_case_text_blocks(case_text):
            if block_fname != fname:
                continue
            for val in _extract_lab_report_dates(body, reference_year):
                lab_anchors.add(_canonical_date_key(val, reference_year))

    consult_key = _canonical_date_key(consult, reference_year)
    if consult_key in lab_anchors:
        result["consultation_date"] = ""
        result["consultation_date_source"] = ""
        return

    provenance = result.get("date_provenance") or {}
    consult_entries = provenance.get("consultation_date") or []
    if consult_entries:
        matching = [e for e in consult_entries if e.get("value") == consult]
        if matching and all(e.get("document_type") == "query_letter" for e in matching):
            if not re.search(r"first\s+consultation|date\s+of\s+consultation", case_text or "", re.I):
                result["consultation_date"] = ""
                result["consultation_date_source"] = ""
                return
    else:
        matching_types = [
            doc_type for _fname, facts, doc_type in per_source
            if facts.get("consultation_date") == consult
        ]
        if matching_types and all(dt == "query_letter" for dt in matching_types):
            if not re.search(r"first\s+consultation|date\s+of\s+consultation", case_text or "", re.I):
                result["consultation_date"] = ""
                result["consultation_date_source"] = ""
                return

    ql_match = _QUERY_LETTER_HEADER_DATE.search(case_text or "")
    if ql_match:
        ql_parsed = _parse_flexible_date(ql_match.group(1).strip(), reference_year)
        ql_key = ql_parsed.strftime("%Y-%m-%d") if ql_parsed else ""
        if ql_key and ql_key == consult_key:
            result["consultation_date"] = ""
            result["consultation_date_source"] = ""
            return

    admission_key = _canonical_date_key(result.get("date_of_admission", ""), reference_year)
    if not admission_key or not consult_key:
        return

    source_label = _norm_text(result.get("consultation_date_source", ""))
    from_explicit_consult = any(
        token in source_label
        for token in ("pre-authorization", "clinical document", "consultation")
    )
    if consult_key > admission_key and not from_explicit_consult:
        result["consultation_date"] = ""
        result["consultation_date_source"] = ""


def enrich_claim_facts(
    case_text: str,
    pdf_paths: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Build claim details with source labels and cross-document discrepancy flags."""
    reference_year = _claim_reference_year(case_text)
    per_source: List[Tuple[str, Dict[str, str], str]] = []
    seen_files: set = set()

    if pdf_paths:
        for pdf_path, fname in pdf_paths:
            text = _gather_text_for_file(case_text, pdf_path, fname)
            if not text.strip():
                continue
            facts = _extract_facts_for_document(fname, text, reference_year)
            per_source.append((fname, facts, facts.get("_doc_type", "other")))
            seen_files.add(fname)

    for fname, body in _split_case_text_blocks(case_text):
        if fname in seen_files:
            continue
        facts = _extract_facts_for_document(fname, body, reference_year)
        per_source.append((fname, facts, facts.get("_doc_type", "other")))
        seen_files.add(fname)

    if not per_source:
        combined = _extract_facts_for_document("combined", case_text, reference_year)
        per_source.append((combined.get("_fname", "combined"), combined, combined.get("_doc_type", "other")))

    provenance = _build_date_provenance(per_source)
    all_document_dates = _flatten_all_document_dates(provenance)
    discrepancies = _detect_date_discrepancies(provenance, reference_year)

    result: Dict[str, Any] = {
        "consultation_date": "",
        "consultation_date_source": "",
        "date_of_admission": "",
        "date_of_admission_source": "",
        "proposed_hospitalization_date": "",
        "proposed_hospitalization_date_source": "",
        "date_of_discharge": "",
        "date_of_discharge_source": "",
        "hospital": "",
        "nature_of_admission": "",
        "room_category_eligible": "",
        "total_hospital_bill": "",
        "date_provenance": provenance,
        "all_document_dates": all_document_dates,
        "date_discrepancies": discrepancies,
    }

    for field in _PRIMARY_DATE_FIELDS + ("proposed_hospitalization_date",):
        candidates = [
            (facts.get(field, ""), _field_priority(doc_type, field), fname, doc_type)
            for fname, facts, doc_type in per_source
        ]
        value, source = _pick_best_candidate(candidates)
        result[field] = value
        result[f"{field}_source"] = source

    for field in ("hospital", "nature_of_admission", "room_category_eligible", "total_hospital_bill"):
        candidates = [
            (facts.get(field, ""), _field_priority(doc_type, field), fname, doc_type)
            for fname, facts, doc_type in per_source
        ]
        value, _source = _pick_best_candidate(candidates)
        result[field] = value

    _sanitize_consultation_date(result, per_source, case_text, reference_year)

    emergency_nature = _infer_nature_of_admission(case_text, "other")
    if emergency_nature == "Emergency":
        result["nature_of_admission"] = "Emergency"
    elif not result["nature_of_admission"] and (
        result["date_of_admission"] or result["proposed_hospitalization_date"]
    ):
        result["nature_of_admission"] = "Planned / Elective"

    return result


def _should_overwrite_claim_field(current: str, extracted: str) -> bool:
    return bool(extracted and not _is_bad_date(extracted))


def _should_overwrite_hospital(current: str, extracted: str) -> bool:
    if not extracted or _score_hospital_name(extracted) <= 0:
        return False
    cur = str(current or "").strip()
    if not cur or cur.lower() in _UNKNOWN_ADMISSION or _score_hospital_name(cur) <= 0:
        return True
    # Prefer higher-quality hospital names (query-letter full names beat address crumbs)
    return _score_hospital_name(extracted) > _score_hospital_name(cur)


def _should_overwrite_bill_total(current: str, extracted: str) -> bool:
    if not extracted:
        return False
    cur = str(current or "").strip()
    if not cur or cur.lower() in _UNKNOWN_ADMISSION:
        return True
    try:
        cur_num = float(re.sub(r"[^\d.]", "", cur.replace(",", "")) or "0")
        ext_num = float(re.sub(r"[^\d.]", "", extracted.replace(",", "")) or "0")
    except ValueError:
        return True
    return ext_num >= cur_num


def _should_overwrite_admission_nature(current: str, extracted: str) -> bool:
    if not extracted:
        return False
    cur = str(current or "").strip().lower()
    return not cur or cur in _UNKNOWN_ADMISSION


def merge_claim_details_into_result(result: dict, facts: Dict[str, Any]) -> dict:
    """Fill claim_details, attach source labels, and surface date discrepancies."""
    claim = result.setdefault("claim_details", {})
    mapping = {
        "consultation_date": facts.get("consultation_date", ""),
        "date_of_admission": facts.get("date_of_admission", ""),
        "proposed_hospitalization_date": facts.get("proposed_hospitalization_date", ""),
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
        elif key == "hospital":
            if _should_overwrite_hospital(str(claim.get(key) or ""), val):
                claim[key] = val
        elif _should_overwrite_claim_field(str(claim.get(key) or ""), val):
            claim[key] = val

    # Clamp OCR year errors (e.g. 18/07/2036 → 18/07/2026) on any remaining dates
    ref_blob = " ".join([
        str(facts.get("date_of_admission") or ""),
        str(claim.get("date_of_admission") or ""),
        str((result.get("insurance_details") or {}).get("claim_incident_number") or ""),
        str(facts.get("proposed_hospitalization_date") or ""),
    ])
    ref_year = _claim_reference_year(ref_blob)
    for field in _PRIMARY_DATE_FIELDS + ("proposed_hospitalization_date",):
        raw = str(claim.get(field) or "").strip()
        if not raw or _is_bad_date(raw):
            continue
        clamped = _format_date_display(raw, ref_year)
        if clamped and _is_plausible_for_claim(clamped, ref_year):
            claim[field] = clamped
        elif not _is_plausible_for_claim(raw, ref_year):
            claim[field] = ""

    # Drop address-like / OCR-garbage hospital names left by the LLM
    current_hospital = str(claim.get("hospital") or "").strip()
    if current_hospital and _score_hospital_name(current_hospital) <= 0:
        claim["hospital"] = ""

    for field in _PRIMARY_DATE_FIELDS + ("proposed_hospitalization_date",):
        source = facts.get(f"{field}_source", "")
        if source:
            claim[f"{field}_source"] = source

    if facts.get("date_provenance"):
        claim["date_provenance"] = facts["date_provenance"]
    if facts.get("all_document_dates"):
        claim["all_document_dates"] = facts["all_document_dates"]

    discrepancies = facts.get("date_discrepancies") or []
    if discrepancies:
        result["date_discrepancies"] = discrepancies
        actionable = filter_actionable_date_discrepancies(discrepancies)
        gaps = result.setdefault("documentation_gaps", [])
        challenges = result.setdefault("challenge_points", [])
        for item in actionable:
            msg = item.get("message", "")
            if msg and msg not in gaps:
                gaps.insert(0, msg)
            if msg and msg not in challenges:
                challenges.insert(0, f"Reconcile date discrepancy — {msg}")

    proposed = str(claim.get("proposed_hospitalization_date") or "").strip()
    actual = str(claim.get("date_of_admission") or "").strip()
    if proposed and actual:
        ref_blob = " ".join([
            str(claim.get("diagnosis") or ""),
            str((result.get("insurance_details") or {}).get("claim_incident_number") or ""),
        ])
        ref_year = _claim_reference_year(ref_blob)
        if _canonical_date_key(proposed, ref_year) != _canonical_date_key(actual, ref_year):
            claim["admission_dates_note"] = (
                f"Proposed hospitalization date: {proposed}. "
                f"Actual date of admission: {actual}. "
                "A difference between proposed and actual admission dates is expected for "
                "planned/elective cases and is not treated as a discrepancy or fraud indicator."
            )

    room = facts.get("room_category_eligible", "")
    if room:
        tba = result.setdefault("treatment_billing_audit", {})
        current = str(tba.get("room_category_eligible") or "").strip()
        if not current or current.lower() in _UNKNOWN_ADMISSION:
            tba["room_category_eligible"] = room

    bill_total = facts.get("total_hospital_bill", "")
    if bill_total and _should_overwrite_bill_total(
        str((result.get("financial_review") or {}).get("total_hospital_bill") or ""),
        bill_total,
    ):
        fin = result.setdefault("financial_review", {})
        fin["total_hospital_bill"] = bill_total

    diagnosis = facts.get("diagnosis", "")
    if diagnosis and not str(claim.get("diagnosis") or "").strip():
        claim["diagnosis"] = diagnosis

    if claim.get("date_of_admission") and not claim.get("date_of_discharge"):
        gaps = result.setdefault("documentation_gaps", [])
        gap_msg = (
            "Date of discharge not documented in uploaded files "
            "(discharge summary may not yet be available for a planned admission)."
        )
        if gap_msg not in gaps and not any(
            "discharge not documented" in str(g).lower() for g in gaps
        ):
            gaps.append(gap_msg)

    return result
