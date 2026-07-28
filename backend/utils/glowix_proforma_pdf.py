"""Glowix MEDICAL AUDIT – EXPERT OPINION proforma PDF.

Generates the client-facing letter format matching Glowix Medical Services'
standard expert-opinion layout (patient/policy, admission, documentation
checklist, treatment/billing, financial, observations Q&A, conclusion, remarks).
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

# Brand / letterhead constants (from Glowix Expert Opinion proforma)
_COMPANY = "GLOWIX MEDICAL SERVICES PRIVATE LIMITED"
_CIN = "U70200HR2024PTC122256"
_GSTIN = "06AALCG2970R1ZV"
_ADDRESS = "543-D, Pace City - II, Sector 37, Gurugram-122001"
_PHONE = "0124 - 4127700"
_EMAIL = "Info@glowixhealth.in"
_WEBSITE = "www.glowixhealth.in"
_DEFAULT_AUDITOR_NAME = "DR. Virender Nagpal"
_DEFAULT_AUDITOR_QUAL = "M.S Ortho"
_DEFAULT_AUDITOR_ROLE = "Advisor"

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
    if not s or s.lower() in {"none", "null", "unknown", "not specified", "—", "-"}:
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
    return f"{sex} / {age_part}"


def _checklist_status(data: dict) -> List[Tuple[str, str]]:
    """Map software checklist / sources into Glowix documentation rows."""
    rows_def = [
        ("Pre-authorization Approval Letter", ("pre-auth", "preauth", "authorization")),
        ("Admission Request Form", ("admission request", "admission form", "indoor")),
        ("Policy Copy / ID Card", ("policy", "id card")),
        ("Indoor Case Papers", ("indoor", "treatment sheet", "case paper")),
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


def generate_glowix_expert_opinion_pdf(data: dict, filename: str = "audit_report.pdf") -> str:
    """Write Glowix Expert Opinion proforma PDF; return filename."""
    styles = _build_styles()
    report_dt = _parse_report_date(data)
    ref = _default_ref(data, report_dt)
    dated = report_dt.strftime("%d-%m-%Y")

    patient = data.get("patient_details") or {}
    insurance = data.get("insurance_details") or {}
    claim = data.get("claim_details") or {}
    tba = data.get("treatment_billing_audit") or {}
    fin = data.get("financial_review") or {}
    savings = data.get("claim_savings") or {}

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

    # Ref / date line
    meta = Table(
        [[
            Paragraph(f"<b>Ref. No. :</b> {_esc(ref)}", styles["meta"]),
            Paragraph(f"<b>Dated :</b> {_esc(dated)}", styles["meta"]),
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
    story.append(Paragraph("MEDICAL AUDIT – EXPERT OPINION", styles["title"]))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#CCCCCC")))

    # 1. Patient & policy
    story.append(Paragraph("1. PATIENT & POLICY INFORMATION", styles["section"]))
    story.append(_kv_block([
        ("Patient Name", _na(patient.get("name"))),
        ("Age / Gender", _age_gender(patient)),
        ("Hospital Reg. No.", _na(patient.get("hospital_reg_no") or patient.get("uhid"))),
        ("Insurance Company", _na(insurance.get("insurance_company"))),
        ("TPA (if applicable)", _na(insurance.get("tpa"))),
        ("Policy No.", _na(insurance.get("policy_number"))),
        ("Claim Incident No.", _na(insurance.get("claim_incident_number"))),
    ], styles))

    # 2. Admission & diagnosis
    story.append(Paragraph("2. ADMISSION & DIAGNOSIS", styles["section"]))
    story.append(_kv_block([
        ("Name of Hospital", _na(claim.get("hospital"))),
        ("Date & Time of Admission", _na(claim.get("date_of_admission"))),
        ("Nature of Admission (Emergency / Planned)", _na(claim.get("nature_of_admission"))),
        ("Provisional Diagnosis", _na(claim.get("provisional_diagnosis") or claim.get("diagnosis"))),
        ("Final Diagnosis", _na(claim.get("final_diagnosis") or claim.get("diagnosis"))),
        ("Procedure / Surgery", _na(claim.get("procedure_or_surgery"))),
        ("Date & Time of Surgery/Procedure", _na(claim.get("procedure_date"))),
    ], styles))

    # 3. Documentation checklist
    story.append(Paragraph("3. DOCUMENTATION CHECKLIST", styles["section"]))
    checklist = _checklist_status(data)
    story.append(_kv_block(checklist, styles))

    # 4. Treatment & billing
    story.append(Paragraph("4. TREATMENT & BILLING AUDIT", styles["section"]))
    story.append(_kv_block([
        ("Room Category Admitted", _na(tba.get("room_category_admitted"))),
        ("Room Category Eligible (per policy)", _na(tba.get("room_category_eligible"))),
        ("Procedures performed", _na(tba.get("procedures_performed") or claim.get("procedure_or_surgery"))),
        ("Cross-checked with Pre-Auth", _na(tba.get("cross_checked_with_preauth"))),
        ("Excluded items billed", _na(tba.get("excluded_items_billed"))),
        ("Charges appropriate", _na(tba.get("charges_appropriate"))),
    ], styles))

    # 5. Financial review
    story.append(Paragraph("5. FINANCIAL REVIEW", styles["section"]))
    story.append(_kv_block([
        (
            "Total Hospital Bill",
            _na(savings.get("total_claim_amount") or fin.get("total_hospital_bill") or claim.get("total_hospital_bill")),
        ),
        ("Non-Payable Amount", _na(fin.get("non_payable_amount"))),
        (
            "Net Claimable Amount",
            _na(savings.get("admissible_amount") or fin.get("net_claimable_amount")),
        ),
        (
            "Amount Recommended for Approval",
            _na(fin.get("recommended_approval_amount") or savings.get("admissible_amount")),
        ),
        ("Patient Liability (if any)", _na(fin.get("patient_liability"))),
    ], styles))

    # 6. Auditor observations
    story.append(Paragraph("6. AUDITOR'S OBSERVATIONS", styles["section"]))
    story.append(_kv_block([
        ("Any Missing Documents?", _missing_documents_answer(checklist)),
        ("Diagnosis vs Treatment Appropriate", "Following are the observations-"),
    ], styles))
    story.append(Spacer(1, 4))
    story.append(Paragraph(_esc(_observation_narrative(data)), styles["body"]))

    observations = data.get("observations") or []
    for idx, obs in enumerate(observations, start=1):
        if not isinstance(obs, dict):
            continue
        q = str(obs.get("question") or "").strip()
        a = str(obs.get("analysis") or obs.get("answer") or "").strip()
        if not q and not a:
            continue
        story.append(Paragraph(f"Q{idx}. {_esc(q)}", styles["q"]))
        ans_label = str(obs.get("answer") or "").strip()
        body = a
        if ans_label and ans_label.lower() not in body.lower()[:80]:
            body = f"Ans. {ans_label}. {a}".strip()
        elif not body.lower().startswith("ans"):
            body = f"Ans. {a}"
        story.append(Paragraph(_esc(body), styles["a"]))

    # Extra clinical Q&A if present
    for qa in data.get("qa_section") or []:
        if not isinstance(qa, dict):
            continue
        q = str(qa.get("question") or "").strip()
        a = str(qa.get("answer") or "").strip()
        just = str(qa.get("justification") or "").strip()
        if not q:
            continue
        story.append(Paragraph(f"Q. {_esc(q)}", styles["q"]))
        story.append(Paragraph(_esc(f"Ans. {a}" + (f" {just}" if just else "")), styles["a"]))

    story.append(_kv_block([
        ("Evidence of Over-billing?", _na((data.get("fraud_abuse") or {}).get("overbilling") or "NA")),
        ("Compliance with Guidelines?", _na(data.get("compliance_verdict"))),
    ], styles))

    # 7. Conclusion
    story.append(Paragraph("7. CONCLUSION", styles["section"]))
    conclusion = str(
        data.get("inference")
        or data.get("auditor_conclusion")
        or ""
    ).strip()
    if not conclusion:
        bullets = data.get("report_summary") or []
        conclusion = " ".join(str(b) for b in bullets[:4]) if bullets else "NA"
    story.append(Paragraph(_esc(conclusion), styles["body"]))

    # 8. Remarks
    story.append(Paragraph("8. REMARKS", styles["section"]))
    remarks = str(data.get("remarks") or "").strip() or (
        "This report is based on available documents. We recommend that all future "
        "OPD/Hospital admission records should include complete clinical notes to support "
        "claim validation. Hospitals and clinics must follow standard documentation "
        "practices to avoid ambiguity in insurance claims."
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
