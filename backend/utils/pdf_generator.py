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

    ref = data.get("report_ref") or data.get("audit_ref") or "-"
    rdate = data.get("report_date") or data.get("audit_date") or "-"
    content.append(Paragraph(f"<b>Ref:</b> {ref} &nbsp;&nbsp; <b>Date:</b> {rdate}", styles["Normal"]))
    content.append(Spacer(1, 10))

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
    # INSURANCE DETAILS
    # =========================
    ins = data.get("insurance_details") or {}
    content.append(Paragraph("2. Insurance Details", styles["Heading2"]))
    content.append(Paragraph(f"Insurance company: {ins.get('insurance_company', '')}", styles["Normal"]))
    content.append(Paragraph(f"Policy number: {ins.get('policy_number', '')}", styles["Normal"]))
    content.append(Paragraph(f"Policy period: {ins.get('policy_period', '')}", styles["Normal"]))
    content.append(Paragraph(f"Claim / incident number: {ins.get('claim_incident_number', '')}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # CLAIM DETAILS
    # =========================
    content.append(Paragraph("3. Claim Details", styles["Heading2"]))
    c = data.get("claim_details", {})

    content.append(Paragraph(f"Hospital: {c.get('hospital','')}", styles["Normal"]))
    content.append(Paragraph(f"Consultation date: {c.get('consultation_date','')}", styles["Normal"]))
    content.append(Paragraph(f"Date of admission: {c.get('date_of_admission','')}", styles["Normal"]))
    content.append(Paragraph(f"Date of discharge: {c.get('date_of_discharge','')}", styles["Normal"]))
    content.append(Paragraph(f"Nature of admission: {c.get('nature_of_admission','')}", styles["Normal"]))
    content.append(Paragraph(f"Procedure / surgery done: {c.get('procedure_or_surgery','')}", styles["Normal"]))
    content.append(Paragraph(f"Diagnosis: {c.get('diagnosis','')}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # COMPLIANCE & CHALLENGES
    # =========================
    verdict = (data.get("compliance_verdict") or "").strip()
    if verdict:
        content.append(Paragraph("Compliance Verdict", styles["Heading2"]))
        content.append(Paragraph(verdict, styles["Normal"]))
        content.append(Spacer(1, 8))

    deviations = data.get("guideline_deviations") or []
    if deviations:
        content.append(Paragraph("Guideline Deviations", styles["Heading2"]))
        for dev in deviations:
            if isinstance(dev, dict):
                content.append(Paragraph(
                    f"{dev.get('issue','')} [{dev.get('severity','')}] — "
                    f"Guideline: {dev.get('guideline_expectation','')} — "
                    f"Evidence: {dev.get('case_evidence','')}",
                    styles["Normal"],
                ))
            else:
                content.append(Paragraph(f"- {dev}", styles["Normal"]))
            content.append(Spacer(1, 3))
        content.append(Spacer(1, 8))

    challenges = data.get("challenge_points") or []
    if challenges:
        content.append(Paragraph("Hospital Must Justify", styles["Heading2"]))
        for pt in challenges:
            content.append(Paragraph(f"- {pt}", styles["Normal"]))
        content.append(Spacer(1, 10))

    # =========================
    # IMAGING FINDINGS
    # =========================
    if data.get("imaging_findings"):
        content.append(Paragraph("4. Imaging Findings", styles["Heading2"]))

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
    content.append(Paragraph("5. Clinical Findings", styles["Heading2"]))

    for item in data.get("clinical_findings", []):
        content.append(Paragraph(
            f"{item.get('parameter')} - {item.get('value')} ({item.get('comment')})",
            styles["Normal"]
        ))

    content.append(Spacer(1, 10))

    # =========================
    # DOCUMENTATION CHECKLIST
    # =========================
    content.append(Paragraph("6. Documentation Checklist", styles["Heading2"]))
    for item in data.get("clinical_checklist", []):
        area = item.get("area", "")
        available = item.get("available", "")
        remarks = item.get("remarks", "")
        content.append(Paragraph(f"{area}: {available}", styles["Normal"]))
        if remarks:
            content.append(Paragraph(f"Remarks: {remarks}", styles["Normal"]))
        content.append(Spacer(1, 3))
    content.append(Spacer(1, 8))

    # =========================
    # DOCUMENTATION GAPS
    # =========================
    content.append(Paragraph("7. Documentation Gaps", styles["Heading2"]))

    for gap in data.get("documentation_gaps", []):
        content.append(Paragraph(f"- {gap}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # =========================
    # TREATMENT & BILLING AUDIT
    # =========================
    tba = data.get("treatment_billing_audit") or {}
    content.append(Paragraph("8. Treatment & Billing Audit", styles["Heading2"]))
    content.append(Paragraph(f"Room category admitted: {tba.get('room_category_admitted','')}", styles["Normal"]))
    content.append(Paragraph(f"Room category eligible (policy): {tba.get('room_category_eligible','')}", styles["Normal"]))
    content.append(Paragraph(f"Procedures performed: {tba.get('procedures_performed','')}", styles["Normal"]))
    content.append(Paragraph(f"Cross-checked with pre-auth: {tba.get('cross_checked_with_preauth','')}", styles["Normal"]))
    content.append(Paragraph(f"Excluded items billed: {tba.get('excluded_items_billed','')}", styles["Normal"]))
    content.append(Paragraph(f"Charges appropriate: {tba.get('charges_appropriate','')}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # FINANCIAL REVIEW
    # =========================
    fin = data.get("financial_review") or {}
    content.append(Paragraph("9. Financial Review", styles["Heading2"]))
    content.append(Paragraph(f"Total hospital bill: {fin.get('total_hospital_bill','')}", styles["Normal"]))
    content.append(Paragraph(f"Non-payable amount: {fin.get('non_payable_amount','')}", styles["Normal"]))
    content.append(Paragraph(f"Net claimable amount: {fin.get('net_claimable_amount','')}", styles["Normal"]))
    content.append(Paragraph(f"Recommended approval amount: {fin.get('recommended_approval_amount','')}", styles["Normal"]))
    content.append(Paragraph(f"Patient liability: {fin.get('patient_liability','')}", styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # TIMELINE
    # =========================
    content.append(Paragraph("10. Timeline", styles["Heading2"]))

    for t in data.get("timeline", []):
        content.append(Paragraph(f"{t.get('date')} - {t.get('event')}", styles["Normal"]))

    content.append(Spacer(1, 10))

    # =========================
    # OBSERVATIONS
    # =========================
    content.append(Paragraph("11. Auditor's Observations (Detailed)", styles["Heading2"]))
    if data.get("auditor_observation_summary"):
        content.append(Paragraph(f"Overall narrative: {data.get('auditor_observation_summary')}", styles["Normal"]))
        content.append(Spacer(1, 5))

    for idx, obs in enumerate(data.get("observations", []), start=1):
        content.append(Paragraph(f"Q{idx}: {obs.get('question')}", styles["Normal"]))
        content.append(Paragraph(f"Analysis: {obs.get('analysis')}", styles["Normal"]))
        content.append(Paragraph(f"Answer: {obs.get('answer')}", styles["Normal"]))
        content.append(Spacer(1, 5))

    content.append(Spacer(1, 10))

    # =========================
    # CONCLUSION
    # =========================
    content.append(Paragraph("12. Inference", styles["Heading2"]))
    conclusion = (data.get("inference") or data.get("auditor_conclusion") or "").strip()
    content.append(Paragraph(conclusion, styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # REMARKS
    # =========================
    content.append(Paragraph("13. Remarks", styles["Heading2"]))
    content.append(Paragraph(data.get("remarks", ""), styles["Normal"]))
    content.append(Spacer(1, 10))

    # =========================
    # Q&A SECTION (🔥 NEW)
    # =========================
    if "qa_section" in data and data["qa_section"]:
        content.append(Paragraph("14. Questions & Answers", styles["Heading2"]))

        for qa in data["qa_section"]:
            content.append(Paragraph(f"Q: {qa.get('question')}", styles["Normal"]))
            content.append(Paragraph(f"A: {qa.get('answer')}", styles["Normal"]))
            content.append(Paragraph(f"Justification: {qa.get('justification')}", styles["Normal"]))
            content.append(Spacer(1, 5))

    # =========================
    # BUILD PDF
    # =========================
    doc.build(content)