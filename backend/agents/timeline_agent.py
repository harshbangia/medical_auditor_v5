"""Timeline agent — reconstruct patient journey from ledger / claim facts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.agents.schemas import EvidenceSpan, TimelineEvent


def _ev(date: str, event: str, category: str, source: str = "") -> TimelineEvent:
    evidence = []
    if source:
        evidence.append(EvidenceSpan(document=source, quote=event, confidence=0.8))
    return TimelineEvent(date=date or "", event=event, category=category, evidence=evidence)


def build_clinical_timeline(
    ledger: Optional[dict] = None,
    claim_facts: Optional[dict] = None,
    existing_timeline: Optional[List[dict]] = None,
) -> List[TimelineEvent]:
    """Merge deterministic dates + notable doc events into an ordered timeline."""
    ledger = ledger or {}
    claim_facts = claim_facts or {}
    merged = ledger.get("merged") or {}

    events: List[TimelineEvent] = []
    seen = set()

    def _add(item: TimelineEvent) -> None:
        key = (item.date.strip().lower(), item.event.strip().lower())
        if not item.event or key in seen:
            return
        seen.add(key)
        events.append(item)

    consult = claim_facts.get("consultation_date") or merged.get("consultation_date") or ""
    admit = claim_facts.get("date_of_admission") or merged.get("admission_date") or ""
    discharge = claim_facts.get("date_of_discharge") or merged.get("discharge_date") or ""
    consult_src = claim_facts.get("consultation_date_source") or merged.get("consultation_date_source") or ""
    admit_src = claim_facts.get("date_of_admission_source") or merged.get("admission_date_source") or ""
    discharge_src = claim_facts.get("date_of_discharge_source") or merged.get("discharge_date_source") or ""

    if consult:
        _add(_ev(consult, "First consultation / clinical presentation", "consult", consult_src))
    if admit:
        nature = merged.get("nature_of_admission") or claim_facts.get("nature_of_admission") or ""
        label = f"Admission{f' ({nature})' if nature else ''}"
        _add(_ev(admit, label, "admission", admit_src))

    for lab in (merged.get("key_labs") or [])[:6]:
        _add(_ev(admit or consult, f"Investigation: {lab}", "investigation",
                 merged.get("diagnosis_source") or ""))

    for img in (merged.get("imaging_findings") or [])[:4]:
        _add(_ev(admit or consult, f"Imaging: {img}", "investigation", ""))

    for proc in (merged.get("procedures") or [])[:5]:
        _add(_ev(admit or "", f"Procedure: {proc}", "procedure", ""))

    for med in (merged.get("medications") or [])[:6]:
        _add(_ev(admit or consult, f"Medication: {med}", "medication", ""))

    if discharge:
        _add(_ev(discharge, "Discharge", "discharge", discharge_src))

    bill = merged.get("bill_amount") or claim_facts.get("total_hospital_bill") or ""
    if bill:
        _add(_ev(discharge or admit, f"Final / hospital bill: {bill}", "billing",
                 merged.get("bill_amount_source") or ""))

    # Preserve useful LLM timeline rows that are not duplicates
    for raw in existing_timeline or []:
        if not isinstance(raw, dict):
            continue
        _add(_ev(str(raw.get("date") or ""), str(raw.get("event") or ""), "clinical", ""))

    # Stable-ish order: undated last within category priority
    category_rank = {
        "consult": 0,
        "admission": 1,
        "investigation": 2,
        "procedure": 3,
        "medication": 4,
        "clinical": 5,
        "discharge": 6,
        "billing": 7,
    }
    events.sort(key=lambda e: (category_rank.get(e.category, 5), e.date or "9999"))
    return events


def timeline_to_result_rows(events: List[TimelineEvent]) -> List[Dict[str, Any]]:
    return [{"date": e.date, "event": e.event, "category": e.category} for e in events]
