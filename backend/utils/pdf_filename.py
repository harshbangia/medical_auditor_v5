import re
from datetime import datetime
from typing import Optional, Union


def _safe_patient_slug(report_data: dict) -> str:
    raw = (report_data.get("patient_details") or {}).get("name") or ""
    raw = str(raw).strip()
    if not raw or raw == "-":
        return "Patient"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
    safe = re.sub(r"\s+", "_", safe).strip("_")
    safe = safe[:80] if safe else "Patient"
    return safe or "Patient"


def _format_timestamp(completed_at: Optional[Union[datetime, str]] = None) -> str:
    if isinstance(completed_at, datetime):
        return completed_at.strftime("%Y%m%d_%H%M%S")
    if isinstance(completed_at, str) and completed_at.strip():
        raw = completed_at.strip()
        if re.fullmatch(r"\d{8}_\d{6}", raw):
            return raw
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%d-%m-%Y %H:%M",
        ):
            try:
                return datetime.strptime(completed_at.strip(), fmt).strftime("%Y%m%d_%H%M%S")
            except ValueError:
                continue
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def pdf_download_filename(
    report_data: dict,
    completed_at: Optional[Union[datetime, str]] = None,
) -> str:
    """Build filename: PatientName_Audit_Report_YYYYMMDD_HHMMSS.pdf"""
    slug = _safe_patient_slug(report_data)
    ts = _format_timestamp(completed_at)
    return f"{slug}_Audit_Report_{ts}.pdf"

