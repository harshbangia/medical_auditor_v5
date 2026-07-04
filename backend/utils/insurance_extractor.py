"""Extract insurance facts from claim documents.

Typed insurer letters often embed the company name only in a logo or footer
image — PyMuPDF native text extraction misses it entirely. This module:
  1. Regex-parses policy / claim / member IDs from typed letter text.
  2. Runs a lightweight vision pass on insurance-letter page 1 when the company
     name is still missing.
"""

import base64
import re
from typing import Dict, List, Optional, Tuple

import fitz

_INSURER_NAME_PATTERNS = [
    re.compile(r"iffco[\s-]*tokio[\s\w]*insurance[\w\s]*", re.I),
    re.compile(r"star[\s\w]*health[\w\s]*", re.I),
    re.compile(r"hdfc[\s\w]*ergo[\w\s]*", re.I),
    re.compile(r"icici[\s\w]*lombard[\w\s]*", re.I),
    re.compile(r"bajaj[\s\w]*allianz[\w\s]*", re.I),
    re.compile(r"new[\s\w]*india[\s\w]*assurance[\w\s]*", re.I),
    re.compile(r"national[\s\w]*insurance[\w\s]*", re.I),
    re.compile(r"united[\s\w]*india[\w\s]*", re.I),
    re.compile(r"religare[\w\s]*", re.I),
    re.compile(r"care[\s\w]*health[\w\s]*", re.I),
    re.compile(r"niva[\s\w]*bupa[\w\s]*", re.I),
    re.compile(r"max[\s\w]*bupa[\w\s]*", re.I),
    re.compile(r"acko[\s\w]*general[\w\s]*", re.I),
    re.compile(r"go[\s\w]*digit[\w\s]*", re.I),
    re.compile(r"cholamandalam[\w\s]*", re.I),
    re.compile(r"royal[\s\w]*sundaram[\w\s]*", re.I),
    re.compile(r"future[\s\w]*generali[\w\s]*", re.I),
    re.compile(r"liberty[\s\w]*general[\w\s]*", re.I),
    re.compile(r"magma[\w\s]*", re.I),
    re.compile(r"reliance[\s\w]*general[\w\s]*", re.I),
    re.compile(r"sbi[\s\w]*general[\w\s]*", re.I),
    re.compile(r"tata[\s\w]*aig[\w\s]*", re.I),
]

_INSURANCE_LETTER_MARKERS = re.compile(
    r"query letter|auth denial|cashless authorization|authorization no|"
    r"claim incident|pre[\s-]?auth|preauthorization|insurance company",
    re.I,
)

_POLICY_NUMBER_PATTERNS = [
    re.compile(
        r"policy\s*(?:no\.?|number|#)\s*[:.]?\s*([A-Z]?\d{5,}[A-Z0-9\-]*)",
        re.I,
    ),
    re.compile(
        r"forming part of\s+policy\s*(?:no\.?|number)\s*[:.]?\s*([A-Z]?\d{5,}[A-Z0-9\-]*)",
        re.I,
    ),
]

_POLICY_FALSE_POSITIVES = {
    "document", "schedule", "certificate", "wordings", "holder", "number",
    "being", "servicing", "issuing", "family", "health", "protector",
    "cum", "invoice", "tax", "blank", "details", "previous", "insured",
}

_POLICY_PERIOD_PATTERNS = [
    re.compile(
        r"start\s*date\s*from\s*[:.]?\s*(\d{1,2}/\d{1,2}/\d{4}).*?"
        r"end\s*date\s*till\s*midnight\s*on\s*[:.]?\s*(\d{1,2}/\d{1,2}/\d{4})",
        re.I | re.S,
    ),
    re.compile(
        r"period\s*of\s*insurance.*?start\s*date\s*[:.]?\s*from\s*[:.]?\s*(\d{1,2}/\d{1,2}/\d{4}).*?"
        r"end\s*date\s*(?:till\s*midnight\s*on\s*[:.]?\s*)?(\d{1,2}/\d{1,2}/\d{4})",
        re.I | re.S,
    ),
    re.compile(
        r"period\s*of\s*insurance.*?start\s*date\s*:\s*from\s*(\d{1,2}/\d{1,2}/\d{4}).*?"
        r"end\s*date\s*:.*?(?:on\s*)?(\d{1,2}/\d{1,2}/\d{4})",
        re.I | re.S,
    ),
    re.compile(
        r"policy\s*period\s*[:.]?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\s*(?:to|-|–)\s*"
        r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
        re.I,
    ),
    re.compile(
        r"valid(?:ity)?\s*(?:from|between)\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\s*(?:to|-|–)\s*"
        r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})",
        re.I,
    ),
]

