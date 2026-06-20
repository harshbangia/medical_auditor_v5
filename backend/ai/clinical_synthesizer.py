"""Synthesise multi-visit clinical facts from transcribed case documents.

Indian OPD records often span several handwritten pages:
  - Visit 1: symptom duration at presentation (e.g. "facial pain x 1 month")
  - Visit 2 (prescription page): treatment course (e.g. "x 2 months", "F/u after 2 months")

The audit LLM often collapses these into a single "1 month" finding. This module
builds an explicit structured summary so the audit distinguishes:
  * symptom duration at each consult
  * prescribed medication course duration
  * follow-up interval
"""

import re
from typing import Any, Dict, List

_RX_COURSE_RE = re.compile(
    r"(?:x|×|for)\s*(\d+)\s*(mths|mth|mo|months)\b",
    re.I,
)

# Symptom / complaint duration at presentation (consultation notes only)
_SYMPTOM_DURATION_RE = re.compile(
    r"(?:facial\s+pain|pain|c/o|p/o|complaint[s]?|symptom[s]?)"
    r"[^\n]{0,80}?"
    r"(?:x|since|for|duration\s*[:.]?)\s*"
    r"(\d+)\s*(mo|mth|mths|month|months|wk|wks|week|weeks|yr|year|years|day|days)",
    re.I,
)

# Follow-up: "F/u after 2 mths", "Review after 2 weeks", "F/b after 20 days"
_FOLLOWUP_RE = re.compile(
    r"(?:f/u|f\.u\.|follow[\s-]?up|review|f/b|fr\.?\s*after)\s*(?:after\s*)?"
    r"(\d+)\s*(mo|mth|mths|month|months|wk|wks|week|weeks|day|days|d)",
    re.I,
)

_VISIT_DATE_RE = re.compile(
    r"(?:^|\n)\s*(?:Mrs?\.?|Ms\.?|Pt\.?|Patient)?\s*"
    r"[A-Za-z\s]{0,40}?\s*(\d{1,2}/\d{1,2}/\d{2,4})\b",
    re.MULTILINE,
)

_PRESCRIPTION_MARKERS = re.compile(
    r"(?:^|\n)\s*(?:tab\.?|cap\.?|℞|rx\.?|zenoxa|tegretol|carbatol|dolokind|dolo|pregaba|lyrica)\b",
    re.I | re.MULTILINE,
)

_UNIT_NORMALIZE = {
    "mo": "month", "mth": "month", "mths": "months", "month": "month", "months": "months",
    "wk": "week", "wks": "weeks", "week": "week", "weeks": "weeks",
    "yr": "year", "year": "year", "years": "years",
    "day": "day", "days": "days", "d": "days",
}


def _fmt_duration(num: str, unit: str) -> str:
    u = _UNIT_NORMALIZE.get(unit.lower(), unit.lower())
    n = int(num)
    if n == 1 and u.endswith("s"):
        u = u[:-1]
    elif n != 1 and not u.endswith("s") and u not in ("months", "weeks", "days", "years"):
        u = u + "s" if u in ("month", "week", "day", "year") else u
    return f"{n} {u}"


def _split_visits(case_text: str) -> List[str]:
    """Split case text into per-page / per-block segments."""
    blocks = []
    for part in re.split(r"=== Page \d+ — vision transcription[^=]*===", case_text or ""):
        part = part.strip()
        if part:
            blocks.append(part)
    if not blocks and case_text:
        blocks = [case_text]
    return blocks


