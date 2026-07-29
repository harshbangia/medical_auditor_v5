"""Per-document analysis summary for audit reports (no extra LLM call)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _block_for_file(case_text: str, filename: str) -> str:
    if not case_text or not filename:
        return ""
    pat = re.compile(
        rf"=== Source document:\s*{re.escape(filename)}\s*===(.*?)(?=\n=== Source document:|\Z)",
        re.I | re.S,
    )
    m = pat.search(case_text)
    return (m.group(1) or "").strip() if m else ""


def _snippet(blob: str, limit: int = 220) -> str:
    one = re.sub(r"\s+", " ", (blob or "").strip())
    if len(one) <= limit:
        return one
    return one[: limit - 3].rsplit(" ", 1)[0] + "..."


def _classify_doc(filename: str, blob: str) -> str:
    low = f"{filename} {blob[:2000]}".lower()
    if re.search(r"query\s*letter|repudiation|denial", low):
        return "Insurer query / denial letter"
    if re.search(r"cashless|pre[\s-]?auth", low):
        return "Pre-authorization form"
    if re.search(r"discharge\s+summary", low):
        return "Discharge summary"
    if re.search(r"\bbill\b|invoice|final bill", low):
        return "Hospital bill / invoice"
    if re.search(r"investigation|lab|biochemistry|pathology", low):
        return "Laboratory / investigation report"
    if re.search(r"ct\b|mri|hrct|x-?ray|radiolog", low):
        return "Radiology report"
    if re.search(r"indoor\s+case|treatment\s+sheet|nursing", low):
        return "Indoor case papers"
    if re.search(r"prescription|rx\b", low):
        return "Prescription / clinical note"
    return "Clinical / other document"


def build_document_analysis(
    case_text: str = "",
    source_summaries: Optional[List[dict]] = None,
    claim_facts: Optional[dict] = None,
    result: Optional[dict] = None,
) -> List[Dict[str, str]]:
    """Build one row per uploaded PDF: what was read and what it contributed."""
    claim_facts = claim_facts or {}
    result = result or {}
    rows: List[Dict[str, str]] = []
    all_dates = claim_facts.get("all_document_dates") or []
    seen_files = set()

    for src in source_summaries or []:
        if not isinstance(src, dict):
            continue
        fname = str(src.get("filename") or "").strip()
        if not fname or fname in seen_files:
            continue
        seen_files.add(fname)
        blob = _block_for_file(case_text, fname)
        doc_type = _classify_doc(fname, blob)
        read_mode = (
            "Typed text + handwriting/scan (vision OCR)"
            if src.get("contains_handwriting_or_scan")
            else "Typed / native PDF text"
        )
        date_bits = [
            f"{e.get('field_label', e.get('field', 'Date'))}: {e.get('value')}"
            for e in all_dates
            if isinstance(e, dict) and e.get("source_file") == fname and e.get("value")
        ]
        key_facts = "; ".join(date_bits[:4]) if date_bits else _snippet(blob, 180)
        if not key_facts:
            key_facts = "No structured fields extracted — review OCR coverage"

        audit_note = []
        if src.get("contains_handwriting_or_scan"):
            audit_note.append("Handwritten/scanned pages transcribed for audit")
        if re.search(r"bill|invoice|grand\s*total", blob, re.I):
            audit_note.append("Financial amounts present")
        if re.search(r"diagnosis|impression|clinical", blob, re.I):
            audit_note.append("Diagnosis/clinical content present")
        if not audit_note:
            audit_note.append("Supporting documentation")

        rows.append({
            "document": fname,
            "document_type": doc_type,
            "how_read": read_mode,
            "key_content": key_facts,
            "audit_use": "; ".join(audit_note),
        })

    return rows


def merge_document_analysis_into_result(
    result: dict,
    case_text: str = "",
    source_summaries: Optional[List[dict]] = None,
    claim_facts: Optional[dict] = None,
) -> dict:
    ledger = (claim_facts or {}).get("case_facts_ledger") or result.get("case_facts_ledger")
    rows = build_document_analysis(case_text, source_summaries, claim_facts, result)
    if ledger and isinstance(ledger.get("per_document_summaries"), list):
        summary_by_file = {
            str(d.get("filename") or ""): d
            for d in ledger["per_document_summaries"]
            if isinstance(d, dict)
        }
        for row in rows:
            fname = row.get("document") or ""
            extra = summary_by_file.get(fname)
            if not extra:
                continue
            blob = _block_for_file(case_text, fname)
            summary = str(extra.get("summary") or "").strip()
            # Drop LLM-invented diagnoses not present in the source text (e.g. meningioma on OT notes)
            if summary and _summary_grounded(summary, blob):
                row["key_content"] = summary
            nf = extra.get("notable_findings") or []
            grounded_nf = [
                str(x) for x in nf[:4]
                if str(x).strip() and _summary_grounded(str(x), blob)
            ]
            if grounded_nf:
                row["audit_use"] = "; ".join(grounded_nf)
    if rows:
        result["document_analysis"] = rows
    return result


_HALLUCINATION_DX_RE = re.compile(
    r"\b(?:meningioma|glioma|metastasis|aneurysm\s+clip|craniotomy\s+for\s+tumor)\b",
    re.I,
)


def _summary_grounded(summary: str, source_blob: str) -> bool:
    """Reject summaries that invent diagnoses absent from the OCR'd source page."""
    if not summary:
        return False
    blob = source_blob or ""
    for m in _HALLUCINATION_DX_RE.finditer(summary):
        term = m.group(0)
        if term.lower() not in blob.lower():
            return False
    return True