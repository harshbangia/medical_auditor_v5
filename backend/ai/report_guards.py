"""Assembly-stage guards: the final validation layer before a report is rendered.

This module enforces the v6 principle: *ground everything, fabricate nothing,
and abstain loudly when evidence is missing.* It runs AFTER the LLM audit and
deterministic enrichment, and it can only ever remove or downgrade unsupported
content — never invent new content.

Responsibilities:
  1. strip_fabricated_financials  — blank invented money when no real bill exists
  2. enforce_date_plausibility    — drop out-of-window dates from EVERY field
  3. canonicalize_entities        — fix OCR'd insurer / hospital names
  4. compute_report_confidence    — set report_confidence + manual_review_required
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.utils.claim_details_extractor import (
    _claim_reference_year,
    _parse_date_year,
    _canonical_date_key,
    _score_hospital_name,
    _EMERGENCY_ADMISSION_MARKERS,
    _PLANNED_MARKERS,
)

# ---------------------------------------------------------------------------
# 1. Financial anti-fabrication
# ---------------------------------------------------------------------------

_MONEY_TOKEN = re.compile(
    r"(?:rs\.?|inr|₹)\s*[\d,]+(?:\.\d{1,2})?|\b\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?\b",
    re.I,
)
_BILL_CONTEXT = re.compile(
    r"\bbill\b|invoice|receipt|amount\s+(?:paid|claimed|payable|billed)|"
    r"total\s+(?:amount|charges?|bill)|grand\s*total|estimate|package\s+cost|"
    r"room\s+rent|pharmacy|consumables?|ot\s*charges?|surgeon|nursing\s+charges?",
    re.I,
)

_FINANCIAL_NUMERIC_FIELDS = (
    "total_hospital_bill", "non_payable_amount", "net_claimable_amount",
    "recommended_approval_amount", "patient_liability", "amount_saved",
    "savings_percentage",
)

_NOT_AVAILABLE_MSG = (
    "Financial review not available — no itemised hospital bill, invoice, or "
    "billed amount was found in the uploaded documents. Provide the final bill "
    "to enable a financial audit. (Figures were NOT estimated to avoid fabrication.)"
)


def has_real_bill_evidence(case_text: str, claim_facts: Optional[dict]) -> bool:
    """True only when a genuine billed amount can be traced to the documents.

    We trust two sources: (a) the deterministic bill total extracted upstream,
    and (b) a money token that co-occurs with billing context in the case text.
    An LLM-populated `financial_review` is explicitly NOT treated as evidence.
    """
    claim_facts = claim_facts or {}
    if str(claim_facts.get("total_hospital_bill") or "").strip():
        return True

    text = case_text or ""
    for m in _MONEY_TOKEN.finditer(text):
        window = text[max(0, m.start() - 120): m.end() + 120]
        if _BILL_CONTEXT.search(window):
            return True
    return False


def strip_fabricated_financials(
    result: dict, case_text: str, claim_facts: Optional[dict]
) -> bool:
    """Blank every financial figure unless a real bill is in evidence.

    Returns True if financials were stripped (i.e. none were available).
    """
    if has_real_bill_evidence(case_text, claim_facts):
        return False

    fin = result.setdefault("financial_review", {})
    for key in _FINANCIAL_NUMERIC_FIELDS:
        fin[key] = ""
    fin["status"] = "not_available"
    fin["note"] = _NOT_AVAILABLE_MSG

    # claim_savings is where the LLM's invented line items get rendered from
    result["claim_savings"] = {
        "total_claim_amount": "",
        "admissible_amount": "",
        "amount_saved": "",
        "savings_percentage": "",
        "highlight": False,
        "line_items": [],
        "status": "not_available",
        "notes": _NOT_AVAILABLE_MSG,
    }
    result["claim_savings_line_items"] = []

    # Remove any money amounts the LLM parked in the billing audit
    tba = result.setdefault("treatment_billing_audit", {})
    for key in ("excluded_items_billed",):
        val = str(tba.get(key) or "")
        if _MONEY_TOKEN.search(val):
            tba[key] = _MONEY_TOKEN.sub("", val).strip(" ,;-") or ""

    gaps = result.setdefault("documentation_gaps", [])
    gap_msg = "Itemised hospital bill / invoice not provided — financial audit could not be performed."
    if not any("bill" in str(g).lower() and "financial" in str(g).lower() for g in gaps):
        gaps.append(gap_msg)

    # Scrub stale financial claims from summary bullets and the inference paragraph
    # (these were assembled earlier, before this guard ran).
    summary = result.get("report_summary") or []
    if isinstance(summary, list):
        result["report_summary"] = [
            b for b in summary
            if not (isinstance(b, str) and _MONEY_TOKEN.search(b) and _FIN_WORDS.search(b))
        ]
    for key in ("inference", "auditor_conclusion"):
        text = str(result.get(key) or "")
        if text and _MONEY_TOKEN.search(text):
            result[key] = _scrub_financial_sentences(text)
    return True


_FIN_WORDS = re.compile(
    r"saved|admissible|non[\s-]?payable|savings|claim\s+amount|deduction|"
    r"₹|\brs\.?\b|\binr\b|payable",
    re.I,
)
# Abbreviations whose trailing '.' must not be treated as a sentence boundary.
_ABBREV = {"Rs.": "Rs", "No.": "No", "Dr.": "Dr", "Mr.": "Mr"}


def _scrub_financial_sentences(text: str) -> str:
    """Remove any sentence that pairs a money amount with a financial term.

    Sentence-splitting protects abbreviations like 'Rs.' so their period is not
    misread as a full stop (which previously left half of 'Rs. 20,000' behind).
    """
    protected = text
    for abbr, token in _ABBREV.items():
        protected = protected.replace(abbr, token)
    sentences = re.split(r"(?<=[.!?])\s+", protected)
    kept = []
    for s in sentences:
        restored = s
        for abbr, token in _ABBREV.items():
            restored = restored.replace(token, abbr)
        if _MONEY_TOKEN.search(restored) and _FIN_WORDS.search(restored):
            continue
        kept.append(restored)
    return " ".join(kept).strip()


# ---------------------------------------------------------------------------
# 2. Date plausibility on EVERY field (regex + LLM origin alike)
# ---------------------------------------------------------------------------

# A claim's dates (consult, admission, discharge, bill, follow-up) cluster within
# ~1 year. A date 2+ years from the claim anchor is almost always an OCR misread
# (e.g. handwritten "26" read as "23"). Window is intentionally tight; legitimate
# far-back first-consult dates are rare and are flagged for review anyway.
_DATE_WINDOW_YEARS = int(os.getenv("DATE_WINDOW_YEARS", "1"))
_ABSOLUTE_MIN_YEAR = 2015
_DATE_TOKEN = re.compile(
    r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b|"
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{2,4})\b|"
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{2,4})\b",
    re.I,
)


def _year_is_plausible(year: Optional[int], ref_year: Optional[int]) -> bool:
    if year is None:
        return True  # no year → cannot judge; leave as-is
    now_year = datetime.now().year
    if year < _ABSOLUTE_MIN_YEAR or year > now_year + 1:
        return False
    if ref_year:
        return abs(year - ref_year) <= _DATE_WINDOW_YEARS
    return True


def _date_token_plausible(token: str, ref_year: Optional[int]) -> bool:
    return _year_is_plausible(_parse_date_year(token, ref_year), ref_year)


def _scrub_dates_in_text(value: str, ref_year: Optional[int]) -> str:
    """Remove implausible date tokens from a free-text value, keep the rest."""
    if not value:
        return value

    def _repl(match: "re.Match") -> str:
        token = match.group(0)
        return token if _date_token_plausible(token, ref_year) else "[date unreadable]"

    return _DATE_TOKEN.sub(_repl, value)


def _impossible_year(year: Optional[int]) -> bool:
    """A date that cannot belong to any real claim (far future or ancient)."""
    if year is None:
        return False
    now_year = datetime.now().year
    return year < _ABSOLUTE_MIN_YEAR or year > now_year + 1


def enforce_date_plausibility(result: dict, case_text: str) -> int:
    """Remove only implausible dates, and NEVER delete a corroborated one.

    Two classes of removal:
      * IMPOSSIBLE dates (year < 2015 or in the future) — always dropped.
      * Out-of-window OUTLIERS in LLM free text (timeline / clinical_findings)
        that are NOT corroborated by the extractor's document-of-record dates.

    Primary claim dates chosen by the deterministic extractor are trusted: they
    already passed document-type-aware validation and drive the discrepancy
    table. Deleting them (as an earlier version did) produced empty admission
    fields and a summary that contradicted the claim block — worse than the
    conflict it tried to hide.
    """
    ref_year = _claim_reference_year(case_text)
    claim = result.get("claim_details") or {}
    primary_fields = (
        "consultation_date", "date_of_admission",
        "date_of_discharge", "proposed_hospitalization_date",
    )

    # Corroborated set = the extractor's picks (kept unless truly impossible).
    trusted_keys = set()
    for field in primary_fields:
        val = str(claim.get(field) or "").strip()
        if val and not _impossible_year(_parse_date_year(val, ref_year)):
            key = _canonical_date_key(val, ref_year)
            if key:
                trusted_keys.add(key)

    def _token_is_bad(token: str) -> bool:
        year = _parse_date_year(token, ref_year)
        if _impossible_year(year):
            return True
        key = _canonical_date_key(token, ref_year)
        if key and key in trusted_keys:
            return False  # corroborated by a document-of-record date → keep
        return not _year_is_plausible(year, ref_year)  # uncorroborated outlier

    removed = 0

    # 1) Primary claim dates: only remove the truly impossible.
    for field in primary_fields:
        val = str(claim.get(field) or "").strip()
        if val and _impossible_year(_parse_date_year(val, ref_year)):
            claim[field] = ""
            claim[f"{field}_source"] = ""
            removed += 1

    # 2) Timeline free-text dates: blank bad ones, keep the event.
    for row in result.get("timeline") or []:
        if not isinstance(row, dict):
            continue
        d = str(row.get("date") or "").strip()
        if d and _token_is_bad(d):
            row["date"] = ""
            row.setdefault("notes", []).append("date removed: implausible/uncorroborated")
            removed += 1

    # 3) clinical_findings: dates hide inside value/comment (the 18/01/2023 leak).
    for row in result.get("clinical_findings") or []:
        if not isinstance(row, dict):
            continue
        for key in ("value", "comment", "parameter"):
            original = str(row.get(key) or "")

            def _repl(match: "re.Match") -> str:
                tok = match.group(0)
                return "[date unreadable]" if _token_is_bad(tok) else tok

            scrubbed = _DATE_TOKEN.sub(_repl, original)
            if scrubbed != original:
                row[key] = scrubbed
                removed += 1

    # 4) all_document_dates provenance: drop only impossible dates.
    kept = []
    for entry in (claim.get("all_document_dates") or []):
        if isinstance(entry, dict):
            v = str(entry.get("value") or "")
            if v and _impossible_year(_parse_date_year(v, ref_year)):
                removed += 1
                continue
        kept.append(entry)
    if claim.get("all_document_dates") is not None:
        claim["all_document_dates"] = kept

    return removed


def correct_nature_of_admission(result: dict, case_text: str) -> None:
    """Force Planned/Elective on pre-auth/planned cases, overriding an LLM guess.

    A pre-authorisation request or a query letter with a *proposed* date of
    hospitalisation is planned/elective by definition. The LLM sometimes writes
    "Emergency" after seeing an ICU/HDU room — this corrects that unless the
    documents contain an explicit emergency-admission marker.
    """
    claim = result.get("claim_details") or {}
    blob = case_text or ""
    has_planned = bool(_PLANNED_MARKERS.search(blob)) or bool(
        str(claim.get("proposed_hospitalization_date") or "").strip()
    )
    has_emergency = bool(_EMERGENCY_ADMISSION_MARKERS.search(blob))
    current = str(claim.get("nature_of_admission") or "").strip()
    if has_planned and not has_emergency and current.lower() != "planned / elective":
        claim["nature_of_admission"] = "Planned / Elective"


# ---------------------------------------------------------------------------
# 3. Entity canonicalisation (OCR clean-up)
# ---------------------------------------------------------------------------

_LIMITED_OCR = re.compile(r"\bL[il1|]?[mwMW][il1|]?T[eE]D\b", re.I)
_HOSPITAL_ADDRESS_TAIL = re.compile(
    r"\s+(?:near|opp\.?|opposite|behind|beside|adjacent|next\s+to|"
    r"in\s+front\s+of|at|,)\b.*$",
    re.I,
)
_OCR_WORD_FIXES = {
    r"\bMultispeciatity\b": "Multispeciality",
    r"\bMultispecialty\b": "Multispeciality",
    r"\bHospita[l1]\b": "Hospital",
    r"\bLiwITED\b": "LIMITED",
}


def _fix_ocr_words(value: str) -> str:
    for pat, repl in _OCR_WORD_FIXES.items():
        value = re.sub(pat, repl, value)
    return value


def canonicalize_entities(result: dict) -> None:
    """Repair common OCR corruptions in insurer and hospital names."""
    ins = result.get("insurance_details") or {}
    company = str(ins.get("insurance_company") or "").strip()
    if company:
        company = _LIMITED_OCR.sub("LIMITED", company)
        company = _fix_ocr_words(company)
        company = re.sub(r"\s{2,}", " ", company).strip()
        ins["insurance_company"] = company

    claim = result.get("claim_details") or {}
    hospital = str(claim.get("hospital") or "").strip()
    if hospital:
        cleaned = _dedupe_hospital_name(hospital)
        cleaned = _HOSPITAL_ADDRESS_TAIL.sub("", cleaned).strip(" ,.-")
        cleaned = _fix_ocr_words(cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        # Only keep the cleaned form if it still looks like a real hospital name
        if cleaned and _score_hospital_name(cleaned) > 0:
            claim["hospital"] = cleaned
        elif _score_hospital_name(hospital) <= 0:
            claim["hospital"] = ""


_FACILITY_KEYWORD = re.compile(
    r"\b(?:hospital|clinic|nursing\s+home|medical\s+centre|medical\s+center|institute|healthcare)\b",
    re.I,
)
_FACILITY_FILLER = {
    "hospital", "clinic", "nursing", "home", "medical", "centre", "center",
    "institute", "healthcare", "health", "care", "multispeciality",
    "multispecialty", "speciality", "specialty", "super", "the", "and", "&",
}


def _dedupe_hospital_name(name: str) -> str:
    """Collapse a name repeated by concatenated sources.

    'SHRI HARI MULTISPECIALITY HOSPITAL Shri Hari Multispeciality Hospital'
    → 'SHRI HARI MULTISPECIALITY HOSPITAL'. Only truncates when the tail is a
    repetition of the head (its distinctive words already appear), so genuine
    long names ('… Hospital and Medical Research Institute') are preserved.
    """
    m = _FACILITY_KEYWORD.search(name)
    if not m:
        return name
    head = name[: m.end()].strip()
    tail = name[m.end():].strip(" ,.-")
    if not tail:
        return name
    head_words = {w for w in re.findall(r"[a-z]+", head.lower())}
    tail_words = {w for w in re.findall(r"[a-z]+", tail.lower())}
    distinctive_tail = tail_words - _FACILITY_FILLER
    if distinctive_tail and distinctive_tail <= head_words:
        return head
    return name


# ---------------------------------------------------------------------------
# 4. Report confidence + abstention
# ---------------------------------------------------------------------------

def compute_report_confidence(
    result: dict,
    source_summaries: Optional[List[dict]],
    guideline_selection: Optional[dict],
    financials_stripped: bool,
) -> None:
    """Set result['report_confidence'] and result['manual_review_required']."""
    reasons: List[str] = []
    score = 100

    diagnosis = str((result.get("claim_details") or {}).get("diagnosis") or "").strip()
    if not diagnosis or diagnosis.lower() in {"unknown", "not documented", "—", "-"}:
        reasons.append("Primary diagnosis could not be reliably read from the documents.")
        score -= 45

    # Handwriting / scan presence lowers ceiling
    handwriting_pages = 0
    for s in source_summaries or []:
        if isinstance(s, dict) and s.get("contains_handwriting_or_scan"):
            handwriting_pages += 1
    if handwriting_pages:
        reasons.append(
            f"{handwriting_pages} document(s) relied on handwriting/scan transcription — verify key facts."
        )
        score -= 15

    # Guideline selection confidence
    gsel = guideline_selection or {}
    gconf = gsel.get("confidence")
    if gsel.get("source") != "user" and isinstance(gconf, (int, float)) and gconf < 0.6:
        reasons.append(
            f"Guideline auto-match confidence is low ({int(gconf * 100)}%) — confirm the correct protocol."
        )
        score -= 25

    if result.get("date_discrepancies"):
        reasons.append("Conflicting dates across documents require reconciliation.")
        score -= 10

    if financials_stripped:
        reasons.append("No itemised bill provided — financial review is unavailable.")
        score -= 5

    if score >= 80:
        confidence = "High"
    elif score >= 55:
        confidence = "Medium"
    else:
        confidence = "Low"

    manual_review = confidence == "Low" or bool(
        not diagnosis
        or (gsel.get("source") != "user" and isinstance(gconf, (int, float)) and gconf < 0.5)
    )

    result["report_confidence"] = confidence
    result["manual_review_required"] = manual_review
    result["manual_review_reasons"] = reasons


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def apply_report_guards(
    result: dict,
    case_text: str,
    claim_facts: Optional[dict] = None,
    source_summaries: Optional[List[dict]] = None,
    guideline_selection: Optional[dict] = None,
) -> dict:
    """Run every assembly-stage guard in order. Safe to call once, at the end."""
    if not isinstance(result, dict) or result.get("error"):
        return result

    financials_stripped = strip_fabricated_financials(result, case_text, claim_facts)
    enforce_date_plausibility(result, case_text)
    correct_nature_of_admission(result, case_text)
    canonicalize_entities(result)
    compute_report_confidence(
        result, source_summaries, guideline_selection, financials_stripped
    )
    if guideline_selection:
        result["guideline_selection"] = guideline_selection
    return result
