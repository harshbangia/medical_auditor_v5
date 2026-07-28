"""Planner agent — decomposes an audit into ordered specialist stages."""

from __future__ import annotations

from typing import List, Optional

from backend.agents.schemas import AuditPlan, PlanStep


def build_audit_plan(
    document_count: int = 0,
    specialty_hint: str = "",
    has_handwriting: bool = False,
    guidelines: Optional[List[str]] = None,
) -> AuditPlan:
    """Deterministic planner (rules-first). LLM planners can replace later."""
    notes: List[str] = []
    if document_count:
        notes.append(f"{document_count} uploaded document(s) to map")
    if has_handwriting:
        notes.append("Handwriting/scan detected — vision OCR path required")
    if guidelines:
        notes.append("Guidelines: " + "; ".join(guidelines[:5]))

    steps = [
        PlanStep(
            step_id="ingest",
            agent="document_intelligence",
            description="Parse PDFs, OCR/vision, classify document types",
        ),
        PlanStep(
            step_id="map",
            agent="entity_extraction",
            description="Per-document structured fact extraction (map step)",
            depends_on=["ingest"],
        ),
        PlanStep(
            step_id="merge",
            agent="case_graph",
            description="Merge into Medical Case Record with source priority",
            depends_on=["map"],
        ),
        PlanStep(
            step_id="identity",
            agent="claim_identity",
            description="Vision+regex extract insurer, policy, claim, hospital, age from preauth",
            depends_on=["merge"],
        ),
        PlanStep(
            step_id="timeline",
            agent="timeline",
            description="Reconstruct patient clinical journey",
            depends_on=["merge", "identity"],
        ),
        PlanStep(
            step_id="align",
            agent="guideline_alignment",
            description="Gate mismatched specialty guidelines",
            depends_on=["merge"],
        ),
        PlanStep(
            step_id="retrieve",
            agent="guideline_retrieval",
            description="Hybrid RAG over selected clinical guidelines",
            depends_on=["align"],
        ),
        PlanStep(
            step_id="audit",
            agent="audit_reasoning",
            description="Adversarial clinical/billing audit with evidence citations",
            depends_on=["retrieve", "timeline"],
        ),
        PlanStep(
            step_id="fraud",
            agent="fraud_detection",
            description="Deterministic + LLM fraud/abuse indicators",
            depends_on=["audit"],
        ),
        PlanStep(
            step_id="verify",
            agent="evidence_verifier",
            description="Drop unsupported findings; score confidence",
            depends_on=["fraud"],
        ),
        PlanStep(
            step_id="report",
            agent="report_generation",
            description="Render PDF/UI from verified JSON only",
            depends_on=["verify"],
        ),
    ]

    return AuditPlan(
        specialty_hint=specialty_hint or "",
        steps=steps,
        notes=notes,
    )


def plan_as_progress_messages(plan: AuditPlan) -> List[str]:
    return [f"{s.step_id}: {s.description}" for s in plan.steps]
