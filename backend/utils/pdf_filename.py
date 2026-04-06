import re


def pdf_download_filename(report_data: dict) -> str:
    """Build a safe download filename from patient name, e.g. John_Doe_audit.pdf."""
    raw = (report_data.get("patient_details") or {}).get("name") or ""
    raw = str(raw).strip()
    if not raw or raw == "-":
        return "audit_report.pdf"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
    safe = re.sub(r"\s+", "_", safe).strip("_")
    safe = safe[:80] if safe else "audit_report"
    if not safe:
        safe = "audit_report"
    return f"{safe}_audit.pdf"
