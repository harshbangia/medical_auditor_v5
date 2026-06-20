"""Extract insurance facts from claim documents.

Typed insurer letters often embed the company name only in a logo or footer
image — PyMuPDF native text extraction misses it entirely. This module:
  1. Regex-parses policy / claim / member IDs from typed letter text.
  2. Runs a lightweight vision pass on insurance-letter page 1 when the company
     name is still missing.
"""

import base64
import re
from typing import Any, Dict, List, Optional, Tuple

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

_POLICY_RE = re.compile(
    r"(?:policy\s*(?:no|number|#)?\s*[:.]?\s*)([A-Z0-9][A-Z0-9\-/]{4,})",
    re.I,
)
_CLAIM_RE = re.compile(
    r"(?:claim\s*(?:incident|no|number|#)?|authorization\s*no|auth\s*incident)\s*[:.]?\s*"
    r"([0-9]{8,}[A-Z0-9.\-]*)",
    re.I,
)
_MEMBER_RE = re.compile(
    r"member\s*(?:code|id|no|number)?\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/]{4,})",
    re.I,
)

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
    return name.strip(" .-")


def find_insurer_in_text(text: str) -> str:
    if not text:
        return ""
    for pat in _INSURER_NAME_PATTERNS:
        m = pat.search(text)
        if m:
            return _clean_insurer_name(m.group(0))
    return ""


def extract_insurance_from_text(text: str) -> Dict[str, str]:
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

    for pat in (_POLICY_RE,):
        m = pat.search(text)
        if m:
            facts["policy_number"] = m.group(1).strip()
            break

    m = _CLAIM_RE.search(text)
    if m:
        facts["claim_incident_number"] = m.group(1).strip().rstrip(".")

    m = _MEMBER_RE.search(text)
    if m:
        facts["member_code"] = m.group(1).strip()

    return facts


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
    facts = extract_insurance_from_text(case_text)

    if pdf_paths:
        for pdf_path, _fname in pdf_paths:
            page1 = _page_native_text(pdf_path, 1)
            page_facts = extract_insurance_from_text(page1)
            for key, val in page_facts.items():
                if val and not facts.get(key):
                    facts[key] = val

            if facts.get("insurance_company"):
                continue
            if _looks_like_insurance_letter(page1):
                name = extract_insurer_from_letterhead(pdf_path, 1)
                if name:
                    facts["insurance_company"] = name
                    print(f"✅ Insurance letterhead OCR: {name}")

    return facts


def merge_insurance_into_result(result: dict, facts: Dict[str, str]) -> dict:
    """Fill blank insurance_details fields from extracted facts."""
    ins = result.setdefault("insurance_details", {})
    mapping = {
        "insurance_company": facts.get("insurance_company", ""),
        "policy_number": facts.get("policy_number", ""),
        "policy_period": facts.get("policy_period", ""),
        "claim_incident_number": facts.get("claim_incident_number", ""),
    }
    for key, val in mapping.items():
        if val and not str(ins.get(key) or "").strip():
            ins[key] = val
    return result
