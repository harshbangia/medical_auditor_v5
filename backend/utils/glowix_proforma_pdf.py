"""Glowix MEDICAL AUDIT REPORT proforma PDF.

Client-facing letter format matching Glowix Medical Services' Medical Audit
Report (patient/claim details, clinical findings table, documentation gaps,
timeline, narrative observations, inference, conclusion, remarks).

Not Q&A / Expert-Opinion style — observations render as prose under §6.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from backend.utils.inr_money import (
    billing_disallowance_rows as _billing_disallowance_rows,
    recompute_financial_review,
)

# Brand / letterhead constants (from Glowix Expert Opinion proforma)
_COMPANY = "GLOWIX MEDICAL SERVICES PRIVATE LIMITED"
_CIN = "U70200HR2024PTC122256"
_GSTIN = "06AALCG2970R1ZV"
_ADDRESS = "543-D, Pace City - II, Sector 37, Gurugram-122001"
_PHONE = "0124 - 4127700"
_EMAIL = "Info@glowixhealth.in"
_WEBSITE = "www.glowixhealth.in"
_DEFAULT_AUDITOR_NAME = os.getenv("AUDITOR_NAME", "DR. D.V. Saharan")
_DEFAULT_AUDITOR_QUAL = os.getenv("AUDITOR_QUALIFICATION", "MD (AIIMS)")
_DEFAULT_AUDITOR_ROLE = os.getenv("AUDITOR_ROLE", "Advisor")

_RED = colors.HexColor("#C41E3A")
_BLUE = colors.HexColor("#1E4D8C")
_BLACK = colors.HexColor("#1A1A1A")
_GRAY = colors.HexColor("#444444")

_PAGE_W, _PAGE_H = A4
_LEFT = 18 * mm
_RIGHT = 18 * mm
_TOP = 28 * mm
_BOTTOM = 28 * mm


def _na(val: Any) -> str:
    s = str(val or "").strip()
    if not s or s.lower() in {
        "none", "null", "unknown", "not specified", "—", "-",
        "na", "n/a", "not provided", "not available",
    }:
        return "NA"
    return s


def _esc(text: Any) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _logo_path() -> Optional[str]:
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "assets", "logo.png"),
        os.path.join("assets", "logo.png"),
        "/home/ubuntu/medical_auditor_v5/assets/logo.png",
    ]
    for path in candidates:
        full = os.path.abspath(path)
        if os.path.isfile(full):
            return full
    return None


def _financial_year_label(dt: datetime) -> str:
    # Indian FY: Apr–Mar → 26-27 for dates in Apr 2026–Mar 2027
    if dt.month >= 4:
        return f"{dt.year % 100:02d}-{(dt.year + 1) % 100:02d}"
    return f"{(dt.year - 1) % 100:02d}-{dt.year % 100:02d}"


def _default_ref(data: dict, report_dt: datetime) -> str:
    existing = str(data.get("report_ref") or data.get("audit_ref") or "").strip()
    if existing and existing not in {"-", "—"}:
        return existing
    claim = str((data.get("insurance_details") or {}).get("claim_incident_number") or "")
    digits = re.sub(r"\D", "", claim)[-4:] or f"{report_dt.timetuple().tm_yday:03d}"
    return f"GMS/{_financial_year_label(report_dt)}/{digits.zfill(4)}"


def _parse_report_date(data: dict) -> datetime:
    raw = str(data.get("report_date") or data.get("audit_date") or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


def _age_gender(patient: dict) -> str:
    age = _na(patient.get("age"))
    sex = _na(patient.get("sex") or patient.get("gender"))
    if age == "NA" and sex == "NA":
        return "NA"
    if age == "NA":
        return sex
    if sex == "NA":
        return f"{age} years" if "year" not in age.lower() else age
    age_part = age if "year" in age.lower() else f"{age} years"
    return f"{age_part} / {sex}"


def _documentation_gap_rows(data: dict) -> List[Any]:
    gaps = data.get("documentation_gaps") or []
    return gaps if isinstance(gaps, list) else []


def _checklist_status(data: dict) -> List[Tuple[str, str]]:
    """Map software checklist / sources into Glowix documentation rows."""
    rows_def = [
        ("Pre-authorization Approval Letter", ("pre-auth", "preauth", "authorization")),
        ("Admission Request Form", ("admission request", "admission form", "indoor")),
        ("Policy Copy / ID Card", ("policy", "id card")),
        ("Indoor Case Papers", ("indoor", "treatment sheet", "case paper", "icps")),
        ("Discharge Summary", ("discharge",)),
        ("Lab / Radiology Reports/X-Ray", ("lab", "radiology", "x-ray", "xray", "mri", "ct")),
        ("Operation Notes (if any)", ("operation", "ot note", "surgery note")),
        ("Pharmacy Bills", ("pharmacy", "medicine bill")),
        ("Implant Stickers (if any)", ("implant",)),
        ("Prescriptions", ("prescription", "rx")),
    ]

    sources = " ".join(
        str(s.get("filename") or s.get("name") or "")
        for s in (data.get("document_sources") or [])
        if isinstance(s, dict)
    ).lower()
    analysis = " ".join(
        f"{r.get('document', '')} {r.get('document_type', '')} {r.get('key_content', '')}"
        for r in (data.get("document_analysis") or [])
        if isinstance(r, dict)
    ).lower()
    checklist_blob = " ".join(
        f"{c.get('area', '')} {c.get('available', '')} {c.get('remarks', '')}"
        for c in (data.get("clinical_checklist") or [])
        if isinstance(c, dict)
    ).lower()
    blob = f"{sources} {analysis} {checklist_blob}"

    explicit: Dict[str, str] = {}
    for item in data.get("clinical_checklist") or []:
        if not isinstance(item, dict):
            continue
        area = str(item.get("area") or "").strip()
        avail = str(item.get("available") or "").strip().upper()
        if area:
            if avail in {"YES", "AVAILABLE", "Y"}:
                explicit[area.lower()] = "Available"
            elif avail in {"NO", "N", "NOT AVAILABLE"}:
                explicit[area.lower()] = "Not Available"
            elif avail in {"NA", "N/A"}:
                explicit[area.lower()] = "NA"

    out: List[Tuple[str, str]] = []
    for label, needles in rows_def:
        status = "NA"
        for key, val in explicit.items():
            if any(n in key for n in needles):
                status = val
                break
        else:
            if any(n in blob for n in needles):
                # Present in uploads unless explicitly marked missing
                if any(f"no {n}" in blob or f"{n} missing" in blob for n in needles):
                    status = "Not Available"
                else:
                    status = "Available"
        out.append((label, status))
    return out


def _build_styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "company": ParagraphStyle(
            "GxCompany",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=_RED,
            alignment=TA_LEFT,
            leading=16,
            spaceAfter=1,
        ),
        "cin": ParagraphStyle(
            "GxCin",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=_BLUE,
            alignment=TA_LEFT,
            leading=10,
        ),
        "meta": ParagraphStyle(
            "GxMeta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=_BLACK,
            leading=12,
        ),
        "title": ParagraphStyle(
            "GxTitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=_BLACK,
            alignment=TA_CENTER,
            spaceBefore=8,
            spaceAfter=12,
            leading=15,
        ),
        "section": ParagraphStyle(
            "GxSection",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=_BLACK,
            spaceBefore=10,
            spaceAfter=6,
            leading=13,
        ),
        "label": ParagraphStyle(
            "GxLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=_BLACK,
            leading=12,
        ),
        "value": ParagraphStyle(
            "GxValue",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=_BLACK,
            leading=12,
        ),
        "body": ParagraphStyle(
            "GxBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=_BLACK,
            alignment=TA_JUSTIFY,
            leading=12,
            spaceAfter=4,
        ),
        "q": ParagraphStyle(
            "GxQ",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=_BLACK,
            leading=12,
            spaceBefore=6,
            spaceAfter=2,
        ),
        "a": ParagraphStyle(
            "GxA",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=_BLACK,
            alignment=TA_JUSTIFY,
            leading=12,
            spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "GxFooter",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=_GRAY,
            alignment=TA_CENTER,
            leading=9,
        ),
        "sign": ParagraphStyle(
            "GxSign",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=_BLACK,
            alignment=TA_RIGHT,
            leading=11,
        ),
        "page": ParagraphStyle(
            "GxPage",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=_GRAY,
            alignment=TA_RIGHT,
        ),
    }


def _kv_block(rows: Sequence[Tuple[str, str]], styles: Dict[str, ParagraphStyle]) -> Table:
    data = []
    for label, value in rows:
        data.append([
            Paragraph(_esc(label), styles["label"]),
            Paragraph(":", styles["label"]),
            Paragraph(_esc(value), styles["value"]),
        ])
    table = Table(data, colWidths=[170, 12, 320])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _header_footer(canvas, doc):
    canvas.saveState()
    styles = _build_styles()
    logo = _logo_path()

    # Header band
    y_top = _PAGE_H - 12 * mm
    x0 = _LEFT
    if logo:
        try:
            canvas.drawImage(logo, x0, y_top - 14 * mm, width=14 * mm, height=14 * mm, mask="auto")
            text_x = x0 + 16 * mm
        except Exception:
            text_x = x0
    else:
        text_x = x0

    canvas.setFillColor(_RED)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(text_x, y_top - 5 * mm, _COMPANY)
    canvas.setFillColor(_BLUE)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(text_x, y_top - 9 * mm, f"(CIN No. : {_CIN})")

    # Footer
    canvas.setStrokeColor(_RED)
    canvas.setLineWidth(1.2)
    canvas.line(_LEFT, 18 * mm, _PAGE_W - _RIGHT, 18 * mm)
    canvas.setFillColor(_GRAY)
    canvas.setFont("Helvetica", 7)
    canvas.drawCentredString(_PAGE_W / 2, 13 * mm, f"(GSTIN/UIN : {_GSTIN})")
    canvas.drawCentredString(_PAGE_W / 2, 9.5 * mm, _ADDRESS)
    canvas.drawCentredString(
        _PAGE_W / 2,
        6 * mm,
        f"Ph. : {_PHONE}, E-mail: {_EMAIL} | {_WEBSITE}",
    )
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(
        _PAGE_W - _RIGHT,
        20 * mm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def _missing_documents_answer(checklist: List[Tuple[str, str]]) -> str:
    missing = [label for label, status in checklist if status in {"Not Available", "NA"}]
    # "NA" for optional items shouldn't force Yes — only clear Not Available
    clearly_missing = [label for label, status in checklist if status == "Not Available"]
    if clearly_missing:
        return "Yes"
    if all(status == "Available" for _, status in checklist):
        return "No"
    return "Yes" if missing else "No"


def _observation_narrative(data: dict) -> str:
    summary = str(data.get("auditor_observation_summary") or "").strip()
    if summary:
        return summary
    inference = str(data.get("inference") or data.get("auditor_conclusion") or "").strip()
    if inference:
        return inference
    patient = data.get("patient_details") or {}
    claim = data.get("claim_details") or {}
    name = _na(patient.get("name"))
    age = _na(patient.get("age"))
    sex = _na(patient.get("sex") or patient.get("gender"))
    dx = _na(claim.get("diagnosis"))
    doa = _na(claim.get("date_of_admission"))
    return (
        f"{name}, {age} years, {sex}, was reviewed for {dx}. "
        f"Date of admission: {doa}. Observations are based on the uploaded clinical records."
    )


def _bullet(text: str, styles: Dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(f"• {_esc(text)}", styles["body"])


def _simple_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    col_widths: Sequence[float],
    styles: Dict[str, ParagraphStyle],
) -> Table:
    data = [[Paragraph(f"<b>{_esc(h)}</b>", styles["label"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(_esc(c), styles["value"]) for c in row])
    table = Table(data, colWidths=list(col_widths))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _topic_heading(question: str) -> str:
    """Turn a model question into a proforma subheading (no Q1 / Ans)."""
    q = re.sub(r"^\s*Q\d*\.?\s*", "", str(question or "").strip())
    q = re.sub(r"\?+\s*$", "", q).strip()
    if not q:
        return "Clinical observation"
    # Keep readable; don't force title-case on medical prose
    if len(q) > 110:
        q = q[:107].rstrip() + "…"
    return q


def _gap_lines(data: dict) -> List[str]:
    lines: List[str] = []
    for gap in data.get("documentation_gaps") or []:
        if isinstance(gap, str) and gap.strip():
            lines.append(gap.strip())
            continue
        if not isinstance(gap, dict):
            continue
        title = str(gap.get("title") or gap.get("gap") or gap.get("category") or "").strip()
        finding = str(
            gap.get("finding")
            or gap.get("forensic_finding")
            or gap.get("description")
            or gap.get("detail")
            or ""
        ).strip()
        evidence = str(gap.get("evidence") or "").strip()
        action = str(gap.get("audit_action") or gap.get("recommendation") or "").strip()
        body = " ".join(p for p in (finding, evidence, action) if p)
        if title and body:
            lines.append(f"{title}: {body}")
        elif title or body:
            lines.append(title or body)
    return lines


def generate_glowix_medical_audit_report_pdf(data: dict, filename: str = "audit_report.pdf") -> str:
    """Write Glowix Medical Audit Report proforma PDF (narrative, not Q&A)."""
    data = recompute_financial_review(dict(data or {}))
    styles = _build_styles()
    report_dt = _parse_report_date(data)
    ref = _default_ref(data, report_dt)
    dated = report_dt.strftime("%d/%m/%Y")

    patient = data.get("patient_details") or {}
    insurance = data.get("insurance_details") or {}
    claim = data.get("claim_details") or {}
    fin = data.get("financial_review") or {}

    doc = BaseDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=_LEFT,
        rightMargin=_RIGHT,
        topMargin=_TOP,
        bottomMargin=_BOTTOM,
    )
    frame = Frame(
        _LEFT,
        _BOTTOM,
        _PAGE_W - _LEFT - _RIGHT,
        _PAGE_H - _TOP - _BOTTOM,
        id="normal",
    )
    doc.addPageTemplates([PageTemplate(id="glowix", frames=frame, onPage=_header_footer)])

    story: List[Any] = []

    meta = Table(
        [[
            Paragraph(f"<b>Ref. No. :</b> {_esc(ref)}", styles["meta"]),
            Paragraph(f"<b>Dated :</b> {_esc(dated)}........", styles["meta"]),
        ]],
        colWidths=[280, 220],
    )
    meta.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(meta)
    story.append(Paragraph("Medical Audit Report", styles["title"]))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#CCCCCC")))

    # 1. Patient Details
    story.append(Paragraph("1. Patient Details", styles["section"]))
    for line in (
        f"Name: {_na(patient.get('name'))}",
        f"Age: {_na(patient.get('age'))} years" if "year" not in str(patient.get("age") or "").lower() else f"Age: {_na(patient.get('age'))}",
        f"Sex: {_na(patient.get('sex') or patient.get('gender'))}",
        f"Claim Number: {_na(insurance.get('claim_incident_number'))}",
        f"Policy Number: {_na(insurance.get('policy_number'))}",
        f"Policy Period: {_na(insurance.get('policy_period'))}",
    ):
        story.append(_bullet(line, styles))

    # 2. Claim Details
    story.append(Paragraph("2. Claim Details", styles["section"]))
    lab_status = "Available"
    for row in data.get("clinical_checklist") or []:
        if not isinstance(row, dict):
            continue
        area = str(row.get("area") or "").lower()
        if "lab" in area or "radiology" in area:
            av = str(row.get("available") or "").strip().upper()
            if av in {"NO", "N", "NOT AVAILABLE"}:
                lab_status = "Not Available"
            elif av in {"YES", "Y", "AVAILABLE"}:
                lab_status = "Available"
            break
    illness = _na(
        claim.get("final_diagnosis")
        or claim.get("diagnosis")
        or claim.get("provisional_diagnosis")
    )
    for line in (
        f"Hospital Name: {_na(claim.get('hospital'))}",
        f"Consultation Date: {_na(claim.get('consultation_date') or claim.get('date_of_admission'))}",
        f"Claimed Illness: {illness}",
        f"Lab Test: {lab_status}",
    ):
        story.append(_bullet(line, styles))

    # 3. Summary of Clinical Findings
    story.append(Paragraph("3. Summary of Clinical Findings", styles["section"]))
    cf_rows: List[List[str]] = []
    for item in data.get("clinical_findings") or []:
        if not isinstance(item, dict):
            continue
        cf_rows.append([
            str(item.get("parameter") or "—"),
            str(item.get("value") or "—"),
            str(item.get("normal_range") or "—"),
            str(item.get("comment") or "—"),
        ])
    if not cf_rows:
        cf_rows = [["—", "Not documented in structured form", "—", "See Observations"]]
    story.append(_simple_table(
        ["Parameter", "Value", "Normal Range", "Comments"],
        cf_rows[:20],
        [120, 100, 110, 170],
        styles,
    ))

    # 4. Documentation Gaps
    story.append(Paragraph("4. Documentation Gaps in Record", styles["section"]))
    intro_bits = [
        _observation_narrative(data),
    ]
    # Prefer a short clinical opener from claim facts
    opener = (
        f"{_na(patient.get('name'))}, {_na(patient.get('age'))} years, "
        f"{_na(patient.get('sex') or patient.get('gender'))}, admitted with "
        f"{illness}. Date of admission: {_na(claim.get('date_of_admission'))}; "
        f"date of discharge: {_na(claim.get('date_of_discharge'))}."
    )
    story.append(Paragraph(_esc(opener), styles["body"]))
    if intro_bits[0] and intro_bits[0] != opener:
        # Only add summary if distinct
        pass

    gaps = _gap_lines(data)
    if gaps:
        story.append(Paragraph(
            "The following documentation / record gaps were identified:",
            styles["body"],
        ))
        for g in gaps[:12]:
            story.append(_bullet(g, styles))
    else:
        story.append(Paragraph(
            "No major mandatory-document gaps were identified beyond the checklist remarks below.",
            styles["body"],
        ))

    # FWA / identity notes as gap bullets when present
    fraud = data.get("fraud_abuse") or {}
    if isinstance(fraud, dict) and str(fraud.get("summary") or "").strip():
        story.append(Paragraph("<b>FWA / integrity notes:</b>", styles["body"]))
        story.append(Paragraph(_esc(fraud.get("summary")), styles["body"]))
        for finding in (fraud.get("findings") or [])[:6]:
            if not isinstance(finding, dict):
                continue
            ind = str(finding.get("indicator") or "").strip()
            ev = str(finding.get("evidence") or "").strip()
            if ind or ev:
                story.append(_bullet(" ".join(p for p in (ind, ev) if p), styles))

    checklist = _checklist_status(data)
    if checklist:
        story.append(Spacer(1, 4))
        story.append(Paragraph("<b>Documentation / clinical checklist:</b>", styles["body"]))
        cl_rows = [[label, status] for label, status in checklist]
        story.append(_simple_table(
            ["Clinical / Document Area", "Available?", "Remarks"],
            [[a, b, ""] for a, b in cl_rows],
            [220, 90, 190],
            styles,
        ))

    # 5. Timeline Review
    story.append(Paragraph("5. Timeline Review", styles["section"]))
    tl_rows: List[List[str]] = []
    for item in data.get("timeline") or []:
        if not isinstance(item, dict):
            continue
        tl_rows.append([
            str(item.get("date") or item.get("when") or "—"),
            str(item.get("event") or item.get("description") or "—"),
        ])
    if not tl_rows:
        if claim.get("date_of_admission"):
            tl_rows.append([str(claim.get("date_of_admission")), "Admitted"])
        if claim.get("date_of_discharge"):
            tl_rows.append([str(claim.get("date_of_discharge")), "Discharged"])
    if not tl_rows:
        tl_rows = [["—", "Timeline not documented"]]
    story.append(_simple_table(["Date", "Event"], tl_rows[:20], [140, 360], styles))

    # 6. Observations — narrative prose only (no Q1/Ans)
    story.append(Paragraph("6. Observations", styles["section"]))
    story.append(Paragraph(_esc(_observation_narrative(data)), styles["body"]))

    observations = data.get("observations") or []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        q = str(obs.get("question") or "").strip()
        analysis = str(obs.get("analysis") or obs.get("justification") or "").strip()
        ans_label = str(obs.get("answer") or "").strip()
        # Skip stub / short items
        body = analysis
        if not body and ans_label and len(ans_label) > 60:
            body = ans_label
        if not body:
            continue
        # Strip accidental Q/Ans prefixes from model output
        body = re.sub(r"(?i)^\s*ans\.?\s*", "", body).strip()
        if q:
            story.append(Paragraph(f"<b>{_esc(_topic_heading(q))}</b>", styles["q"]))
        if ans_label and ans_label.lower() not in {"supported", "not supported", "partially supported", "insufficient evidence"}:
            story.append(Paragraph(_esc(body), styles["body"]))
        else:
            # Optionally lead with stance in prose, not "Ans. Supported."
            if ans_label and analysis:
                story.append(Paragraph(
                    _esc(f"({ans_label}) {analysis}"),
                    styles["body"],
                ))
            else:
                story.append(Paragraph(_esc(body), styles["body"]))

    # Billing disallowances as narrative bullets under observations (not Q&A)
    disallow_rows = _billing_disallowance_rows(data)
    if disallow_rows:
        story.append(Paragraph("<b>Billing / financial observations</b>", styles["q"]))
        for row in disallow_rows[:12]:
            title = str(row.get("title") or row.get("item") or "Disallowance").strip()
            amt = str(row.get("amount") or row.get("deduction_amount") or "").strip()
            reason = str(row.get("reason") or row.get("evidence") or "").strip()
            action = str(row.get("audit_action") or row.get("recommendation") or "").strip()
            head = title + (f" — {amt}" if amt else "")
            detail = " ".join(p for p in (reason, action) if p)
            story.append(_bullet(f"{head}. {detail}".strip(), styles))
        if fin.get("total_hospital_bill") or fin.get("non_payable_amount"):
            story.append(Paragraph(
                _esc(
                    f"Financial summary: Total hospital bill {_na(fin.get('total_hospital_bill'))}; "
                    f"non-payable / patient liability {_na(fin.get('non_payable_amount') or fin.get('patient_liability'))}; "
                    f"net claimable / recommended {_na(fin.get('net_claimable_amount') or fin.get('recommended_approval_amount'))}."
                ),
                styles["body"],
            ))

    # 7. Inference
    story.append(Paragraph("7. Inference", styles["section"]))
    inference = str(data.get("inference") or "").strip()
    if not inference:
        inference = str(data.get("auditor_conclusion") or data.get("compliance_verdict") or "").strip()
    if not inference:
        inference = "Inference is based on the available clinical records and policy documents reviewed for this audit."
    story.append(Paragraph(_esc(inference), styles["body"]))

    # 8. Auditor's Conclusion
    story.append(Paragraph("8. Auditor's Conclusion", styles["section"]))
    conclusion = str(data.get("auditor_conclusion") or data.get("inference") or "").strip()
    recommended = str(data.get("claim_recommended") or "").strip()
    if recommended:
        story.append(Paragraph(
            _esc(f"Claim recommendation: {recommended}."),
            styles["body"],
        ))
    if conclusion:
        story.append(Paragraph(_esc(conclusion), styles["body"]))
    verdict = str(data.get("compliance_verdict") or "").strip()
    if verdict:
        story.append(Paragraph(_esc(f"Guideline / compliance assessment: {verdict}."), styles["body"]))

    # 9. Remarks
    story.append(Paragraph("9. Remarks", styles["section"]))
    remarks = str(data.get("remarks") or "").strip() or (
        "This report is based on available documents. We recommend that all future "
        "hospital admission records include complete clinical notes to support claim "
        "validation and avoid ambiguity in insurance claims."
    )
    story.append(Paragraph(_esc(remarks), styles["body"]))
    story.append(Spacer(1, 16))

    auditor_name = _na(data.get("auditor_name") or _DEFAULT_AUDITOR_NAME)
    auditor_qual = _na(data.get("auditor_qualification") or _DEFAULT_AUDITOR_QUAL)
    auditor_role = _na(data.get("auditor_role") or _DEFAULT_AUDITOR_ROLE)
    sign_block = [
        Paragraph("<b>Auditor Name & Signature:</b>", styles["sign"]),
        Spacer(1, 28),
        Paragraph(_esc(auditor_name), styles["sign"]),
        Paragraph(_esc(auditor_qual), styles["sign"]),
        Paragraph(_esc(auditor_role), styles["sign"]),
        Paragraph("Glowix Medical Services Pvt. Ltd.", styles["sign"]),
        Paragraph(_esc(_ADDRESS), styles["sign"]),
    ]
    story.append(KeepTogether(sign_block))

    doc.build(story)
    return filename


def generate_glowix_expert_opinion_pdf(data: dict, filename: str = "audit_report.pdf") -> str:
    """Backward-compatible alias — downloads now use Medical Audit Report proforma."""
    return generate_glowix_medical_audit_report_pdf(data, filename)

