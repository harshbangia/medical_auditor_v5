from reportlab.platypus import *
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

def generate_pdf(data, filename="audit_report.pdf"):

    doc = SimpleDocTemplate(filename)
    styles = getSampleStyleSheet()
    content = []

    # =========================
    # TITLE
    # =========================
    content.append(Paragraph("MEDICAL AUDIT REPORT", styles["Title"]))
    content.append(Spacer(1, 12))

    # =========================
    # GUIDELINE USED
    # =========================
    content.append(Paragraph("Guideline Referenced", styles["Heading2"]))
    content.append(Paragraph(data.get("guideline_used", "-"), styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # PATIENT DETAILS
    # =========================
    content.append(Paragraph("1. Patient Details", styles["Heading2"]))
    p = data.get("patient_details", {})

    content.append(Paragraph(f"Name: {p.get('name','')}", styles["Normal"]))
    content.append(Paragraph(f"Age: {p.get('age','')}", styles["Normal"]))
    content.append(Paragraph(f"Sex: {p.get('sex','')}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # CLAIM DETAILS
    # =========================
    content.append(Paragraph("2. Claim Details", styles["Heading2"]))
    c = data.get("claim_details", {})

    content.append(Paragraph(f"Hospital: {c.get('hospital','')}", styles["Normal"]))
    content.append(Paragraph(f"Diagnosis: {c.get('diagnosis','')}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # IMAGING FINDINGS (NEW)
    # =========================
    if data.get("imaging_findings"):
        content.append(Paragraph("3. Imaging Findings", styles["Heading2"]))

        for img in data.get("imaging_findings", []):
            content.append(Paragraph(
                f"{img.get('type','')} - {img.get('finding','')}",
                styles["Normal"]
            ))
            content.append(Paragraph(
                f"Clinical Correlation: {img.get('clinical_correlation','')}",
                styles["Normal"]
            ))
            content.append(Paragraph(
                f"Consistency: {img.get('consistency_with_diagnosis','')}",
                styles["Normal"]
            ))
            content.append(Spacer(1, 5))

        content.append(Spacer(1, 10))

    # =========================
    # CLINICAL FINDINGS
    # =========================
    content.append(Paragraph("4. Clinical Findings", styles["Heading2"]))

    for item in data.get("clinical_findings", []):
        content.append(Paragraph(
            f"{item.get('parameter')} - {item.get('value')} ({item.get('comment')})",
            styles["Normal"]
        ))

    content.append(Spacer(1, 10))

    # =========================
    # DOCUMENTATION GAPS
    # =========================
    content.append(Paragraph("5. Documentation Gaps", styles["Heading2"]))

    for gap in data.get("documentation_gaps", []):
        content.append(Paragraph(f"- {gap}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # =========================
    # TIMELINE
    # =========================
    content.append(Paragraph("6. Timeline", styles["Heading2"]))

    for t in data.get("timeline", []):
        content.append(Paragraph(f"{t.get('date')} - {t.get('event')}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # =========================
    # OBSERVATIONS
    # =========================
    content.append(Paragraph("7. Observations", styles["Heading2"]))

    for obs in data.get("observations", []):
        content.append(Paragraph(f"Q: {obs.get('question')}", styles["Normal"]))
        content.append(Paragraph(f"Analysis: {obs.get('analysis')}", styles["Normal"]))
        content.append(Paragraph(f"Answer: {obs.get('answer')}", styles["Normal"]))
        content.append(Spacer(1, 5))

    content.append(Spacer(1, 10))

    # =========================
    # CONCLUSION
    # =========================
    content.append(Paragraph("8. Auditor’s Conclusion", styles["Heading2"]))
    content.append(Paragraph(data.get("auditor_conclusion", ""), styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # REMARKS
    # =========================
    content.append(Paragraph("9. Remarks", styles["Heading2"]))
    content.append(Paragraph(data.get("remarks", ""), styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # Q&A SECTION (🔥 NEW)
    # =========================
    if "qa_section" in data and data["qa_section"]:
        content.append(Paragraph("10. Questions & Answers", styles["Heading2"]))

        for qa in data["qa_section"]:
            content.append(Paragraph(f"Q: {qa.get('question')}", styles["Normal"]))
            content.append(Paragraph(f"A: {qa.get('answer')}", styles["Normal"]))
            content.append(Paragraph(f"Justification: {qa.get('justification')}", styles["Normal"]))
            content.append(Spacer(1, 5))

    # =========================
    # BUILD PDF
    # =========================
    doc.build(content)