_CLAIM_RE = re.compile(
    r"(?:claim\s*(?:incident|no|number|#)?|authorization\s*no|auth\s*incident)\s*[:.]?\s*"
    r"([0-9]{8,}[A-Z0-9.\-]*)",
    re.I,
)
_MEMBER_RE = re.compile(
    r"member\s*(?:code|id|no|number)?\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{4,})",
    re.I,
)
_MEMBER_POLICY_BASE_RE = re.compile(r"^([A-Z]?\d{5,})")

_BAD_INSURANCE_VALUES = {
    "policy_number": _POLICY_FALSE_POSITIVES | {"", "-", "—", "na", "n/a", "not available", "unknown"},
    "policy_period": {"", "-", "—", "na", "n/a", "not available", "unknown"},
    "insurance_company": {"", "-", "—"},
    "claim_incident_number": {"", "-", "—"},
}

_LETTERHEAD_PROMPT = """You are reading the FIRST PAGE of an Indian health-insurance claim letter
(query letter, denial letter, pre-auth letter, or cashless authorization letter).

Extract ONLY the insurance / TPA company name from the letterhead logo or footer.
Common examples: IFFCO-Tokio General Insurance, Star Health, HDFC ERGO, ICICI Lombard.

Reply in plain text, one line only:
INSURER: <full company name>

If you cannot read a company name, reply exactly:
INSURER: UNKNOWN
"""


def _clean_insurer_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    name = re.sub(r"^(insurer|insurance company)\s*[:.]?\s*", "", name, flags=re.I)
    name = re.split(
        r"\s+(?:query letter|policy schedule|date:|claim incident|member code)\b",
        name,
        maxsplit=1,
        flags=re.I,
    )[0]
    return name.strip(" .-")


def find_insurer_in_text(text: str) -> str:
    if not text:
        return ""
    for pat in _INSURER_NAME_PATTERNS:
        m = pat.search(text)
        if m:
            return _clean_insurer_name(m.group(0))
    return ""


def _is_valid_policy_number(val: str) -> bool:
    val = (val or "").strip().rstrip(".")
    if len(val) < 5:
        return False
    if val.lower() in _POLICY_FALSE_POSITIVES:
        return False
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-/]{4,}", val, re.I):
        return False
    if re.search(r"[\s|_\[\]{}\\]", val):
        return False
    if not re.search(r"\d", val):
        return False
    if val.isalpha():
        return False
    return True


