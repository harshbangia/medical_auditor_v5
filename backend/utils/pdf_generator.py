from reportlab.platypus import *
from reportlab.lib.styles import getSampleStyleSheet

def generate_pdf(data, filename="audit_report.pdf"):

    doc = SimpleDocTemplate(filename)
    styles = getSampleStyleSheet()
    content = []

    # Title
    content.append(Paragraph("MEDICAL AUDIT REPORT", styles["Title"]))
    content.append(Spacer(1, 10))

    # Patient Details
    content.append(Paragraph("1. Patient Details", styles["Heading2"]))
    p = data.get("patient_details", {})
    content.append(Paragraph(f"Name: {p.get('name','')}", styles["Normal"]))
    content.append(Paragraph(f"Age: {p.get('age','')}", styles["Normal"]))
    content.append(Paragraph(f"Sex: {p.get('sex','')}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # Claim Details
    content.append(Paragraph("2. Claim Details", styles["Heading2"]))
    c = data.get("claim_details", {})
    content.append(Paragraph(f"Hospital: {c.get('hospital','')}", styles["Normal"]))
    content.append(Paragraph(f"Diagnosis: {c.get('diagnosis','')}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # Clinical Findings
    content.append(Paragraph("3. Clinical Findings", styles["Heading2"]))
    for item in data.get("clinical_findings", []):
        content.append(Paragraph(
            f"{item.get('parameter')} - {item.get('value')} ({item.get('comment')})",
            styles["Normal"]
        ))

    content.append(Spacer(1, 10))

    # Documentation Gaps
    content.append(Paragraph("4. Documentation Gaps", styles["Heading2"]))
    for gap in data.get("documentation_gaps", []):
        content.append(Paragraph(f"- {gap}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # Timeline
    content.append(Paragraph("5. Timeline", styles["Heading2"]))
    for t in data.get("timeline", []):
        content.append(Paragraph(f"{t.get('date')} - {t.get('event')}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # Observations
    content.append(Paragraph("6. Observations", styles["Heading2"]))
    for obs in data.get("observations", []):
        content.append(Paragraph(f"Q: {obs.get('question')}", styles["Normal"]))
        content.append(Paragraph(f"Analysis: {obs.get('analysis')}", styles["Normal"]))
        content.append(Paragraph(f"Answer: {obs.get('answer')}", styles["Normal"]))
        content.append(Spacer(1, 5))

    content.append(Spacer(1, 10))

    # Conclusion
    content.append(Paragraph("7. Auditor’s Conclusion", styles["Heading2"]))
    content.append(Paragraph(data.get("auditor_conclusion",""), styles["Normal"]))

    content.append(Spacer(1, 10))

    # Remarks
    content.append(Paragraph("8. Remarks", styles["Heading2"]))
    content.append(Paragraph(data.get("remarks",""), styles["Normal"]))

    doc.build(content)