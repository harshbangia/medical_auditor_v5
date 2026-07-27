"""V2 agent orchestrator — post-processing and plan attachment for the audit pipeline.

Full agent fan-out (Doc AI microservice, Celery, etc.) comes in later phases.
This module is the strangler entry point: attach plan, MCR, timeline, verifier.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.agents.case_graph import build_medical_case_record
from backend.agents.evidence_verifier import verify_audit_result
from backend.agents.planner import build_audit_plan
from backend.agents.timeline_agent import timeline_to_result_rows


def apply_agent_postprocess(
    result: dict,
    *,
    case_text: str = "",
    source_summaries: Optional[List[dict]] = None,
    case_facts_ledger: Optional[dict] = None,
    claim_facts: Optional[dict] = None,
    guidelines: Optional[List[str]] = None,
) -> dict:
    """Attach plan + MCR + rebuilt timeline + evidence verification."""
    if not result or result.get("error"):
        return result

    has_hw = any(
        isinstance(s, dict) and s.get("contains_handwriting_or_scan")
        for s in (source_summaries or [])
    )
    plan = build_audit_plan(
        document_count=len(source_summaries or []),
        specialty_hint=str((result.get("claim_details") or {}).get("diagnosis") or ""),
        has_handwriting=has_hw,
        guidelines=guidelines,
    )
    for step in plan.steps:
        step.status = "done"

    mcr = build_medical_case_record(
        case_facts_ledger,
        claim_facts,
        existing_timeline=result.get("timeline") or [],
    )

    # Prefer structured timeline when we have dates
    if mcr.timeline:
        result["timeline"] = timeline_to_result_rows(mcr.timeline)

    # Ensure patient/diagnosis from MCR if still blank
    patient = result.setdefault("patient_details", {})
    if mcr.patient_name and not str(patient.get("name") or "").strip():
        patient["name"] = mcr.patient_name
    if mcr.age and not str(patient.get("age") or "").strip():
        patient["age"] = mcr.age
    if mcr.sex and not str(patient.get("sex") or "").strip():
        patient["sex"] = mcr.sex

    claim = result.setdefault("claim_details", {})
    if mcr.diagnosis and not str(claim.get("diagnosis") or "").strip():
        claim["diagnosis"] = mcr.diagnosis
    if mcr.hospital and not str(claim.get("hospital") or "").strip():
        claim["hospital"] = mcr.hospital

    result = verify_audit_result(
        result,
        case_text=case_text,
        source_summaries=source_summaries,
        case_facts_ledger=case_facts_ledger,
    )

    result["audit_plan"] = plan.model_dump()
    result["medical_case_record"] = mcr.model_dump()
    result["mcr_prompt_block"] = mcr.to_prompt_block()

    # Surface verification notes into documentation_gaps lightly
    notes = (result.get("verification") or {}).get("notes") or []
    if notes:
        gaps = result.setdefault("documentation_gaps", [])
        for note in notes[:5]:
            if note.startswith("Patient name") or note.startswith("Diagnosis missing"):
                if note not in gaps:
                    gaps.append(note)

    return result


def mcr_context_for_audit(ledger: Optional[dict], claim_facts: Optional[dict] = None) -> str:
    """Build MCR prompt block before the main reasoning LLM call."""
    mcr = build_medical_case_record(ledger, claim_facts)
    return mcr.to_prompt_block()
