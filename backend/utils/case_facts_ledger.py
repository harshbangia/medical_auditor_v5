"""Merge per-document map outputs into a single CaseFacts ledger."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.ai.case_profiler import normalize_str_list, stringify_item
from backend.utils.demographics_normalizer import (
    normalize_age,
    normalize_bill_amount,
    normalize_hospital_name,
    normalize_patient_name,
    normalize_policy_number,
    score_name_quality,
)

# Higher number = preferred source for identity / diagnosis fields
_DOC_PRIORITY = {
    "discharge_summary": 100,
    "discharge": 100,
    "preauth": 80,
    "clinical": 70,
    "prescription": 65,
    "indoor": 60,
    "bill": 50,
    "lab": 40,
    "radiology": 40,
    "query_letter": 20,
    "other": 10,
}

# Document types that must NOT supply claim diagnosis (imaging impressions)
_DX_EXCLUDED_TYPES = frozenset({"radiology", "lab", "lab_report", "bill"})


def _doc_priority(doc_type: str) -> int:
    key = (doc_type or "other").strip().lower().replace(" ", "_")
    if key in _DOC_PRIORITY:
        return _DOC_PRIORITY[key]
    for prefix, score in _DOC_PRIORITY.items():
        if prefix in key:
            return score
    return 5


def _norm(s: Any) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _is_empty(val: Any) -> bool:
    v = str(val or "").strip()
    return not v or v.lower() in {"-", "na", "n/a", "unknown", "not documented", "not stated"}


def _pick_best(
    candidates: List[Tuple[str, str, str]],
    field: str = "",
) -> Tuple[str, str]:
    """Pick value with highest doc priority; tie-break by quality / length."""
    best_val, best_src, best_score = "", "", -1
    for val, src, doc_type in candidates:
        if _is_empty(val):
            continue
        score = _doc_priority(doc_type) * 10
        if field == "patient_name":
            nq = score_name_quality(val)
            if nq < 5:
                continue
            score += nq * 5  # prefer complete clean names over high-priority OCR junk
            val = normalize_patient_name(val) or val
        elif field == "age":
            age = normalize_age(val)
            if not age:
                continue
            val = age
            score += 5
        elif field == "hospital":
            hosp = normalize_hospital_name(val)
            if not hosp:
                continue
            val = hosp
            score += min(len(hosp), 40)
        elif field == "policy_number":
            pol = normalize_policy_number(val)
            if not pol:
                continue
            val = pol
            score += 20
        elif field == "bill_amount":
            bill = normalize_bill_amount(val)
            if not bill:
                continue
            val = bill
            score += 20
        else:
            score += min(len(val), 40)
        if score > best_score or (score == best_score and len(val) > len(best_val)):
            best_val, best_src, best_score = val, src, score
    return best_val, best_src


def _collect_unique(
    candidates: List[Tuple[str, str, str]],
) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for val, src, _doc_type in sorted(candidates, key=lambda x: -_doc_priority(x[2])):
        if _is_empty(val):
            continue
        key = _norm(val)
        if key in seen:
            continue
        seen.add(key)
        out.append({"value": val, "source": src})
    return out


def _detect_conflicts(field: str, candidates: List[Tuple[str, str, str]]) -> Optional[dict]:
    distinct = []
    seen = set()
    for val, src, doc_type in candidates:
        if _is_empty(val):
            continue
        key = _norm(val)
        if key in seen:
            continue
        seen.add(key)
        distinct.append({"value": val, "source": src, "document_type": doc_type})
    if len(distinct) < 2:
        return None
    return {"field": field, "values": distinct}


def build_case_facts_ledger(
    per_document_facts: List[dict],
    claim_facts: Optional[dict] = None,
) -> dict:
    """Merge map-step outputs + deterministic claim facts into one ledger."""
    claim_facts = claim_facts or {}
    docs = [d for d in (per_document_facts or []) if isinstance(d, dict)]

    def _candidates(field: str) -> List[Tuple[str, str, str]]:
        rows: List[Tuple[str, str, str]] = []
        for doc in docs:
            src = doc.get("source_file") or "unknown"
            dtype = doc.get("document_type") or "other"
            if field == "diagnosis" and (dtype or "").lower().replace(" ", "_") in _DX_EXCLUDED_TYPES:
                continue
            val = doc.get(field) or ""
            if field == "diagnosis" and re.search(
                r"thalamogeniculate|midline\s+shift|peri[\s-]?hemorrhagic|hu\s*:",
                str(val),
                re.I,
            ):
                continue
            if not _is_empty(val):
                rows.append((str(val).strip(), src, dtype))
        return rows

    merged: Dict[str, Any] = {}

    for field in (
        "patient_name", "age", "sex", "hospital", "diagnosis",
        "chief_complaint", "nature_of_admission",
        "admission_date", "discharge_date", "consultation_date",
        "bill_amount", "policy_number", "claim_number",
    ):
        val, src = _pick_best(_candidates(field), field=field)
        if field == "patient_name":
            val = normalize_patient_name(val)
        elif field == "age":
            val = normalize_age(val)
        elif field == "hospital":
            val = normalize_hospital_name(val)
        elif field == "policy_number":
            val = normalize_policy_number(val)
        elif field == "bill_amount":
            val = normalize_bill_amount(val)
        merged[field] = val
        merged[f"{field}_source"] = src

    # Prefer deterministic claim_facts for dates/hospital when present
    for field, src_field in (
        ("admission_date", "date_of_admission_source"),
        ("discharge_date", "date_of_discharge_source"),
        ("consultation_date", "consultation_date_source"),
    ):
        cf_val = claim_facts.get(field.replace("admission_date", "date_of_admission")) or claim_facts.get(field)
        if field == "admission_date":
            cf_val = claim_facts.get("date_of_admission") or ""
        elif field == "discharge_date":
            cf_val = claim_facts.get("date_of_discharge") or ""
        elif field == "consultation_date":
            cf_val = claim_facts.get("consultation_date") or ""
        if not _is_empty(cf_val):
            merged[field] = cf_val
            merged[f"{field}_source"] = claim_facts.get(src_field) or merged.get(f"{field}_source") or ""

    cf_hospital = normalize_hospital_name(claim_facts.get("hospital") or "")
    if cf_hospital:
        merged["hospital"] = cf_hospital
    cf_bill = normalize_bill_amount(claim_facts.get("total_hospital_bill") or "")
    if cf_bill:
        merged["bill_amount"] = cf_bill
    if not _is_empty(claim_facts.get("nature_of_admission")):
        merged["nature_of_admission"] = claim_facts["nature_of_admission"]

    merged["diagnosis_all"] = _collect_unique(_candidates("diagnosis"))

    proc_cands: List[Tuple[str, str, str]] = []
    med_cands: List[Tuple[str, str, str]] = []
    lab_cands: List[Tuple[str, str, str]] = []
    img_cands: List[Tuple[str, str, str]] = []
    gap_cands: List[Tuple[str, str, str]] = []
    billing_flags: List[str] = []

    for doc in docs:
        src = doc.get("source_file") or "unknown"
        dtype = doc.get("document_type") or "other"
        for p in doc.get("procedures") or []:
            proc_cands.append((stringify_item(p), src, dtype))
        for m in doc.get("medications") or []:
            med_cands.append((stringify_item(m), src, dtype))
        for lab in doc.get("key_labs") or []:
            lab_cands.append((stringify_item(lab), src, dtype))
        for img in doc.get("imaging_findings") or []:
            img_cands.append((stringify_item(img), src, dtype))
        for gap in doc.get("documentation_gaps") or []:
            gap_cands.append((stringify_item(gap), src, dtype))
        if not _is_empty(doc.get("bill_amount")):
            billing_flags.append(f"Bill in {src}: {doc['bill_amount']}")

    merged["procedures"] = [x["value"] for x in _collect_unique(proc_cands)]
    merged["medications"] = [x["value"] for x in _collect_unique(med_cands)]
    merged["key_labs"] = [x["value"] for x in _collect_unique(lab_cands)]
    merged["imaging_findings"] = [x["value"] for x in _collect_unique(img_cands)]
    merged["documentation_gaps"] = [x["value"] for x in _collect_unique(gap_cands)]
    merged["billing_flags"] = billing_flags

    conflicts = []
    for field in ("patient_name", "diagnosis", "bill_amount"):
        c = _detect_conflicts(field, _candidates(field))
        if c:
            conflicts.append(c)

    per_document_summaries = []
    for doc in docs:
        per_document_summaries.append({
            "filename": doc.get("source_file") or "",
            "document_type": doc.get("document_type") or "other",
            "summary": doc.get("summary") or "",
            "notable_findings": normalize_str_list(doc.get("notable_findings")),
            "patient_name": doc.get("patient_name") or "",
            "diagnosis": doc.get("diagnosis") or "",
        })

    return {
        "documents": docs,
        "merged": merged,
        "conflicts": conflicts,
        "per_document_summaries": per_document_summaries,
    }


def apply_ledger_to_claim_facts(claim_facts: dict, ledger: dict) -> dict:
    """Overlay ledger merged fields onto claim_facts for pipeline / merge step."""
    claim_facts = dict(claim_facts or {})
    merged = (ledger or {}).get("merged") or {}

    if not _is_empty(merged.get("diagnosis")):
        claim_facts["diagnosis"] = merged["diagnosis"]
        claim_facts["diagnosis_source"] = merged.get("diagnosis_source") or ""

    if not _is_empty(merged.get("patient_name")):
        claim_facts["patient_name"] = merged["patient_name"]
        claim_facts["patient_name_source"] = merged.get("patient_name_source") or ""

    if not _is_empty(merged.get("age")):
        claim_facts["patient_age"] = merged["age"]
    if not _is_empty(merged.get("sex")):
        claim_facts["patient_sex"] = merged["sex"]

    if not _is_empty(merged.get("bill_amount")) and _is_empty(claim_facts.get("total_hospital_bill")):
        claim_facts["total_hospital_bill"] = merged["bill_amount"]

    claim_facts["case_facts_ledger"] = ledger
    return claim_facts


def format_ledger_for_audit(ledger: dict, max_chars: int = 14000) -> str:
    """Human-readable ledger block for the reasoning LLM."""
    if not ledger:
        return ""

    merged = ledger.get("merged") or {}
    lines = [
        "=== CASE FACTS LEDGER (per-document map step — authoritative extracted facts) ===",
        f"Patient: {merged.get('patient_name') or 'Not documented'}"
        + (f" (source: {merged['patient_name_source']})" if merged.get("patient_name_source") else ""),
        f"Age / Sex: {merged.get('age') or '—'} / {merged.get('sex') or '—'}",
        f"Primary diagnosis: {merged.get('diagnosis') or 'Not documented'}"
        + (f" (source: {merged['diagnosis_source']})" if merged.get("diagnosis_source") else ""),
        f"Hospital: {merged.get('hospital') or '—'}",
        f"Admission: {merged.get('admission_date') or '—'} | Discharge: {merged.get('discharge_date') or '—'}",
        f"Nature: {merged.get('nature_of_admission') or '—'}",
        f"Chief complaint: {merged.get('chief_complaint') or '—'}",
        f"Bill amount: {merged.get('bill_amount') or '—'}",
    ]

    if merged.get("diagnosis_all") and len(merged["diagnosis_all"]) > 1:
        lines.append("All diagnoses by document:")
        for item in merged["diagnosis_all"]:
            lines.append(f"  - {item.get('value')} ({item.get('source')})")

    if merged.get("medications"):
        lines.append("Medications (by document): " + "; ".join(merged["medications"][:12]))
    if merged.get("key_labs"):
        lines.append("Key labs: " + "; ".join(merged["key_labs"][:10]))
    if merged.get("imaging_findings"):
        lines.append("Imaging: " + "; ".join(merged["imaging_findings"][:8]))

    conflicts = ledger.get("conflicts") or []
    if conflicts:
        lines.append("CONFLICTS (resolve using source document priority — discharge > preauth > clinical):")
        for c in conflicts:
            parts = [f"{v.get('value')} ({v.get('source')})" for v in c.get("values") or []]
            lines.append(f"  - {c.get('field')}: " + " vs ".join(parts))

    lines.append("")
    lines.append("=== PER-DOCUMENT SUMMARIES ===")
    for doc in ledger.get("per_document_summaries") or []:
        lines.append(f"--- {doc.get('filename')} ({doc.get('document_type')}) ---")
        if doc.get("patient_name"):
            lines.append(f"  Patient in file: {doc['patient_name']}")
        if doc.get("diagnosis"):
            lines.append(f"  Diagnosis in file: {doc['diagnosis']}")
        lines.append(f"  Summary: {doc.get('summary') or '—'}")
        nf = doc.get("notable_findings") or []
        if nf:
            lines.append("  Notable: " + "; ".join(nf[:6]))

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n… [truncated]"
    return text


def merge_patient_from_ledger(result: dict, ledger: dict) -> dict:
    """Fill patient_details from ledger when LLM left blanks or wrong."""
    merged = (ledger or {}).get("merged") or {}
    patient = result.setdefault("patient_details", {})
    claim = result.setdefault("claim_details", {})

    name = normalize_patient_name(merged.get("patient_name") or patient.get("name"))
    if name and (
        _is_empty(patient.get("name"))
        or score_name_quality(name) >= score_name_quality(str(patient.get("name") or ""))
    ):
        patient["name"] = name

    age = normalize_age(merged.get("age") or patient.get("age"))
    if age:
        patient["age"] = age
    else:
        # Drop implausible ages left by the LLM (e.g. 149)
        bad = normalize_age(patient.get("age"))
        if not bad:
            patient["age"] = ""

    sex = str(merged.get("sex") or "").strip()
    if sex and _is_empty(patient.get("sex")):
        patient["sex"] = sex

    hospital = normalize_hospital_name(merged.get("hospital") or claim.get("hospital"))
    if hospital:
        claim["hospital"] = hospital
    elif claim.get("hospital") and not normalize_hospital_name(claim.get("hospital")):
        claim["hospital"] = ""

    policy = normalize_policy_number(merged.get("policy_number") or "")
    if policy:
        ins = result.setdefault("insurance_details", {})
        current_pol = normalize_policy_number(ins.get("policy_number") or "")
        if not current_pol:
            ins["policy_number"] = policy

    bill = normalize_bill_amount(merged.get("bill_amount") or "")
    if bill:
        claim["total_hospital_bill"] = bill

    procs = merged.get("procedures")
    if isinstance(procs, list) and procs:
        current_proc = str(claim.get("procedure_or_surgery") or "").strip()
        # Prefer ledger procedures (already past-history filtered at map step)
        joined = "; ".join(str(p) for p in procs if str(p).strip())
        if joined and (
            _is_empty(current_proc)
            or re.search(r"\bturp\b", current_proc, re.I)
            and not re.search(r"\bturp\b", joined, re.I)
        ):
            claim["procedure_or_surgery"] = joined
    return result