def _extract_policy_numbers(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for pat in _POLICY_NUMBER_PATTERNS:
        for m in pat.finditer(text or ""):
            val = m.group(1).strip().rstrip(".")
            if _is_valid_policy_number(val) and val not in seen:
                seen.add(val)
                found.append(val)
    return found


def _score_policy_number(val: str, source: str) -> int:
    score = 0
    if re.match(r"^[A-Z]\d{5,}$", val, re.I):
        score += 20
    if re.match(r"^\d{6,}$", val):
        score += 10
    src = (source or "").lower()
    if "query" in src or "querr" in src:
        score += 30
    if "policy" in src:
        score += 25
    if "schedule" in src or "policy document" in src:
        score += 15
    return score


def _pick_best_policy_number(candidates: List[Tuple[str, int]]) -> str:
    ranked = sorted(
        [(v, s) for v, s in candidates if _is_valid_policy_number(v)],
        key=lambda x: (x[1], len(x[0])),
        reverse=True,
    )
    return ranked[0][0] if ranked else ""


def _extract_policy_period(text: str) -> str:
    for pat in _POLICY_PERIOD_PATTERNS:
        m = pat.search(text or "")
        if m:
            start, end = m.group(1).strip(), m.group(2).strip()
            return f"{start} to {end}"

    block = re.search(r"period\s*of\s*insurance.{0,500}", text or "", re.I | re.S)
    if block:
        snippet = block.group(0)
        start_m = re.search(
            r"start\s*date\s*(?:from\s*[:.]?\s*|:\s*from\s*)?(\d{1,2}/\d{1,2}/\d{4})",
            snippet,
            re.I,
        )
        end_m = re.search(
            r"end\s*date\s*(?:till\s*midnight\s*on\s*[:.]?\s*)?(\d{1,2}/\d{1,2}/\d{4})",
            snippet,
            re.I,
        )
        if start_m and end_m:
            return f"{start_m.group(1)} to {end_m.group(1)}"
    return ""


def extract_insurance_from_text(text: str, source: str = "") -> Dict[str, str]:
    """Regex extraction of insurance fields present in typed letter text."""
    facts: Dict[str, str] = {
        "insurance_company": find_insurer_in_text(text),
        "policy_number": "",
        "claim_incident_number": "",
        "policy_period": "",
        "member_code": "",
    }
    if not text:
        return facts

    policy_candidates = _extract_policy_numbers(text)
    if policy_candidates:
        facts["policy_number"] = _pick_best_policy_number([
            (val, _score_policy_number(val, source)) for val in policy_candidates
        ])

    period = _extract_policy_period(text)
    if period:
        facts["policy_period"] = period

    m = _CLAIM_RE.search(text)
    if m:
        facts["claim_incident_number"] = m.group(1).strip().rstrip(".")

    m = _MEMBER_RE.search(text)
    if m:
        member = m.group(1).strip()
        facts["member_code"] = member
        base = _MEMBER_POLICY_BASE_RE.match(member)
        if base and not facts["policy_number"]:
            facts["policy_number"] = base.group(1)

    return facts


def _is_policy_wording_source(filename: str, text: str) -> bool:
    name = (filename or "").lower()
    blob = (text or "").lower()
    if "wording" in name or "terms and conditions" in name:
        return True
    if re.search(r"policy\s+wording|schedule\s+of\s+benefits|general\s+terms", blob):
        return True
    return False


def _source_priority(filename: str, text: str) -> int:
    name = (filename or "").lower()
    if _is_policy_wording_source(filename, text):
        return 15
    if "query" in name or "querr" in name or _INSURANCE_LETTER_MARKERS.search(text or ""):
        return 100
    if "schedule" in name or re.search(r"tax\s+invoice|period\s+of\s+insurance", text or "", re.I):
        return 110
    if "policy" in name:
        return 90
    if "pre auth" in name or "preauth" in name:
        return 85
    return 50


def _pick_best_field(
    candidates: List[Tuple[str, int]],
    field: str,
) -> str:
    bad = _BAD_INSURANCE_VALUES.get(field, set())
    ranked = sorted(
        [(v, p) for v, p in candidates if v and v.lower() not in bad],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[0][0] if ranked else ""


def _render_page_b64(pdf_path: str, page_num: int = 1, dpi: int = 180) -> str:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num - 1]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return base64.b64encode(pix.tobytes("jpeg")).decode()
    finally:
        doc.close()


def _page_native_text(pdf_path: str, page_num: int = 1) -> str:
    doc = fitz.open(pdf_path)
    try:
        return doc[page_num - 1].get_text() or ""
    finally:
        doc.close()


def _pdf_native_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    try:
        return "".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _looks_like_insurance_letter(page_text: str) -> bool:
    return bool(_INSURANCE_LETTER_MARKERS.search(page_text or ""))


def extract_insurer_from_letterhead(pdf_path: str, page_num: int = 1) -> str:
    """Vision pass on insurance-letter page 1 to read logo/footer company name."""
    try:
        from backend.ai.llm_helpers import extract_response_text, image_input_part
        from backend.llm_client import get_openai_client
    except Exception as exc:
        print(f"⚠️ Letterhead vision unavailable: {exc}")
        return ""

    image_b64 = _render_page_b64(pdf_path, page_num)
    if not image_b64:
        return ""

    content = [
        {"type": "input_text", "text": _LETTERHEAD_PROMPT},
        image_input_part(image_b64, detail="high"),
    ]
    try:
        client = get_openai_client()
        model = __import__("os").getenv("VISION_OCR_MODEL", "gpt-4o")
        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": content}],
        )
        text = (extract_response_text(response) or "").strip()
        m = re.search(r"INSURER:\s*(.+)", text, re.I)
        if not m:
            return ""
        name = _clean_insurer_name(m.group(1))
        if not name or name.upper() == "UNKNOWN":
            return ""
        return name
    except Exception as exc:
        print(f"⚠️ Letterhead vision failed for {pdf_path} p{page_num}: {exc}")
        return ""