def synthesize_clinical_visits(case_text: str) -> List[Dict[str, Any]]:
    """Return structured visit records parsed from transcribed case text."""
    visits: List[Dict[str, Any]] = []
    for block in _split_visits(case_text):
        dates = _VISIT_DATE_RE.findall(block)
        visit_date = dates[0] if dates else ""

        is_prescription = bool(_PRESCRIPTION_MARKERS.search(block))

        symptom_durations = []
        if not is_prescription or "c/o" in block.lower() or "p/o" in block.lower():
            symptom_durations = [
                _fmt_duration(m.group(1), m.group(2))
                for m in _SYMPTOM_DURATION_RE.finditer(block)
            ]

        med_courses = []
        if is_prescription:
            med_courses = [
                _fmt_duration(m.group(1), m.group(2))
                for m in _RX_COURSE_RE.finditer(block)
            ]

        followups = [
            _fmt_duration(m.group(1), m.group(2))
            for m in _FOLLOWUP_RE.finditer(block)
        ]

        has_clinical = bool(
            symptom_durations
            or med_courses
            or followups
            or is_prescription
            or "trigeminal" in block.lower()
            or "neuralgia" in block.lower()
        )

        if not has_clinical:
            continue

        visits.append({
            "date": visit_date,
            "symptom_duration_at_visit": symptom_durations[0] if symptom_durations else "",
            "medication_course_duration": med_courses[-1] if med_courses else "",
            "follow_up_after": followups[-1] if followups else "",
            "is_prescription_page": is_prescription,
            "raw_excerpt": block[:400].replace("\n", " ").strip(),
        })
    return visits


def build_clinical_synthesis_section(case_text: str) -> str:
    """Plain-text block injected into audit context."""
    visits = synthesize_clinical_visits(case_text)
    if not visits:
        return ""

    lines = [
        "=== CLINICAL VISIT SYNTHESIS (from all consultation / prescription pages) ===",
        "IMPORTANT: Do NOT collapse these into a single duration.",
        "  • 'symptom duration at visit' = how long patient had symptoms WHEN SEEN on that date",
        "  • 'medication course duration' = how long medicines were prescribed FOR",
        "  • 'follow-up after' = when patient was asked to return",
        "",
    ]

    for i, v in enumerate(visits, 1):
        parts = [f"Visit {i}"]
        if v.get("date"):
            parts.append(f"date {v['date']}")
        if v.get("symptom_duration_at_visit"):
            parts.append(f"symptom duration at presentation: {v['symptom_duration_at_visit']}")
        if v.get("medication_course_duration"):
            parts.append(f"medication course prescribed for: {v['medication_course_duration']}")
        if v.get("follow_up_after"):
            parts.append(f"follow-up instructed after: {v['follow_up_after']}")
        if v.get("is_prescription_page"):
            parts.append("(prescription page)")
        lines.append("- " + " | ".join(parts))

    # Explicit guidance for common TN case pattern
    symptom_vals = [v["symptom_duration_at_visit"] for v in visits if v.get("symptom_duration_at_visit")]
    med_vals = [v["medication_course_duration"] for v in visits if v.get("medication_course_duration")]
    fu_vals = [v["follow_up_after"] for v in visits if v.get("follow_up_after")]

    if symptom_vals or med_vals or fu_vals:
        lines.append("")
        lines.append("SUMMARY FOR AUDIT:")
        if symptom_vals:
            lines.append(
                f"  Symptom duration at presentation (consult note): "
                f"{', '.join(dict.fromkeys(symptom_vals))}"
            )
        if med_vals:
            lines.append(
                f"  Medication course prescribed (prescription page): "
                f"{', '.join(dict.fromkeys(med_vals))}"
            )
        if fu_vals:
            lines.append(
                f"  Follow-up interval instructed: {', '.join(dict.fromkeys(fu_vals))}"
            )
        if symptom_vals and (med_vals or fu_vals):
            lines.append(
                "  NOTE: Symptom duration at first consult and medication course / follow-up "
                "duration are DIFFERENT facts from DIFFERENT pages — report ALL in "
                "clinical_findings and observations. Do NOT report only the 1-month figure."
            )

    lines.append(
        "In clinical_findings, include SEPARATE rows for symptom duration AND treatment course duration."
    )
    return "\n".join(lines)
