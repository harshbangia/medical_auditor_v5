"""Build MedicalCaseRecord from the per-document case facts ledger."""

from __future__ import annotations

from typing import Optional

from backend.agents.schemas import MedicalCaseRecord
from backend.agents.timeline_agent import build_clinical_timeline


def build_medical_case_record(
    ledger: Optional[dict] = None,
    claim_facts: Optional[dict] = None,
    existing_timeline: Optional[list] = None,
) -> MedicalCaseRecord:
    ledger = ledger or {}
    claim_facts = claim_facts or {}
    merged = ledger.get("merged") or {}

    timeline = build_clinical_timeline(ledger, claim_facts, existing_timeline)
    sources = []
    for doc in ledger.get("documents") or []:
        if isinstance(doc, dict) and doc.get("source_file"):
            sources.append(str(doc["source_file"]))

    return MedicalCaseRecord(
        patient_name=merged.get("patient_name") or claim_facts.get("patient_name") or "",
        age=merged.get("age") or claim_facts.get("patient_age") or "",
        sex=merged.get("sex") or claim_facts.get("patient_sex") or "",
        hospital=merged.get("hospital") or claim_facts.get("hospital") or "",
        diagnosis=merged.get("diagnosis") or claim_facts.get("diagnosis") or "",
        procedures=list(merged.get("procedures") or []),
        medications=list(merged.get("medications") or []),
        key_labs=list(merged.get("key_labs") or []),
        imaging=list(merged.get("imaging_findings") or []),
        admission_date=merged.get("admission_date") or claim_facts.get("date_of_admission") or "",
        discharge_date=merged.get("discharge_date") or claim_facts.get("date_of_discharge") or "",
        consultation_date=merged.get("consultation_date") or claim_facts.get("consultation_date") or "",
        nature_of_admission=(
            merged.get("nature_of_admission") or claim_facts.get("nature_of_admission") or ""
        ),
        bill_amount=merged.get("bill_amount") or claim_facts.get("total_hospital_bill") or "",
        policy_number=merged.get("policy_number") or "",
        claim_number=merged.get("claim_number") or "",
        timeline=timeline,
        documentation_gaps=list(merged.get("documentation_gaps") or []),
        conflicts=list(ledger.get("conflicts") or []),
        sources=sources,
    )