def enrich_insurance_facts(
    case_text: str,
    pdf_paths: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, str]:
    """
    Build best-effort insurance facts from combined case text and source PDFs.

    pdf_paths: list of (pdf_path, filename) tuples from temp files during extraction.
    """
    per_source: List[Tuple[str, Dict[str, str], int]] = []

    combined = extract_insurance_from_text(case_text, source="combined")
    per_source.append(("combined", combined, 50))

    if pdf_paths:
        for pdf_path, fname in pdf_paths:
            native = _pdf_native_text(pdf_path)
            text = native if native.strip() else ""
            if not text.strip() and case_text and fname:
                for marker in (f"({fname})", f"— vision transcription ({fname})", fname):
                    if marker in case_text:
                        start = case_text.find(marker)
                        text = case_text[max(0, start - 200): start + 12000]
                        break

            page_facts = extract_insurance_from_text(text, source=fname)
            priority = _source_priority(fname, text)
            per_source.append((fname, page_facts, priority))

            if not page_facts.get("insurance_company"):
                page1 = _page_native_text(pdf_path, 1)
                if _looks_like_insurance_letter(page1):
                    name = extract_insurer_from_letterhead(pdf_path, 1)
                    if name:
                        page_facts["insurance_company"] = name
                        print(f"✅ Insurance letterhead OCR: {name}")

    result = {
        "insurance_company": "",
        "policy_number": "",
        "claim_incident_number": "",
        "policy_period": "",
        "member_code": "",
    }
    for field in result:
        candidates = []
        for src, facts, priority in per_source:
            field_priority = priority
            if field == "policy_period" and "policy" in (src or "").lower():
                field_priority = max(priority, 110)
            candidates.append((facts.get(field, ""), field_priority))
        result[field] = _pick_best_field(candidates, field)

    # Policy numbers: vote across per-source extractions with source weighting
    policy_candidates: List[Tuple[str, int]] = []
    for src, facts, priority in per_source:
        val = facts.get("policy_number", "")
        if _is_valid_policy_number(val):
            policy_candidates.append((val, priority + _score_policy_number(val, src)))
    best_policy = _pick_best_policy_number(policy_candidates)
    if best_policy:
        result["policy_number"] = best_policy

    return result


def _should_overwrite_insurance_field(field: str, current: str, extracted: str) -> bool:
    if not extracted:
        return False
    cur = str(current or "").strip()
    bad = _BAD_INSURANCE_VALUES.get(field, set())
    if field == "policy_number":
        if not _is_valid_policy_number(extracted):
            return False
        if _is_valid_policy_number(cur) and not _should_overwrite_insurance_field_score(cur, extracted):
            return False
    if not cur or cur.lower() in bad:
        return True
    if field == "policy_number" and cur.lower() in _POLICY_FALSE_POSITIVES:
        return True
    if field == "policy_period" and not cur:
        return True
    return False


def _should_overwrite_insurance_field_score(current: str, extracted: str) -> bool:
    """Prefer member-code style policy IDs over noisy OCR fragments."""
    def score(val: str) -> int:
        s = 0
        if re.fullmatch(r"[A-Z]\d{6,}(?:-\d+-\d+)?", val, re.I):
            s += 50
        if re.fullmatch(r"[A-Z]\d{5,}", val, re.I):
            s += 30
        if re.search(r"[\s|_\[\]{}\\]", val):
            s -= 40
        return s

    return score(extracted) > score(current)


def merge_insurance_into_result(result: dict, facts: Dict[str, str]) -> dict:
    """Fill or correct insurance_details from extracted facts."""
    ins = result.setdefault("insurance_details", {})
    mapping = {
        "insurance_company": facts.get("insurance_company", ""),
        "policy_number": facts.get("policy_number", ""),
        "policy_period": facts.get("policy_period", ""),
        "claim_incident_number": facts.get("claim_incident_number", ""),
    }
    for key, val in mapping.items():
        if val and _should_overwrite_insurance_field(key, str(ins.get(key) or ""), val):
            ins[key] = val
    return result
