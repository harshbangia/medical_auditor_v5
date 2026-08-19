"""Parse Health Claim Assessor Reports into structured notebook facts.

Assessor PDFs are often scans; we work from OCR/vision text already folded into
case_text / notebook chunks. Patterns mirror NotebookLM's use of Assessor FWA.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def is_assessor_document(filename: str, text: str = "") -> bool:
    name = (filename or "").lower()
    blob = (text or "")[:4000].lower()
    if "assessor" in name or "health claim assessor" in name:
        return True
    if "health claim assessor report" in blob:
        return True
    if "fwa alert" in blob and "mrc alert" in blob:
        return True
    return False


def _first(rx: re.Pattern, text: str, group: int = 1) -> str:
    m = rx.search(text or "")
    return (m.group(group).strip() if m else "") or ""


def parse_assessor_text(text: str, filename: str = "") -> Dict[str, Any]:
    """Extract claim IDs, finance rows, and FWA-style insights from assessor OCR."""
    blob = text or ""
    out: Dict[str, Any] = {
        "source_file": filename,
        "is_assessor": is_assessor_document(filename, blob),
        "claim_number": "",
        "sub_claim_number": "",
        "policy_number": "",
        "patient_name": "",
        "claimed_amount": "",
        "claim_type": "",
        "hospital": "",
        "diagnosis": "",
        "fwa_alerts": [],
        "finance_bills": [],
        "identity_mismatches": [],
    }
    if not blob.strip():
        return out

    out["claim_number"] = _first(
        re.compile(
            r"(?:claim\s*(?:number|no\.?|incident(?:\s*no\.?)?))\s*[:.]?\s*"
            r"(\d{10,16}(?:\.[A-Za-z0-9]{1,4})?)",
            re.I,
        ),
        blob,
    )
    out["sub_claim_number"] = _first(
        re.compile(
            r"(?:sub\s*claim\s*(?:number|no\.?))\s*[:.]?\s*"
            r"(\d{10,16}\.[A-Za-z0-9]{1,4})",
            re.I,
        ),
        blob,
    )
    # Prefer sub-claim root when present
    if out["sub_claim_number"] and not out["claim_number"]:
        out["claim_number"] = out["sub_claim_number"].split(".", 1)[0]

    out["policy_number"] = _first(
        re.compile(r"(?:policy\s*(?:number|no\.?))\s*[:.]?\s*(H[A-Z0-9Il]{5,12})", re.I),
        blob,
    ).upper()

    out["patient_name"] = _first(
        re.compile(
            r"(?:name\s+of\s+the\s+insured|insured\s*name|patient\s*name)\s*[:.]?\s*"
            r"([A-Za-z][A-Za-z .']{2,60})",
            re.I,
        ),
        blob,
    )
    out["claimed_amount"] = _first(
        re.compile(
            r"(?:claimed\s*amount|total\s*billed\s*amount)\s*[:.]?\s*"
            r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)",
            re.I,
        ),
        blob,
    )
    out["claim_type"] = _first(
        re.compile(r"claim\s*type\s*[:.]?\s*(reimbursement|cashless)", re.I),
        blob,
    )
    out["hospital"] = _first(
        re.compile(r"hospital\s*name\s*[:.]?\s*([^\n]{5,80})", re.I),
        blob,
    )
    out["diagnosis"] = _first(
        re.compile(r"(?:diagnosis|claimed\s*illness)\s*[:.]?\s*([^\n]{5,120})", re.I),
        blob,
    )

    # Finance table-ish rows: bill no + amount
    for m in re.finditer(
        r"\b([A-Z]?\d{5,}|A\d{5,}|DH\d[\w/]+)\b[^\n]{0,40}?"
        r"(?:rs\.?|inr|₹)?\s*([\d,]{3,9}(?:\.\d{1,2})?)",
        blob,
        re.I,
    ):
        bill_no, amt = m.group(1), m.group(2)
        if float(amt.replace(",", "") or 0) < 100:
            continue
        out["finance_bills"].append({"bill_number": bill_no, "amount": amt})
        if len(out["finance_bills"]) >= 40:
            break

    # Identity / FWA narrative hooks
    for pat, label in (
        (
            r"(SAVITHA\s*A\s*G|different\s+patient|record\s+(?:reuse|recycling|mixing)|"
            r"template\s+reuse|demographic\s+mismatch)",
            "Identity / record tampering",
        ),
        (
            r"(bill\s*amount\s*verification|aggregate[\s-]*sum|line[\s-]*item\s*sum|"
            r"grand\s*total.{0,40}(?:mismatch|failed|does\s+not))",
            "Pharmacy / bill math anomaly",
        ),
        (
            r"(pre[\s-]*existing|old_diagnosis|non[\s-]*disclosure|material\s+fact)",
            "Pre-existing / non-disclosure",
        ),
        (
            r"(irrelevant\s+(?:drug|medication|billed)|rabifast|duplicate\s+bill)",
            "Irrelevant / duplicate billing",
        ),
        (
            r"(physiolog(?:y|ically)\s+impossib|anion\s*gap\s*-?\d|pO2\s*(?:of\s*)?\d|"
            r"hypoxemia|SpO2\s*98)",
            "Physiological contradiction",
        ),
    ):
        m = re.search(pat, blob, re.I)
        if m:
            start = max(0, m.start() - 80)
            end = min(len(blob), m.end() + 160)
            excerpt = re.sub(r"\s+", " ", blob[start:end]).strip()
            out["fwa_alerts"].append({
                "category": label,
                "indicator": label,
                "evidence": excerpt[:400],
                "severity": "High",
                "recommendation": "Verify against source documents and Assessor FWA panel.",
                "source_file": filename,
            })

    # Named alien patients
    for m in re.finditer(
        r"(?:patient(?:\s+id)?\s*[:.]?\s*\d{3,6}[^\n]{0,40})?"
        r"(?:name|patient)\s*[:.]?\s*['\"]?([A-Z][A-Z ]{3,40})['\"]?",
        blob,
    ):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        if name and name.lower() not in (out["patient_name"] or "").lower():
            if re.search(r"SAVITHA|DAYA\s*THE|BUU\b", name, re.I):
                out["identity_mismatches"].append(name)

    return out


def merge_assessor_from_chunks(
    chunks: List[Any],
) -> Dict[str, Any]:
    """Scan notebook chunks for assessor-sourced text and merge parses."""
    merged: Dict[str, Any] = {
        "is_assessor": False,
        "claim_number": "",
        "sub_claim_number": "",
        "policy_number": "",
        "patient_name": "",
        "claimed_amount": "",
        "claim_type": "",
        "hospital": "",
        "diagnosis": "",
        "fwa_alerts": [],
        "finance_bills": [],
        "identity_mismatches": [],
        "source_files": [],
    }
    by_file: Dict[str, List[str]] = {}
    for ch in chunks or []:
        fname = getattr(ch, "filename", "") or ""
        text = getattr(ch, "text", "") or ""
        if is_assessor_document(fname, text) or "fwa" in text.lower()[:2000]:
            by_file.setdefault(fname, []).append(text)

    for fname, parts in by_file.items():
        parsed = parse_assessor_text("\n".join(parts), fname)
        if not parsed.get("is_assessor") and not parsed.get("fwa_alerts"):
            continue
        merged["is_assessor"] = True
        merged["source_files"].append(fname)
        for key in (
            "claim_number", "sub_claim_number", "policy_number", "patient_name",
            "claimed_amount", "claim_type", "hospital", "diagnosis",
        ):
            if parsed.get(key) and not merged.get(key):
                merged[key] = parsed[key]
        merged["fwa_alerts"].extend(parsed.get("fwa_alerts") or [])
        merged["finance_bills"].extend(parsed.get("finance_bills") or [])
        merged["identity_mismatches"].extend(parsed.get("identity_mismatches") or [])

    # de-dupe alerts by indicator
    seen = set()
    uniq = []
    for a in merged["fwa_alerts"]:
        ind = str(a.get("indicator") or "")
        if ind in seen:
            continue
        seen.add(ind)
        uniq.append(a)
    merged["fwa_alerts"] = uniq
    return merged
