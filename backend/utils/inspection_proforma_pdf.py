"""QCI / NABCB document-verification inspection report (GMS/FOR/DV/01)."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from backend.utils.glowix_proforma_pdf import (
    _ADDRESS,
    _BLUE,
    _CIN,
    _COMPANY,
    _EMAIL,
    _GRAY,
    _GSTIN,
    _PHONE,
    _RED,
    _WEBSITE,
    _esc,
    _logo_path,
)
from backend.utils.inspection_mapper import (
    AUTHENTICITY_OPTIONS,
    CHRONOLOGY_OPTIONS,
    COMPLETENESS_OPTIONS,
    CONCLUSION_OPTIONS,
    CONFORMITY_OPTIONS,
    CONSISTENCY_OPTIONS,
    CORRELATION_OPTIONS,
    TRACEABILITY_OPTIONS,
    map_audit_to_inspection,
)

_PAGE_W, _PAGE_H = A4
_LEFT = 14 * mm
_RIGHT = 14 * mm
_TOP = 28 * mm
_BOTTOM = 22 * mm
_CONTENT_W = _PAGE_W - _LEFT - _RIGHT


def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "InspTitle",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            alignment=TA_CENTER,
            leading=15,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "InspSub",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            alignment=TA_CENTER,
            leading=12,
            textColor=_BLUE,
            spaceAfter=2,
        ),
        "section": ParagraphStyle(
            "InspSection",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "InspBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11,
            alignment=TA_JUSTIFY,
            spaceAfter=3,
        ),
        "cell": ParagraphStyle(
            "InspCell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            alignment=TA_LEFT,
        ),
        "head": ParagraphStyle(
            "InspHead",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9.5,
        ),
        "check": ParagraphStyle(
            "InspCheck",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            spaceAfter=1,
        ),
    }


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_esc(text if str(text or "").strip() else "—"), style)


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[float], styles: Dict[str, ParagraphStyle]) -> Table:
    data: List[List[Any]] = [[_p(header, styles["head"]) for header in headers]]
    for row in rows:
        data.append([_p(cell, styles["cell"]) for cell in row])
    table = Table(data, colWidths=list(widths), repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _kv(pairs: Sequence[tuple], styles: Dict[str, ParagraphStyle]) -> Table:
    data = [[_p(label, styles["head"]), _p(value, styles["cell"])] for label, value in pairs]
    table = Table(data, colWidths=[190, _CONTENT_W - 190])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _checks(selected: str, options: Sequence[tuple], styles: Dict[str, ParagraphStyle]) -> List[Paragraph]:
    lines = []
    for code, label in options:
        mark = "[X]" if selected == code else "[ ]"
        lines.append(Paragraph(f"{mark} {_esc(label)}", styles["check"]))
    return lines


def _checks_plain(selected: str, options: Sequence[str], styles: Dict[str, ParagraphStyle]) -> List[Paragraph]:
    lines = []
    for label in options:
        mark = "[X]" if selected == label else "[ ]"
        lines.append(Paragraph(f"{mark} {_esc(label)}", styles["check"]))
    return lines


def _letterhead(canvas, doc) -> None:
    canvas.saveState()
    logo = _logo_path()
    y_top = _PAGE_H - 10 * mm
    text_x = _LEFT
    if logo:
        try:
            canvas.drawImage(logo, _LEFT, y_top - 12 * mm, width=12 * mm, height=12 * mm, mask="auto")
            text_x = _LEFT + 14 * mm
        except Exception:
            text_x = _LEFT
    canvas.setFillColor(_RED)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(text_x, y_top - 4 * mm, _COMPANY)
    canvas.setFillColor(_BLUE)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(text_x, y_top - 8 * mm, f"(CIN No. : {_CIN})")
    canvas.setStrokeColor(_RED)
    canvas.setLineWidth(1)
    canvas.line(_LEFT, 16 * mm, _PAGE_W - _RIGHT, 16 * mm)
    canvas.setFillColor(_GRAY)
    canvas.setFont("Helvetica", 7)
    canvas.drawCentredString(_PAGE_W / 2, 11.5 * mm, f"(GSTIN/UIN : {_GSTIN})")
    canvas.drawCentredString(_PAGE_W / 2, 8 * mm, _ADDRESS)
    canvas.drawCentredString(_PAGE_W / 2, 4.5 * mm, f"Ph. : {_PHONE}, E-mail: {_EMAIL} | {_WEBSITE}")
    canvas.restoreState()


class _NumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.setFillColor(_GRAY)
            self.setFont("Helvetica", 8)
            self.drawRightString(
                _PAGE_W - _RIGHT,
                18 * mm,
                f"Page {self._pageNumber} of {page_count}",
            )
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)


def _personnel_block(title: str, person: dict, styles: Dict[str, ParagraphStyle]) -> List[Any]:
    lines = [
        Paragraph(title, styles["section"]),
        _kv([
            ("Name", person.get("name") or "__________________________"),
            ("Designation", person.get("designation") or "__________________________"),
            ("Authorization No.", person.get("authorization_no") or "__________________________"),
            ("Signature", "__________________________"),
            ("Date", person.get("date") or "__________________________"),
        ], styles),
    ]
    return lines


def generate_inspection_report_pdf(data: dict, filename: str = "inspection_report.pdf") -> str:
    """Write the GMS/FOR/DV/01 inspection report for a stored audit case."""
    report = map_audit_to_inspection(data or {})
    styles = _styles()
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
        _CONTENT_W,
        _PAGE_H - _TOP - _BOTTOM,
        id="normal",
    )
    doc.addPageTemplates([PageTemplate(id="inspection", frames=frame, onPage=_letterhead)])
    story: List[Any] = []
    client = report["client"]
    patient = report["patient"]

    story.append(Paragraph("DOCUMENT VERIFICATION INSPECTION REPORT", styles["title"]))
    story.append(Paragraph("IAF SCOPE 35 – OTHER SERVICES", styles["subtitle"]))
    story.append(Paragraph(
        "Hospital &amp; Health Insurance Claim Documents and Supporting Records",
        styles["subtitle"],
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph("DOCUMENT CONTROL", styles["section"]))
    story.append(_kv([
        ("Document No.", report["document_no"]),
        ("Revision No.", report["revision_no"]),
        ("Effective Date", report["effective_date"]),
        ("Report No.", report["report_no"]),
        ("Inspection Reference No.", report["inspection_reference_no"]),
        ("Confidentiality", report["confidentiality"]),
    ], styles))

    story.append(Paragraph("1. CLIENT / ASSIGNMENT DETAILS", styles["section"]))
    story.append(_kv([
        ("Client / Organization", client["organization"]),
        ("Client Reference No.", client["client_reference_no"]),
        ("Claim Reference No.", client["claim_reference_no"]),
        ("Work Order / Assignment No.", client["work_order_no"]),
        ("Date of Receipt", client["date_of_receipt"]),
        ("Date of Inspection / Verification", client["date_of_inspection"]),
        ("Date of Report", client["date_of_report"]),
        ("Mode of Documents Received", client["mode_received"]),
        ("Number of Documents Received", client["document_count"]),
        ("Inspection Location / Mode", client["location_mode"]),
    ], styles))

    story.append(Paragraph("2. PATIENT / CLAIM IDENTIFICATION", styles["section"]))
    story.append(_kv([
        ("Patient Name", patient["name"]),
        ("Age / DOB", patient["age_dob"]),
        ("Gender", patient["gender"]),
        ("UHID / MRN", patient["uhid"]),
        ("IPD / Admission No.", patient["ipd"]),
        ("Policy / Member ID", patient["policy"]),
        ("Claim No.", patient["claim_no"]),
        ("Hospital / Healthcare Provider", patient["hospital"]),
        ("Date of Admission", patient["admission"]),
        ("Date of Discharge", patient["discharge"]),
        ("Diagnosis as Recorded", patient["diagnosis"]),
        ("Procedure / Surgery as Recorded", patient["procedure"]),
        ("Claim Type", patient["claim_type"]),
    ], styles))

    story.append(Paragraph("3. SCOPE OF INSPECTION", styles["section"]))
    story.append(Paragraph(
        "The inspection is limited to document verification of hospital, health-insurance claim "
        "and supporting records submitted/made available for inspection.",
        styles["body"],
    ))
    story.append(Paragraph("The inspection includes, as applicable:", styles["body"]))
    for line in (
        "Identification of documents",
        "Completeness assessment",
        "Chronology verification",
        "Internal consistency assessment",
        "Cross-document verification",
        "Traceability assessment",
        "Authenticity-indicator assessment",
        "Documentary correlation of clinical, investigation, treatment, procedure and billing records",
        "Identification and classification of discrepancies",
        "Conformity determination against the specified inspection criteria",
    ):
        story.append(Paragraph(f"• {_esc(line)}", styles["body"]))

    story.append(Paragraph("4. APPLICABLE INSPECTION CRITERIA / METHOD", styles["section"]))
    story.append(_kv([
        ("Inspection Standard", "ISO/IEC 17020 – applicable requirements"),
        ("IAF Sector", "Scope 35 – Other Services"),
        ("Inspection SOP", "GMS/SOP/DV/01"),
        ("Inspection Checklist", "GMS/CHK/DV/01"),
        ("Client Contract / Work Order", client["work_order_no"]),
        ("Applicable Client Requirements", client["organization"]),
        ("Other Specified Criteria", "Document verification of records submitted for this assignment"),
    ], styles))

    story.append(Paragraph("5. DOCUMENT INVENTORY", styles["section"]))
    story.append(_table(
        ["Sr.", "Document", "Date", "Reference No.", "Pages", "Available", "Remarks"],
        [
            [row["sr"], row["document"], row["date"], row["reference_no"], row["pages"], row["available"], row["remarks"]]
            for row in report["inventory"]
        ],
        [22, 110, 52, 78, 36, 48, _CONTENT_W - 346],
        styles,
    ))

    story.append(Paragraph("6. IDENTIFICATION OF DOCUMENTS", styles["section"]))
    story.append(Paragraph(
        "Check document type, patient identity, hospital/source, UHID/IPD/claim linkage, date, "
        "reference number and other applicable identifiers. Status: C / NC / UC / NA.",
        styles["body"],
    ))
    story.append(_table(
        ["Sr.", "Verification Parameter", "Status", "Observation"],
        [[row["sr"], row["parameter"], row["status"], row["observation"]] for row in report["identification"]],
        [22, 150, 40, _CONTENT_W - 212],
        styles,
    ))
    story.append(Paragraph(f"<b>Identification Finding:</b> {_esc(report['identification_finding'])}", styles["body"]))

    story.append(Paragraph("7. COMPLETENESS ASSESSMENT", styles["section"]))
    story.append(Paragraph("Status: C complete / PC partially complete / IC incomplete / NA.", styles["body"]))
    story.append(_table(
        ["Parameter", "Status", "Observation"],
        [[row["parameter"], row["status"], row["observation"]] for row in report["completeness_rows"]],
        [170, 50, _CONTENT_W - 220],
        styles,
    ))
    story.append(Paragraph("<b>Overall Completeness:</b>", styles["body"]))
    story.extend(_checks_plain(report["overall_completeness"], COMPLETENESS_OPTIONS, styles))
    story.append(Paragraph(f"<b>Missing / Required Documents:</b> {_esc(report['missing_documents'])}", styles["body"]))

    story.append(Paragraph("8. CHRONOLOGY VERIFICATION", styles["section"]))
    story.append(_table(
        ["Event", "Date", "Time", "Source", "Status", "Observation"],
        [
            [row["event"], row["date"], row["time"], row["source"], row["status"], row["observation"]]
            for row in report["chronology"]
        ],
        [90, 55, 40, 70, 60, _CONTENT_W - 315],
        styles,
    ))
    story.append(Paragraph("<b>Overall Chronology:</b>", styles["body"]))
    story.extend(_checks_plain(report["overall_chronology"], CHRONOLOGY_OPTIONS, styles))
    story.append(Paragraph(f"<b>Chronology Observation:</b> {_esc(report['chronology_observation'])}", styles["body"]))

    story.append(Paragraph("9. INTERNAL CONSISTENCY", styles["section"]))
    story.append(_table(
        ["Parameter", "Records Compared", "Status", "Observation"],
        [[row["parameter"], row["records_compared"], row["status"], row["observation"]] for row in report["consistency"]],
        [100, 110, 90, _CONTENT_W - 300],
        styles,
    ))
    story.append(Paragraph("<b>Overall Internal Consistency:</b>", styles["body"]))
    story.extend(_checks_plain(report["overall_consistency"], CONSISTENCY_OPTIONS, styles))

    story.append(Paragraph("10. CROSS-DOCUMENT VERIFICATION", styles["section"]))
    story.append(_table(
        ["Primary Record", "Corroborating Record", "Parameter", "Result", "Observation"],
        [
            [row["primary"], row["corroborating"], row["parameter"], row["result"], row["observation"]]
            for row in report["cross_document"]
        ],
        [80, 90, 80, 36, _CONTENT_W - 286],
        styles,
    ))
    story.append(Paragraph(
        "Result codes: CV – Cross-Verified; PV – Partially Verified; D – Discrepancy; "
        "UR – Unreconciled; ND – Supporting Document Not Available; UC – Unable to Conclude; "
        "NA – Not Applicable.",
        styles["body"],
    ))

    story.append(Paragraph("11. TRACEABILITY / AUTHENTICITY INDICATORS", styles["section"]))
    story.append(_table(
        ["Indicator", "Present", "Verified Where Applicable", "Observation"],
        [[row["indicator"], row["present"], row["verified"], row["observation"]] for row in report["traceability"]],
        [130, 50, 90, _CONTENT_W - 270],
        styles,
    ))
    story.append(Paragraph("<b>Traceability Result:</b>", styles["body"]))
    story.extend(_checks_plain(report["traceability_result"], TRACEABILITY_OPTIONS, styles))
    story.append(Paragraph("<b>Authenticity Indicator Result:</b>", styles["body"]))
    story.extend(_checks_plain(report["authenticity_result"], AUTHENTICITY_OPTIONS, styles))
    if report.get("authenticity_note") and report["authenticity_note"] != "—":
        story.append(Paragraph(_esc(report["authenticity_note"]), styles["body"]))

    story.append(Paragraph("12. CLINICAL / DOCUMENTARY / BILLING CORRELATION", styles["section"]))
    story.append(_table(
        ["Claimed/Billed Item", "Supporting Record", "Correlation", "Observation"],
        [[row["item"], row["support"], row["correlation"], row["observation"]] for row in report["correlation"]],
        [90, 90, 110, _CONTENT_W - 290],
        styles,
    ))
    story.append(Paragraph("<b>Correlation Status:</b>", styles["body"]))
    story.extend(_checks_plain(report["correlation_status"], CORRELATION_OPTIONS, styles))

    story.append(Paragraph("13. DISCREPANCY / OBSERVATION REGISTER", styles["section"]))
    discrepancy_rows = report["discrepancies"] or [{
        "sr": "—",
        "reference": "—",
        "requirement": "—",
        "evidence": "No documentary discrepancy was recorded in the stored case file.",
        "classification": "—",
    }]
    story.append(_table(
        ["Sr.", "Document / Page / Reference", "Requirement", "Objective Evidence / Observation", "Class"],
        [
            [row["sr"], row["reference"], row["requirement"], row["evidence"], row["classification"]]
            for row in discrepancy_rows
        ],
        [22, 90, 80, _CONTENT_W - 232, 40],
        styles,
    ))
    story.append(Paragraph(
        "Classification: OBS – Observation; MD – Minor Discrepancy; MAT – Material Discrepancy; "
        "NC – Non-Conformity; UC – Unable to Conclude.",
        styles["body"],
    ))

    story.append(Paragraph("14. CONFORMITY ASSESSMENT", styles["section"]))
    story.append(Paragraph(
        "Based solely on the documents made available for inspection and against the specified inspection criteria:",
        styles["body"],
    ))
    story.extend(_checks(report["conformity_code"], CONFORMITY_OPTIONS, styles))
    story.append(_kv([
        ("Basis of Conformity Determination", report["conformity_basis"]),
        ("Requirement(s), if any, not fulfilled", report["requirements_not_fulfilled"]),
        ("Objective Evidence", report["objective_evidence"]),
    ], styles))

    story.append(Paragraph("15. OVERALL INSPECTION FINDINGS", styles["section"]))
    findings = report["overall_findings"]
    story.append(_kv([
        ("Identification", findings["identification"]),
        ("Completeness", findings["completeness"]),
        ("Chronology", findings["chronology"]),
        ("Internal Consistency", findings["consistency"]),
        ("Cross-Document Verification", findings["cross"]),
        ("Traceability / Authenticity Indicators", findings["traceability"]),
        ("Clinical / Billing Correlation", findings["correlation"]),
        ("Material Discrepancies, if any", findings["material"]),
    ], styles))

    story.append(Paragraph("16. INSPECTION CONCLUSION", styles["section"]))
    story.append(Paragraph(
        "Based on the hospital, health-insurance claim and supporting records submitted for inspection, "
        "and subject to the scope, criteria and limitations stated in this report, the inspected documents "
        "are classified as:",
        styles["body"],
    ))
    story.extend(_checks(report["conclusion_code"], CONCLUSION_OPTIONS, styles))
    story.append(Paragraph(f"<b>Conclusion / Remarks:</b> {_esc(report['conclusion_remarks'])}", styles["body"]))

    story.append(Paragraph("17. LIMITATIONS", styles["section"]))
    for index, line in enumerate(report["limitations"], start=1):
        story.append(Paragraph(f"{index}. {_esc(line)}", styles["body"]))

    story.append(Paragraph("18. INSPECTION PERSONNEL", styles["section"]))
    empty = {"name": "", "designation": "", "authorization_no": "", "date": ""}
    story.extend(_personnel_block("Prepared / Inspected By", report["prepared_by"], styles))
    story.extend(_personnel_block("Technically Reviewed By", empty, styles))
    story.extend(_personnel_block("Authorized / Approved By", empty, styles))

    story.append(Paragraph("19. REPORT CONTROL", styles["section"]))
    story.append(_kv([
        ("Report No.", report["report_no"]),
        ("Revision No.", report["revision_no"]),
        ("Original Issue Date", report["report_date"]),
        ("Amendment/Reissue Date", "—"),
        ("Supersedes Report No., if applicable", "—"),
        ("Reason for Amendment/Reissue", "—"),
        ("Total Pages", "As numbered in the page header"),
    ], styles))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#CBD5E1")))
    story.append(Paragraph("CONFIDENTIALITY STATEMENT", styles["section"]))
    story.append(Paragraph(
        "This report contains confidential information and shall be handled in accordance with the "
        "applicable confidentiality, information-security, record-control and retention requirements "
        "of Glowix Medical Services.",
        styles["body"],
    ))
    story.append(Paragraph("END OF REPORT", styles["subtitle"]))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return filename
