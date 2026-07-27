"""Shared schemas for the V2 multi-agent medical audit architecture.

Every finding and entity should eventually link to EvidenceSpan so the
verifier and UI can prove claims against source documents.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocType(str, Enum):
    DISCHARGE_SUMMARY = "discharge_summary"
    BILL = "bill"
    PREAUTH = "preauth"
    QUERY_LETTER = "query_letter"
    LAB = "lab"
    RADIOLOGY = "radiology"
    INDOOR = "indoor"
    PRESCRIPTION = "prescription"
    CLINICAL = "clinical"
    OTHER = "other"


class EvidenceSpan(BaseModel):
    """NotebookLM-style citation to a source document."""

    document: str = ""
    page: Optional[int] = None
    quote: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TimelineEvent(BaseModel):
    date: str = ""
    event: str = ""
    category: str = ""  # consult | admission | investigation | procedure | medication | discharge | billing
    evidence: List[EvidenceSpan] = Field(default_factory=list)


class AuditFinding(BaseModel):
    category: str = ""
    claim: str = ""
    severity: str = "Medium"
    explanation: str = ""
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    guideline_ref: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verified: bool = False


class PlanStep(BaseModel):
    step_id: str
    agent: str
    description: str
    depends_on: List[str] = Field(default_factory=list)
    status: str = "pending"  # pending | running | done | skipped | failed


class AuditPlan(BaseModel):
    audit_goal: str = "Evidence-grounded medical insurance claim audit"
    specialty_hint: str = ""
    steps: List[PlanStep] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class MedicalCaseRecord(BaseModel):
    """Authoritative structured case state (Case Graph / MCR)."""

    patient_name: str = ""
    age: str = ""
    sex: str = ""
    hospital: str = ""
    diagnosis: str = ""
    procedures: List[str] = Field(default_factory=list)
    medications: List[str] = Field(default_factory=list)
    key_labs: List[str] = Field(default_factory=list)
    imaging: List[str] = Field(default_factory=list)
    admission_date: str = ""
    discharge_date: str = ""
    consultation_date: str = ""
    nature_of_admission: str = ""
    bill_amount: str = ""
    policy_number: str = ""
    claim_number: str = ""
    timeline: List[TimelineEvent] = Field(default_factory=list)
    documentation_gaps: List[str] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)

    def identity_complete(self) -> bool:
        return bool(self.patient_name and self.diagnosis)

    def to_prompt_block(self) -> str:
        lines = [
            "=== MEDICAL CASE RECORD (structured, authoritative) ===",
            f"Patient: {self.patient_name or '—'} | Age: {self.age or '—'} | Sex: {self.sex or '—'}",
            f"Hospital: {self.hospital or '—'}",
            f"Diagnosis: {self.diagnosis or '—'}",
            f"Admission: {self.admission_date or '—'} → Discharge: {self.discharge_date or '—'}",
            f"Nature: {self.nature_of_admission or '—'}",
            f"Bill: {self.bill_amount or '—'}",
        ]
        if self.procedures:
            lines.append("Procedures: " + "; ".join(self.procedures[:10]))
        if self.medications:
            lines.append("Medications: " + "; ".join(self.medications[:12]))
        if self.key_labs:
            lines.append("Labs: " + "; ".join(self.key_labs[:10]))
        if self.imaging:
            lines.append("Imaging: " + "; ".join(self.imaging[:8]))
        if self.timeline:
            lines.append("Timeline:")
            for ev in self.timeline[:20]:
                lines.append(f"  - {ev.date or 'undated'}: {ev.event}")
        if self.conflicts:
            lines.append("Conflicts:")
            for c in self.conflicts[:5]:
                lines.append(f"  - {c}")
        if self.documentation_gaps:
            lines.append("Gaps: " + "; ".join(self.documentation_gaps[:8]))
        return "\n".join(lines)


class AgentRunResult(BaseModel):
    agent: str
    ok: bool = True
    message: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
