from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from backend.utils.pdf_table_helpers import data_table, kv_table, section


def _rows_from_dict(d: dict, mapping: list) -> list:
    rows = []
    for key, label in mapping:
        val = str((d or {}).get(key) or "").strip()
        rows.append((label, val or "—"))
    return rows


def generate_pdf(data, filename="audit_report.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    styles = getSampleStyleSheet()
    content = []

    # Title
    content.append(Paragraph("MEDICAL AUDIT REPORT", styles["Title"]))
    content.append(Spacer(1, 10))

    ref = data.get("report_ref") or data.get("audit_ref") or "-"
    rdate = data.get("report_date") or data.get("audit_date") or "-"
    content.append(Paragraph(f"<b>Ref:</b> {ref} &nbsp;&nbsp; <b>Date:</b> {rdate}", styles["Normal"]))
    content.append(Spacer(1, 12))

    # Guideline
    content.extend(section("Guideline Referenced", styles))
    content.append(kv_table([("Guideline", data.get("guideline_used", "-"))], header=False))
    content.append(Spacer(1, 12))

    # 1. Patient Details
    content.extend(section("1. Patient Details", styles))
    p = data.get("patient_details") or {}
    content.append(kv_table(_rows_from_dict(p, [
        ("name", "Name"),
        ("age", "Age"),
        ("sex", "Sex"),
    ])))
    content.append(Spacer(1, 12))

    # 2. Insurance Details
    content.extend(section("2. Insurance Details", styles))
    ins = data.get("insurance_details") or {}
    content.append(kv_table(_rows_from_dict(ins, [
        ("insurance_company", "Insurance company"),
        ("policy_number", "Policy number"),
        ("policy_period", "Policy period"),
        ("claim_incident_number", "Claim / incident number"),
    ])))
    content.append(Spacer(1, 12))

    # 3. Claim Details
    content.extend(section("3. Claim Details", styles))
    c = data.get("claim_details") or {}
    claim_rows = [
        ("Hospital", c.get("hospital") or "—", ""),
        (
            "Consultation date",
            c.get("consultation_date") or "—",
            c.get("consultation_date_source") or "",
        ),
        (
            "Date of admission",
            c.get("date_of_admission") or "—",
            c.get("date_of_admission_source") or "",
        ),
        (
            "Date of discharge",
            c.get("date_of_discharge") or "—",
            c.get("date_of_discharge_source") or "",
        ),
        ("Nature of admission", c.get("nature_of_admission") or "—", ""),
        ("Procedure / surgery done", c.get("procedure_or_surgery") or "—", ""),
        ("Diagnosis", c.get("diagnosis") or "—", ""),
    ]
    content.append(data_table(
        ["Field", "Value", "Source document"],
        claim_rows,
        col_widths=[120, 150, 225],
    ))
    content.append(Spacer(1, 12))

    date_discrepancies = data.get("date_discrepancies") or []
    if date_discrepancies:
        content.extend(section("Date Discrepancies Across Documents", styles))
        disc_rows = []
        for item in date_discrepancies:
            if not isinstance(item, dict):
                continue
            detail_parts = []
            for entry in item.get("entries") or []:
                if isinstance(entry, dict):
                    detail_parts.append(
                        f"{entry.get('value', '')} ({entry.get('source_label', '')})"
                    )
            disc_rows.append([
                item.get("label", item.get("field", "")),
                item.get("message", ""),
                "; ".join(detail_parts),
            ])
        content.append(data_table(
            ["Date field", "Discrepancy", "Values found"],
            disc_rows or [["—", "—", "—"]],
            col_widths=[110, 200, 185],
        ))
        content.append(Spacer(1, 12))

    # Compliance verdict
    verdict = (data.get("compliance_verdict") or "").strip()
    if verdict:
        content.extend(section("Compliance Verdict", styles))
        content.append(kv_table([("Verdict", verdict)], header=False))
        content.append(Spacer(1, 10))

    # Guideline deviations
    deviations = data.get("guideline_deviations") or []
    if deviations:
        content.extend(section("Guideline Deviations", styles))
        dev_rows = []
        for dev in deviations:
            if isinstance(dev, dict):
                dev_rows.append([
                    dev.get("issue", ""),
                    dev.get("severity", ""),
                    dev.get("guideline_expectation", ""),
                    dev.get("case_evidence", ""),
                ])
            else:
                dev_rows.append([str(dev), "", "", ""])
        content.append(data_table(
            ["Issue", "Severity", "Guideline expectation", "Case evidence"],
            dev_rows,
            col_widths=[110, 55, 155, 180],
        ))
        content.append(Spacer(1, 12))

    # Hospital must justify
    challenges = data.get("challenge_points") or []
    if challenges:
        content.extend(section("Hospital Must Justify", styles))
        ch_rows = [[str(i + 1), pt] for i, pt in enumerate(challenges)]
        content.append(data_table(["#", "Challenge point"], ch_rows, col_widths=[30, 470]))
        content.append(Spacer(1, 12))

    # 4. Imaging Findings
    imaging = data.get("imaging_findings") or []
    if imaging:
        content.extend(section("4. Imaging Findings", styles))
        img_rows = []
        for img in imaging:
            if isinstance(img, dict):
                img_rows.append([
                    img.get("type", ""),
                    img.get("finding", ""),
                    img.get("clinical_correlation", ""),
                    img.get("consistency_with_diagnosis", ""),
                ])
        content.append(data_table(
            ["Type", "Finding", "Clinical correlation", "Consistency"],
            img_rows,
            col_widths=[90, 130, 140, 140],
        ))
        content.append(Spacer(1, 12))

    # 5. Clinical Findings (tabular)
    content.extend(section("5. Clinical Findings", styles))
    cf_rows = []
    for item in data.get("clinical_findings") or []:
        if isinstance(item, dict):
            cf_rows.append([
                item.get("parameter", ""),
                item.get("value", ""),
                item.get("comment", ""),
                item.get("source", item.get("normal_range", "")),
            ])
    if not cf_rows:
        cf_rows = [["—", "Not documented", "", ""]]
    content.append(data_table(
        ["Parameter", "Value", "Comment", "Source"],
        cf_rows,
        col_widths=[130, 100, 140, 130],
    ))
    content.append(Spacer(1, 12))

    # 6. Documentation Checklist
    content.extend(section("6. Documentation Checklist", styles))
    cl_rows = []
    for item in data.get("clinical_checklist") or []:
        if isinstance(item, dict):
            cl_rows.append([
                item.get("area", ""),
                item.get("available", ""),
                item.get("remarks", ""),
            ])
    if not cl_rows:
        cl_rows = [["—", "—", "—"]]
    content.append(data_table(
        ["Area", "Available", "Remarks"],
        cl_rows,
        col_widths=[160, 70, 270],
    ))
    content.append(Spacer(1, 12))

    # 7. Documentation Gaps
    content.extend(section("7. Documentation Gaps", styles))
    gaps = data.get("documentation_gaps") or []
    gap_rows = [[str(i + 1), g] for i, g in enumerate(gaps)] if gaps else [["—", "None identified"]]
    content.append(data_table(["#", "Gap"], gap_rows, col_widths=[30, 470]))
    content.append(Spacer(1, 12))

    # 8. Treatment & Billing Audit
    content.extend(section("8. Treatment & Billing Audit", styles))
    tba = data.get("treatment_billing_audit") or {}
    content.append(kv_table(_rows_from_dict(tba, [
        ("room_category_admitted", "Room category admitted"),
        ("room_category_eligible", "Room category eligible (policy)"),
        ("procedures_performed", "Procedures performed"),
        ("cross_checked_with_preauth", "Cross-checked with pre-auth"),
        ("excluded_items_billed", "Excluded items billed"),
        ("charges_appropriate", "Charges appropriate"),
    ])))
    content.append(Spacer(1, 12))

    # 9. Financial Review
    content.extend(section("9. Financial Review", styles))
    fin = data.get("financial_review") or {}
    content.append(kv_table(_rows_from_dict(fin, [
        ("total_hospital_bill", "Total hospital bill"),
        ("non_payable_amount", "Non-payable amount"),
        ("net_claimable_amount", "Net claimable amount"),
        ("recommended_approval_amount", "Recommended approval amount"),
        ("patient_liability", "Patient liability"),
    ])))
    content.append(Spacer(1, 12))

    # 10. Timeline
    content.extend(section("10. Timeline", styles))
    tl_rows = []
    for t in data.get("timeline") or []:
        if isinstance(t, dict):
            tl_rows.append([t.get("date", ""), t.get("event", "")])
    if not tl_rows:
        tl_rows = [["—", "—"]]
    content.append(data_table(["Date", "Event"], tl_rows, col_widths=[90, 410]))
    content.append(Spacer(1, 12))

    # 11. Observations
    content.extend(section("11. Auditor's Observations (Detailed)", styles))
    if data.get("auditor_observation_summary"):
        content.append(kv_table(
            [("Overall narrative", data.get("auditor_observation_summary"))],
            header=False,
        ))
        content.append(Spacer(1, 8))

    obs_rows = []
    for idx, obs in enumerate(data.get("observations") or [], start=1):
        if isinstance(obs, dict):
            obs_rows.append([
                f"Q{idx}",
                obs.get("question", ""),
                obs.get("analysis", ""),
                obs.get("answer", ""),
            ])
    if obs_rows:
        content.append(data_table(
            ["#", "Question", "Analysis", "Answer"],
            obs_rows,
            col_widths=[25, 120, 255, 100],
        ))
    content.append(Spacer(1, 12))

    # 12. Inference
    content.extend(section("12. Inference", styles))
    conclusion = (data.get("inference") or data.get("auditor_conclusion") or "").strip()
    content.append(kv_table([("Conclusion", conclusion or "—")], header=False))
    content.append(Spacer(1, 12))

    # 13. Remarks
    content.extend(section("13. Remarks", styles))
    content.append(kv_table([("Remarks", data.get("remarks", "") or "—")], header=False))
    content.append(Spacer(1, 12))

    # 14. Q&A
    if data.get("qa_section"):
        content.extend(section("14. Questions & Answers", styles))
        qa_rows = []
        for qa in data["qa_section"]:
            if isinstance(qa, dict):
                qa_rows.append([
                    qa.get("question", ""),
                    qa.get("answer", ""),
                    qa.get("justification", ""),
                ])
        content.append(data_table(
            ["Question", "Answer", "Justification"],
            qa_rows or [["—", "—", "—"]],
            col_widths=[160, 160, 180],
        ))
        content.append(Spacer(1, 12))

    doc.build(content)
