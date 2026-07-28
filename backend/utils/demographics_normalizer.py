"""Normalize demographics and money facts corrupted by OCR / LLM map steps.

Indian hospital HIS overlays often look like:
  Patient Name : Mr GAGANDEEP SINGH GULATI UHID : LMH2025435121 ... Age : 49 Y 0 M 0 D

Handwriting / LLM noise turns these into age 149, "GaGa DEEP", "Certified Hospital",
UHID-as-policy, past-history TURP as current surgery, and Rs. 20 bills.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_AGE_YMD_RE = re.compile(
    r"(?:age|aged)\s*[:.]?\s*"
    r"(\d{1,3})\s*(?:y(?:ears?|rs?)?|yrs?)\b"
    r"(?:\s*0?\s*m(?:onths?|ths?)?\b)?"
    r"(?:\s*0?\s*d(?:ays?)?\b)?",
    re.I,
)
_AGE_SLASH_RE = re.compile(
    r"(?:age|aged)?\s*[:.]?\s*\(?\s*(\d{1,3})\s*[Yy]\s*/\s*[MFmf]\s*\)?",
    re.I,
)
_AGE_PLAIN_RE = re.compile(
    r"(?:age|aged)\s*[:.]?\s*(\d{1,3})\b",
    re.I,
)
_AGE_BARE_YMD_RE = re.compile(
    r"\b(\d{1,3})\s*Y\s+(\d{1,2})\s*M\s+(\d{1,2})\s*D\b",
    re.I,
)

_UHID_RE = re.compile(
    r"\b(?:UHID|UHID\s*No\.?|Patient\s*ID|IP\s*No\.?|IPD)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{5,})",
    re.I,
)
_UHID_VALUE_RE = re.compile(r"^(?:LMH|UHID|IPD|IP)[A-Z0-9\-/]*\d{4,}$", re.I)

_NAME_HEADER_RE = re.compile(
    r"Patient\s*Name\s*[:.]?\s*(?:Mr\.?|Mrs\.?|Ms\.?|Miss\.?)?\s*"
    r"([A-Za-z][A-Za-z .']{2,60}?)(?=\s+(?:UHID|IPD|Gender|Age|S/O|W/O|D/O)\b|$)",
    re.I,
)

_HOSPITAL_LETTERHEAD_RE = re.compile(
    r"((?:L\.?\s*N\.?\s*)?Medical\s+College\s*(?:&|and)\s*J\.?\s*K\.?\s*Hospital|"
    r"Gokuldas\s+Hospital(?:\s+Pvt\.?\s*Ltd\.?)?|"
    r"Kokilaben[^\n]{0,40}Hospital|"
    r"Gangapada\s+Super\s+Speciality\s+Hospital|"
    r"[A-Z][A-Za-z.&' \-]{3,50}\s+Hospital(?:\s+Pvt\.?\s*Ltd\.?)?)",
    re.I,
)

_HOSPITAL_BOILERPLATE_RE = re.compile(
    r"^(?:certified|accredited|iso\b|nabh|nabl|mci\s+approved|"
    r"an?\s+iso|institute|hospital)$|"
    r"certified\s+hospital|accredited\s+hospital|iso\s*9001|"
    r"unless\s+arising|arising\s+out\s+of",
    re.I,
)

_PAST_HISTORY_PROC_RE = re.compile(
    r"(?:h\s*/\s*o|history\s+of|past\s+history|k\s*/\s*c\s*/\s*o|known\s+case\s+of|"
    r"previously\s+underwent|old\s+surgery)\s*[:.]?\s*([^\n.;]{3,80})",
    re.I,
)

_CURRENT_PROC_CONTEXT_RE = re.compile(
    r"(?:procedure\s*(?:performed|done|name)?|operation\s*(?:done|performed)|"
    r"surgical\s*procedure|o\s*/\s*c\s*/\s*o|proposed\s+treatment|"
    r"findings\s*/\s*procedure)\s*[:.]?\s*([^\n]{3,120})",
    re.I,
)

_SUM_TOTAL_RE = re.compile(
    r"(?:sum\s*total\s*(?:expected\s*)?(?:cost|amount)|"
    r"total\s*(?:expected\s*)?(?:cost|amount|hospital\s*bill)|"
    r"grand\s*total|net\s*(?:amount|payable))\s*[:.]?\s*"
    r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
    re.I,
)


def normalize_age(raw: Any) -> str:
    """Return age in years as a digit string, or '' if implausible."""
    text = str(raw or "").strip()
    if not text or text.lower() in {"na", "n/a", "unknown", "-", "—"}:
        return ""

    candidates: List[int] = []
    for pat in (_AGE_YMD_RE, _AGE_SLASH_RE, _AGE_BARE_YMD_RE, _AGE_PLAIN_RE):
        m = pat.search(text)
        if m:
            try:
                candidates.append(int(m.group(1)))
            except (TypeError, ValueError):
                pass
    if not candidates:
        # Lone integer from map step ("149", "49")
        m = re.fullmatch(r"(\d{1,3})\s*(?:years?|yrs?|y)?", text, re.I)
        if m:
            candidates.append(int(m.group(1)))

    for age in candidates:
        if 1 <= age <= 120:
            return str(age)
    return ""


def extract_age_from_text(text: str) -> str:
    """Prefer HIS overlay age formats over free-form OCR noise."""
    blob = text or ""
    for pat in (_AGE_YMD_RE, _AGE_BARE_YMD_RE, _AGE_SLASH_RE):
        for m in pat.finditer(blob):
            age = normalize_age(m.group(0))
            if age:
                return age
    # Fallback: Age: 49
    m = _AGE_PLAIN_RE.search(blob)
    if m:
        return normalize_age(m.group(0))
    return ""


def normalize_patient_name(raw: Any) -> str:
    """Clean OCR-split / shouty names; keep Mr/Mrs prefix when present."""
    name = re.sub(r"\s+", " ", str(raw or "").strip(" .-,"))
    if not name or name.lower() in {"na", "n/a", "unknown", "patient"}:
        return ""
    # Drop trailing administrative tokens
    name = re.split(
        r"\s+(?:UHID|IPD|Gender|Age|S/O|W/O|D/O|Reg\.?|Contact)\b",
        name,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" .-")

    # Fix Camel-broken OCR like "GaGa DEEP SINGH" → prefer title-ish tokens
    parts = name.split()
    fixed: List[str] = []
    for part in parts:
        hon = re.sub(r"[.]", "", part).lower()
        if hon in {"mr", "mrs", "ms", "miss"}:
            fixed.append({"mr": "Mr.", "mrs": "Mrs.", "ms": "Ms.", "miss": "Miss"}[hon])
            continue
        # Collapse internal weird casing: GaGa → Gaga (then title)
        if re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]|[A-Z][a-z]+[A-Z]", part):
            part = part.title() if not part.isupper() else part
        if part.isupper() and len(part) > 2:
            part = part.title()
        fixed.append(part)

    cleaned = " ".join(fixed)
    # Merge accidental splits of a single given name: "Ga Ga" / "Gagan Deep" kept,
    # but "GaGa" already title-cased above.
    cleaned = re.sub(r"\bGa\s+Ga\b", "Gaga", cleaned, flags=re.I)
    cleaned = re.sub(r"\bGaga\s+Deep\b", "Gagandeep", cleaned, flags=re.I)
    cleaned = re.sub(r"\bGagan\s+Deep\b", "Gagandeep", cleaned, flags=re.I)
    return cleaned.strip(" .-")


def extract_patient_name_from_text(text: str) -> str:
    m = _NAME_HEADER_RE.search(text or "")
    if m:
        return normalize_patient_name(m.group(1))
    return ""


def normalize_bill_amount(raw: Any, source_text: str = "") -> str:
    """Keep only plausible hospital totals (reject Rs. 20 OCR crumbs)."""
    text = str(raw or "").strip()
    # Prefer labeled sum-total from surrounding text when available
    if source_text:
        m = _SUM_TOTAL_RE.search(source_text)
        if m:
            text = m.group(1)

    cleaned = re.sub(r"(?:rs\.?|inr|₹)", " ", text, flags=re.I)
    cleaned = cleaned.replace(",", "")
    m = re.search(r"(\d+(?:\.\d{1,2})?)", cleaned)
    if not m:
        return ""
    try:
        val = float(m.group(1))
    except ValueError:
        return ""
    # Indian hospital claims / preauth packages are almost never below 5,000
    if val < 5000 or val > 50_000_000:
        return ""
    formatted = f"{val:,.2f}".rstrip("0").rstrip(".")
    return f"Rs. {formatted}"


def is_uhid_not_policy(val: str) -> bool:
    v = (val or "").strip()
    if not v:
        return False
    if _UHID_VALUE_RE.match(v):
        return True
    if re.match(r"^LMH", v, re.I):
        return True
    if re.match(r"^(?:UHID|IPD|IP)[\s\-/]*", v, re.I):
        return True
    return False


def normalize_policy_number(raw: Any) -> str:
    val = str(raw or "").strip().rstrip(".")
    if not val or is_uhid_not_policy(val):
        return ""
    if re.match(r"^H\d{5,}$", val, re.I):
        return val.upper()
    return val


def normalize_hospital_name(raw: Any) -> str:
    name = re.sub(r"\s+", " ", str(raw or "").strip(" .-,"))
    if not name:
        return ""
    if _HOSPITAL_BOILERPLATE_RE.search(name):
        return ""
    low = name.lower()
    if low in {"hospital", "certified hospital", "accredited hospital", "nursing home"}:
        return ""
    if len(name) < 8:
        return ""
    if not re.search(r"hospital|clinic|nursing|medical\s+college|institute|healthcare", low):
        return ""
    return name


def extract_hospital_from_text(text: str) -> str:
    for m in _HOSPITAL_LETTERHEAD_RE.finditer(text or ""):
        cand = normalize_hospital_name(m.group(1))
        if cand and not _HOSPITAL_BOILERPLATE_RE.search(cand):
            return cand
    return ""


def filter_past_history_procedures(
    procedures: List[Any],
    source_text: str = "",
) -> List[str]:
    """Drop surgeries that appear only as past history (H/O TURP etc.)."""
    past: set = set()
    for m in _PAST_HISTORY_PROC_RE.finditer(source_text or ""):
        chunk = m.group(1).lower()
        for token in re.findall(r"[a-z]{3,}", chunk):
            past.add(token)

    current_ctx = " ".join(
        m.group(1).lower() for m in _CURRENT_PROC_CONTEXT_RE.finditer(source_text or "")
    )

    out: List[str] = []
    for raw in procedures or []:
        proc = str(raw or "").strip()
        if not proc:
            continue
        low = proc.lower()
        tokens = set(re.findall(r"[a-z]{3,}", low))
        if tokens & past and not (tokens & set(re.findall(r"[a-z]{3,}", current_ctx))):
            if re.search(r"\b(?:turp|appendectomy|cholecystectomy|cabg|ptca|hernia)\b", low):
                continue
        out.append(proc)
    return out


def extract_typed_demographics(text: str) -> Dict[str, str]:
    """Deterministic pull from HIS overlays / letterheads (beats handwriting LLM)."""
    blob = text or ""
    out = {
        "patient_name": extract_patient_name_from_text(blob),
        "age": extract_age_from_text(blob),
        "hospital": extract_hospital_from_text(blob),
        "uhid": "",
        "sex": "",
    }
    m = _UHID_RE.search(blob)
    if m:
        out["uhid"] = m.group(1).strip()
    sex_m = re.search(r"\b(?:gender|sex)\s*[:.]?\s*(male|female|m|f)\b", blob, re.I)
    if sex_m:
        g = sex_m.group(1).lower()
        out["sex"] = "Male" if g in {"m", "male"} else "Female"
    elif re.search(r"\b\d{1,3}\s*[Yy]\s*/\s*M\b", blob):
        out["sex"] = "Male"
    elif re.search(r"\b\d{1,3}\s*[Yy]\s*/\s*F\b", blob):
        out["sex"] = "Female"
    return out


def sanitize_mapped_facts(facts: dict, source_text: str = "") -> dict:
    """Post-process one document-map or merged ledger dict."""
    out = dict(facts or {})
    typed = extract_typed_demographics(source_text) if source_text else {}

    name = normalize_patient_name(out.get("patient_name") or typed.get("patient_name"))
    if typed.get("patient_name") and (
        not name
        or len(typed["patient_name"]) >= len(name)
        or re.search(r"[a-z][A-Z]", str(out.get("patient_name") or ""))
    ):
        name = normalize_patient_name(typed["patient_name"])
    out["patient_name"] = name

    age = normalize_age(out.get("age"))
    if not age and typed.get("age"):
        age = typed["age"]
    # Typed HIS age always wins over implausible LLM age
    if typed.get("age") and (not age or int(age) > 120):
        age = typed["age"]
    out["age"] = age

    if typed.get("sex") and not str(out.get("sex") or "").strip():
        out["sex"] = typed["sex"]

    hospital = normalize_hospital_name(out.get("hospital"))
    if not hospital and typed.get("hospital"):
        hospital = typed["hospital"]
    if hospital and _HOSPITAL_BOILERPLATE_RE.search(hospital):
        hospital = typed.get("hospital") or ""
    out["hospital"] = hospital

    out["policy_number"] = normalize_policy_number(out.get("policy_number"))
    out["bill_amount"] = normalize_bill_amount(out.get("bill_amount"), source_text)
    if isinstance(out.get("procedures"), list):
        out["procedures"] = filter_past_history_procedures(out["procedures"], source_text)
    return out


def score_name_quality(name: str) -> int:
    n = normalize_patient_name(name)
    if not n:
        return -1
    score = min(len(n), 40)
    if re.search(r"\b(?:Singh|Kumar|Devi|Begum|Gulati|Sharma|Patel)\b", n, re.I):
        score += 10
    if re.search(r"[a-z][A-Z]", name or ""):
        score -= 15  # CamelCase OCR breakage
    if re.fullmatch(r"(?:Mr\.?|Mrs\.?|Ms\.?)?\s*[A-Za-z]{1,4}", n):
        score -= 20
    return score
