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


def _money_num(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = re.sub(r"[^\d.]", "", str(raw))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_inr(amount: float) -> str:
    # Keep simple Indian-style grouping-ish display without locale deps
    whole = int(round(amount))
    return f"Rs. {whole:,}"


def _billing_disallowance_rows(data: dict) -> List[dict]:
    rows = data.get("billing_disallowances") or data.get("recommended_deductions") or []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _documentation_gap_rows(data: dict) -> List[Any]:
    gaps = data.get("documentation_gaps") or []
    return gaps if isinstance(gaps, list) else []


def _sum_disallowances(data: dict) -> Optional[float]:
    total = 0.0
    found = False
    for row in _billing_disallowance_rows(data):
        n = _money_num(row.get("amount") or row.get("deduction_amount") or row.get("rupees"))
        if n is not None:
            total += n
            found = True
    return total if found else None


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
    disallow_sum = _sum_disallowances(data)
    non_pay_raw = fin.get("non_payable_amount")
    non_pay_num = _money_num(non_pay_raw)
    if disallow_sum is not None and (non_pay_num is None or non_pay_num < disallow_sum):
        non_pay_display = _fmt_inr(disallow_sum)
    else:
        non_pay_display = _na(non_pay_raw)
    story.append(_kv_block([
        (
            "Total Hospital Bill",
            _na(savings.get("total_claim_amount") or fin.get("total_hospital_bill") or claim.get("total_hospital_bill")),
        ),
        ("Non-Payable Amount", non_pay_display),
        (
            "Net Claimable Amount",
            _na(savings.get("admissible_amount") or fin.get("net_claimable_amount")),
        ),
        (
            "Amount Recommended for Approval",
            _na(fin.get("recommended_approval_amount") or savings.get("admissible_amount")),
        ),
        ("Patient Liability (if any)", _na(fin.get("patient_liability") or (non_pay_display if disallow_sum else None))),
    ], styles))

    disallow_rows = _billing_disallowance_rows(data)
    if disallow_rows:
        story.append(Spacer(1, 4))
        story.append(Paragraph("<b>Itemised billing disallowances / queries:</b>", styles["body"]))
        for i, row in enumerate(disallow_rows[:12], start=1):
            title = str(row.get("title") or row.get("item") or row.get("category") or "Disallowance").strip()
            amt = str(row.get("amount") or row.get("deduction_amount") or "").strip()
            reason = str(row.get("reason") or row.get("evidence") or row.get("finding") or "").strip()
            action = str(row.get("audit_action") or row.get("recommendation") or "").strip()
            head = f"{i}. {title}"
            if amt:
                head += f" — {amt}"
            story.append(Paragraph(_esc(head), styles["q"]))
            body_bits = [b for b in (reason, action) if b]
            if body_bits:
                story.append(Paragraph(_esc(" ".join(body_bits)), styles["a"]))

    # 6. FWA Investigation (Case Notebook)
    fwa_rows = data.get("fwa_investigation") or (data.get("fraud_abuse") or {}).get("findings") or []
    if isinstance(fwa_rows, list) and fwa_rows:
        story.append(Paragraph("6. FWA INVESTIGATION (CASE NOTEBOOK)", styles["section"]))
        risk = _na((data.get("fraud_abuse") or {}).get("risk_level"))
        summary = str((data.get("fraud_abuse") or {}).get("summary") or "").strip()
        story.append(_kv_block([
            ("Overall FWA Risk", risk),
        ], styles))
        if summary:
            story.append(Paragraph(_esc(summary), styles["body"]))
        for i, row in enumerate(fwa_rows[:10], start=1):
            if not isinstance(row, dict):
                continue
            ind = str(row.get("indicator") or "").strip()
            if not ind:
                continue
            sev = str(row.get("severity") or "").strip()
            ev = str(row.get("evidence") or "").strip()
            rec = str(row.get("recommendation") or "").strip()
            cite = row.get("citation") or {}
            cite_bits = []
            if cite.get("filename"):
                cite_bits.append(str(cite["filename"]))
            if cite.get("page"):
                cite_bits.append(f"p.{cite['page']}")
            cite_lbl = f" [{', '.join(cite_bits)}]" if cite_bits else ""
            story.append(Paragraph(
                _esc(f"{i}. [{sev or 'Medium'}] {ind}{cite_lbl}"),
                styles["q"],
            ))
            body = ev
            if rec:
                body = f"{ev} Recommendation: {rec}".strip()
            if body:
                story.append(Paragraph(_esc(body), styles["a"]))

    # 7. Auditor observations
    story.append(Paragraph("7. AUDITOR'S OBSERVATIONS", styles["section"]))
    story.append(_kv_block([
        ("Any Missing Documents?", _missing_documents_answer(checklist)),
        ("Diagnosis vs Treatment Appropriate", "Following are the observations-"),
    ], styles))
    story.append(Spacer(1, 4))
    story.append(Paragraph(_esc(_observation_narrative(data)), styles["body"]))

    gap_rows = _documentation_gap_rows(data)
    if gap_rows:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Documentation & forensic gaps:</b>", styles["body"]))
        for i, gap in enumerate(gap_rows[:10], start=1):
            if isinstance(gap, str):
                text = gap.strip()
                if text:
                    story.append(Paragraph(_esc(f"{i}. {text}"), styles["a"]))
                continue
            if not isinstance(gap, dict):
                continue
            title = str(gap.get("title") or gap.get("gap") or gap.get("category") or "Gap").strip()
            finding = str(
                gap.get("finding")
                or gap.get("forensic_finding")
                or gap.get("description")
                or gap.get("detail")
                or ""
            ).strip()
            evidence = str(gap.get("evidence") or "").strip()
            action = str(gap.get("audit_action") or gap.get("recommendation") or "").strip()
            story.append(Paragraph(_esc(f"{i}. {title}"), styles["q"]))
            body = " ".join(b for b in (finding, evidence, action) if b)
            if body:
                story.append(Paragraph(_esc(body), styles["a"]))

    observations = data.get("observations") or []
    for idx, obs in enumerate(observations, start=1):
        if not isinstance(obs, dict):
            continue
        q = str(obs.get("question") or "").strip()
        analysis = str(obs.get("analysis") or obs.get("justification") or "").strip()
        ans_label = str(obs.get("answer") or "").strip()
        # Prefer long analysis; avoid using short Supported/Not Supported as the whole answer body
        a = analysis or (ans_label if len(ans_label) > 40 else "")
        if not q and not a:
            continue
        story.append(Paragraph(f"Q{idx}. {_esc(q)}", styles["q"]))
        if ans_label and analysis:
            body = f"Ans. {ans_label}. {analysis}".strip()
        elif ans_label and not analysis:
            body = f"Ans. {ans_label}"
        elif a and not a.lower().startswith("ans"):
            body = f"Ans. {a}"
        else:
            body = a or "Ans. Insufficient Evidence"
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

    # 8. Conclusion / Final audit decision
    story.append(Paragraph("8. CONCLUSION", styles["section"]))
    recommended = str(
        data.get("claim_recommended")
        or data.get("claim_recommendation")
        or ""
    ).strip()
    not_recommended = str(data.get("claim_not_recommended") or "").strip()
    if recommended or not_recommended:
        story.append(_kv_block([
            ("Claim Recommended", _na(recommended or "NA")),
            ("Claim Not Recommended", _na(not_recommended or ("NA" if recommended.lower() in {"yes", "y"} else ""))),
        ], styles))
    conclusion = str(
        data.get("inference")
        or data.get("auditor_conclusion")
        or ""
    ).strip()
    if not conclusion:
        bullets = data.get("report_summary") or []
        conclusion = " ".join(str(b) for b in bullets[:4]) if bullets else "NA"
    story.append(Paragraph(_esc(conclusion), styles["body"]))

    # 9. Remarks
    story.append(Paragraph("9. REMARKS", styles["section"]))
    remarks = str(data.get("remarks") or "").strip() or (
        "This report is based on available documents and Case Notebook grounded review. "
        "We recommend that all future OPD/Hospital admission records should include "
        "complete clinical notes to support claim validation. Hospitals and clinics must "
        "follow standard documentation practices to avoid ambiguity in insurance claims."
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
