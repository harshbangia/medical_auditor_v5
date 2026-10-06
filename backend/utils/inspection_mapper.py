"""Map a stored medical-audit case into the QCI document-verification proforma.

The inspection conclusion uses only document identification, completeness,
chronology, consistency, traceability, and correlation. Claim stance, medical
necessity, and policy admissibility stay in the medical audit report.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.utils.glowix_proforma_pdf import _default_ref, _parse_report_date

DOCUMENT_NO = "GMS/FOR/DV/01"
REVISION_NO = "00"
CONFIDENTIALITY = "Confidential / Controlled"

_INVENTORY: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("Claim Form", ("claim form", "claimform"), "core"),
    ("Admission Record", ("admission record", "admission form", "admission request", "admission", "indoor"), "core"),
    ("Initial Assessment", ("initial assessment", "history sheet"), "optional"),
    ("Clinical / Progress Notes", ("progress note", "clinical note", "case paper", "indoor case", "icp"), "core"),
    ("Nursing Records", ("nursing", "nurse chart"), "optional"),
    ("Investigation Reports", ("investigation", "lab report", "laboratory", "radiology", "x ray", "xray", "mri", "ct scan", "hrct"), "core"),
    ("Medication / Treatment Records", ("medication", "treatment chart", "drug chart", "prescription"), "optional"),
    ("Procedure / Operative Records", ("operative", "operation note", "ot note", "surgery note"), "procedure"),
    ("Anaesthesia Records", ("anaesthesia", "anesthesia"), "procedure"),
    ("ICU Records", ("icu",), "icu"),
    ("Implant / Consumable Records", ("implant", "sticker"), "procedure"),
    ("Discharge Summary", ("discharge",), "core"),
    ("Pharmacy Records / Bills", ("pharmacy",), "optional"),
    ("Itemized Hospital Bill", ("itemized", "itemised", "bill breakup", "break up"), "bill"),
    ("Final Bill", ("final bill",), "bill"),
    ("Pre-authorization / Approval", ("pre auth", "preauth", "authorization", "cashless approval"), "optional"),
    ("Insurer / TPA Correspondence", ("insurer", "tpa", "query letter"), "optional"),
    ("Other Supporting Records", ("policy", "aadhaar", "kyc", "id card", "identity"), "optional"),
)

_CORE_LABELS = {label for label, _, kind in _INVENTORY if kind == "core"}
_BILL_LABELS = {label for label, _, kind in _INVENTORY if kind == "bill"}

_POLICY_WORDS = (
    "irdai",
    "non-payable",
    "non payable",
    "not payable",
    "waiting period",
    "pre-existing",
    "pre existing",
    "medical necessity",
    "medically necessary",
    "not medically",
    "recommend deduct",
    "disallow",
    "admissible",
    "claim denial",
    "deny the claim",
    "approve the claim",
    "reject the claim",
    "amount saved",
    "recommended approval",
)

_DOC_CONFLICT_WORDS = (
    "mismatch",
    "contradict",
    "different patient",
    "altered",
    "impossible date",
    "does not belong",
    "unrelated",
    "not the same",
)

_MISSING_WORDS = ("missing", "absent", "not enclosed", "not available", "not submitted", "not found")

LIMITATIONS: Tuple[str, ...] = (
    "The inspection conclusion is limited to the documents and information submitted/made available for verification.",
    "No assumption has been made regarding records that were not submitted or otherwise made available.",
    "Documentary verification does not by itself constitute approval or rejection of an insurance claim.",
    "Absence or inconsistency of an authenticity indicator does not by itself establish fraud, forgery, fabrication or manipulation.",
    "Independent source authentication shall be considered performed only where specifically undertaken according to the approved inspection method and recorded in this report.",
    "Medical necessity, treatment appropriateness, policy admissibility and legal liability are outside the document-verification conclusion unless expressly included in the approved inspection scope and applicable criteria.",
    "Findings relate only to the inspection item(s)/documents identified in this report.",
)

CONFORMITY_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("C", "C – CONFORMING"),
    ("C-O", "C-O – CONFORMING WITH OBSERVATION"),
    ("NC", "NC – NON-CONFORMING"),
    ("UC", "UC – UNABLE TO CONCLUDE"),
)

CONCLUSION_OPTIONS: Tuple[Tuple[str, str], ...] = (
    ("C", "CONFORMING"),
    ("C-O", "CONFORMING WITH OBSERVATION(S)"),
    ("NC", "NON-CONFORMING"),
    ("UC", "UNABLE TO CONCLUDE DUE TO INSUFFICIENT OBJECTIVE EVIDENCE"),
)

COMPLETENESS_OPTIONS = (
    "Complete",
    "Partially Complete",
    "Incomplete",
    "Unable to Determine",
)

CHRONOLOGY_OPTIONS = (
    "Consistent",
    "Minor Discrepancy",
    "Material Discrepancy",
    "Gap Identified",
    "Unable to Conclude",
)

CONSISTENCY_OPTIONS = (
    "Consistent",
    "Minor Discrepancy",
    "Material Discrepancy",
    "Unreconciled",
    "Unable to Conclude",
)

TRACEABILITY_OPTIONS = (
    "Adequately Traceable",
    "Partially Traceable",
    "Not Traceable",
    "Unable to Conclude",
)

AUTHENTICITY_OPTIONS = (
    "Defined Indicators Verified",
    "Partially Verified",
    "Discrepancy Identified",
    "Independent Source Authentication Not Performed",
    "Unable to Conclude",
)

CORRELATION_OPTIONS = (
    "Correlated",
    "Partially Correlated",
    "Discrepancy Identified",
    "Insufficient Supporting Records",
    "Not Applicable",
)

_CHRONOLOGY_ROWS: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("Pre-admission consultation", ("consult", "pre-admission", "pre admission", "opd"), ()),
    ("Admission", ("admitted", "admission"), ("icu",)),
    ("Initial assessment", ("initial assessment", "assessment"), ()),
    ("Investigation", ("invest", "lab", "radiology", "mri", "ct", "x-ray", "xray"), ()),
    ("Treatment", ("treatment", "medication", "therapy"), ()),
    ("Procedure / Surgery", ("surg", "procedure", "operation", "operat"), ()),
    ("ICU admission / transfer", ("icu",), ()),
    ("Discharge", ("discharg",), ()),
    ("Final billing", ("bill", "invoice"), ()),
    ("Claim submission", ("claim submission", "claim submitted", "intimation"), ()),
)


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _clip(text: Any, limit: int = 360) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return "—"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _strip_money(text: str) -> str:
    text = re.sub(r"(?i)\brs\.?\s*[\d,]+(?:\.\d+)?", "", text)
    text = re.sub(r"₹\s*[\d,]+(?:\.\d+)?", "", text)
    text = re.sub(r"(?i)\b(deduct|withhold|disallow)\b[^.]{0,80}", "", text)
    return re.sub(r"\s+", " ", text).strip(" ;,.")


def _neutralize(text: Any) -> str:
    raw = _strip_money(str(text or ""))
    raw = re.sub(r"(?i)\bfraud(ulent)?\b", "discrepancy", raw)
    raw = re.sub(r"(?i)\bforgery\b", "alteration indicator", raw)
    raw = re.sub(r"(?i)\bfwa\b", "integrity", raw)
    return _clip(raw)


def _is_policy_opinion(text: str) -> bool:
    blob = _norm(text)
    return any(_norm(word) in blob for word in _POLICY_WORDS)


def _dash(val: Any) -> str:
    text = str(val or "").strip()
    if not text or text.lower() in {"none", "null", "unknown", "na", "n/a", "not specified", "-", "—"}:
        return "—"
    return text


def _parse_date(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _joined_sources(data: dict) -> str:
    parts: List[str] = []
    for src in data.get("document_sources") or []:
        if isinstance(src, dict):
            parts.append(str(src.get("filename") or src.get("name") or ""))
        else:
            parts.append(str(src))
    for row in data.get("document_analysis") or []:
        if isinstance(row, dict):
            parts.append(
                " ".join(
                    str(row.get(key) or "")
                    for key in ("document", "document_type")
                )
            )
    return _norm(" ".join(parts))


def _explicit_checklist(data: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in data.get("clinical_checklist") or []:
        if not isinstance(item, dict):
            continue
        area = _norm(item.get("area"))
        avail = str(item.get("available") or "").strip().upper()
        if not area:
            continue
        if avail in {"YES", "Y", "AVAILABLE"}:
            out[area] = "Y"
        elif avail in {"NO", "N", "NOT AVAILABLE"}:
            out[area] = "N"
        elif avail in {"NA", "N/A"}:
            out[area] = "NA"
    return out


def _checklist_hit(explicit: Dict[str, str], needles: Sequence[str]) -> str:
    for area, status in explicit.items():
        if any(needle in area for needle in needles):
            return status
    return ""


def _gap_texts(data: dict) -> List[str]:
    texts: List[str] = []
    for gap in data.get("documentation_gaps") or []:
        if isinstance(gap, str):
            texts.append(gap)
            continue
        if not isinstance(gap, dict):
            continue
        texts.append(
            " ".join(
                str(gap.get(key) or "")
                for key in ("title", "gap", "finding", "forensic_finding", "description", "detail", "evidence")
            )
        )
    return [t.strip() for t in texts if t and t.strip()]


def _procedure_applicable(claim: dict, blob: str) -> bool:
    proc = _norm(claim.get("procedure_or_surgery"))
    managed = any(k in proc for k in ("medical management", "conservative", "no surgery"))
    noted = any(k in blob for k in ("operative", "operation note", "ot note", "anaesthesia", "anesthesia"))
    if managed and not noted:
        return False
    if any(k in proc for k in ("surgery", "operative", "operation", "implant")):
        return True
    return noted


def _icu_applicable(claim: dict, blob: str, gaps: Sequence[str]) -> bool:
    blob_all = _norm(f"{claim.get('nature_of_admission') or ''} {blob} {' '.join(gaps)}")
    return "icu" in blob_all


def _inventory_rows(data: dict, blob: str, explicit: Dict[str, str], gaps: Sequence[str]) -> List[dict]:
    claim = data.get("claim_details") or {}
    procedure_on = _procedure_applicable(claim, blob)
    icu_on = _icu_applicable(claim, blob, gaps)
    gap_blob = _norm(" ".join(gaps))
    rows: List[dict] = []
    for index, (label, needles, kind) in enumerate(_INVENTORY, start=1):
        applicable = True
        if kind == "procedure" and not procedure_on:
            applicable = False
        if kind == "icu" and not icu_on:
            applicable = False
        hit = _checklist_hit(explicit, needles)
        present = any(needle in blob for needle in needles)
        missing_named = any(needle in gap_blob for needle in needles) and any(
            word in gap_blob for word in _MISSING_WORDS
        )
        # "Report present but film not enclosed" is not an absent investigation.
        if missing_named and "present" in gap_blob:
            missing_named = False
        if not applicable and not present and hit != "Y":
            available = "NA"
            remarks = "Not applicable on the records inspected."
            row_applicable = False
        elif hit == "N" or (missing_named and not present and hit != "Y"):
            available = "N"
            remarks = "Recorded as not available in the case file."
            row_applicable = True
        elif hit == "Y" or present:
            available = "Y"
            remarks = "Present in the stored case file."
            row_applicable = True
        elif hit == "NA":
            available = "NA"
            remarks = "Marked not applicable."
            row_applicable = False
        else:
            available = "NA"
            remarks = "Not identified in the stored case file."
            row_applicable = True
        rows.append({
            "sr": index,
            "document": label,
            "date": "—",
            "reference_no": "—",
            "pages": "—",
            "available": available,
            "remarks": remarks,
            "applicable": row_applicable,
            "kind": kind,
        })
    _attach_document_dates(rows, data)
    return rows


def _attach_document_dates(rows: List[dict], data: dict) -> None:
    analyses = [r for r in (data.get("document_analysis") or []) if isinstance(r, dict)]
    for row in rows:
        if row["available"] != "Y":
            continue
        needles = next(n for label, n, _ in _INVENTORY if label == row["document"])
        for analysis in analyses:
            blob = _norm(f"{analysis.get('document')} {analysis.get('document_type')}")
            if any(needle in blob for needle in needles):
                row["reference_no"] = _dash(analysis.get("document"))
                content = str(analysis.get("key_content") or "")
                found = _parse_loose_date(content)
                if found:
                    row["date"] = found
                break


def _parse_loose_date(text: str) -> str:
    match = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text or "")
    return match.group(1) if match else ""


def _row_map(rows: Sequence[dict]) -> Dict[str, dict]:
    return {row["document"]: row for row in rows}


def _available(rows: Dict[str, dict], *labels: str) -> str:
    matched = [rows[label] for label in labels if label in rows]
    if not matched:
        return "NA"
    if matched and all(not row["applicable"] and row["available"] == "NA" for row in matched):
        return "NA"
    if any(row["available"] == "Y" for row in matched):
        return "Y"
    if any(row["available"] == "N" for row in matched):
        return "N"
    return "NA"


def _overall_completeness(rows: Sequence[dict]) -> str:
    by_label = _row_map(rows)
    y_count = sum(1 for row in rows if row["available"] == "Y")
    n_count = sum(1 for row in rows if row["available"] == "N")
    if y_count == 0 and n_count == 0:
        return "Unable to Determine"
    core_flags = [_available(by_label, label) for label in _CORE_LABELS]
    bill_flag = "Y" if any(by_label[label]["available"] == "Y" for label in _BILL_LABELS if label in by_label) else (
        "N" if any(by_label[label]["available"] == "N" for label in _BILL_LABELS if label in by_label) else "NA"
    )
    judged = core_flags + [bill_flag]
    if n_count >= 2 and n_count >= y_count:
        return "Incomplete"
    if any(flag == "N" for flag in judged) or any(flag == "NA" for flag in core_flags) or bill_flag == "NA":
        if y_count == 0:
            return "Unable to Determine"
        return "Partially Complete"
    if y_count > 0 and n_count == 0:
        return "Complete"
    return "Partially Complete"


def _missing_line(rows: Sequence[dict]) -> str:
    missing = [row["document"] for row in rows if row["available"] == "N"]
    unknown = [
        row["document"]
        for row in rows
        if row["available"] == "NA" and row["kind"] in {"core", "bill"} and row["applicable"]
    ]
    parts = []
    if missing:
        parts.append("Not available: " + ", ".join(missing) + ".")
    if unknown:
        parts.append("Not identified in the stored case file: " + ", ".join(unknown) + ".")
    return " ".join(parts) if parts else "No missing document was explicitly recorded."


def _timeline(data: dict) -> List[dict]:
    events = []
    for item in data.get("timeline") or []:
        if not isinstance(item, dict):
            continue
        events.append({
            "date": str(item.get("date") or "").strip(),
            "event": str(item.get("event") or "").strip(),
            "time": str(item.get("time") or "").strip(),
            "source": str(item.get("source") or item.get("source_file") or "").strip(),
        })
    return events


def _match_event(events: Sequence[dict], needles: Sequence[str], exclude: Sequence[str]) -> Optional[dict]:
    for event in events:
        text = _norm(event.get("event"))
        if any(item in text for item in exclude):
            continue
        if any(needle in text for needle in needles):
            return event
    return None


def _chronology(data: dict, rows: Sequence[dict]) -> Tuple[List[dict], str, str]:
    claim = data.get("claim_details") or {}
    events = _timeline(data)
    procedure_on = _procedure_applicable(claim, _joined_sources(data))
    icu_on = any(row["document"] == "ICU Records" and row["applicable"] and row["available"] == "Y" for row in rows)
    table: List[dict] = []
    for label, needles, exclude in _CHRONOLOGY_ROWS:
        event = _match_event(events, needles, exclude)
        date = event.get("date") if event else ""
        if label == "Admission" and not date:
            date = str(claim.get("date_of_admission") or "")
        if label == "Discharge" and not date:
            date = str(claim.get("date_of_discharge") or "")
        if label == "Pre-admission consultation" and not date:
            date = str(claim.get("consultation_date") or "")
        not_applicable = (
            (label == "Procedure / Surgery" and not procedure_on)
            or (label == "ICU admission / transfer" and not icu_on)
        )
        if not_applicable and not date:
            status = "NA"
            observation = "Not applicable on the records inspected."
        elif date:
            status = "Recorded"
            observation = _dash(event.get("event") if event else label)
        else:
            status = "Not recorded"
            observation = "No date was stored for this event."
        table.append({
            "event": label,
            "date": _dash(date),
            "time": _dash(event.get("time") if event else ""),
            "source": _dash(event.get("source") if event else ""),
            "status": status,
            "observation": observation,
        })
    admission = _parse_date(claim.get("date_of_admission")) or _parse_date(
        next((row["date"] for row in table if row["event"] == "Admission"), "")
    )
    discharge = _parse_date(claim.get("date_of_discharge")) or _parse_date(
        next((row["date"] for row in table if row["event"] == "Discharge"), "")
    )
    gap_blob = _norm(" ".join(_gap_texts(data)))
    date_conflict = any(word in gap_blob for word in ("date mismatch", "impossible date", "discharge before"))
    if date_conflict or (admission and discharge and discharge < admission):
        overall = "Material Discrepancy"
        note = "A stored date is out of order or a date conflict is recorded."
    elif admission and discharge:
        overall = "Consistent"
        note = "Admission is on or before discharge in the stored case file."
    elif admission or discharge:
        overall = "Gap Identified"
        note = "Only one of admission or discharge is dated in the stored case file."
    else:
        overall = "Unable to Conclude"
        note = "Admission and discharge dates were not stored."
    return table, overall, note


def _classify_discrepancy(text: str) -> str:
    blob = _norm(text)
    if any(word in blob for word in _DOC_CONFLICT_WORDS):
        return "MAT"
    if any(word in blob for word in _MISSING_WORDS):
        return "MD"
    return "OBS"


def _discrepancies(data: dict) -> List[dict]:
    rows: List[dict] = []

    def add(reference: str, requirement: str, evidence: str) -> None:
        if _is_policy_opinion(f"{requirement} {evidence}"):
            return
        cleaned = _neutralize(evidence)
        if cleaned == "—":
            return
        rows.append({
            "sr": len(rows) + 1,
            "reference": _clip(reference, 80),
            "requirement": _clip(requirement, 120),
            "evidence": cleaned,
            "classification": _classify_discrepancy(evidence),
        })

    for gap in data.get("documentation_gaps") or []:
        if isinstance(gap, str):
            add("Case file", "Document completeness", gap)
            continue
        if not isinstance(gap, dict):
            continue
        add(
            str(gap.get("evidence") or gap.get("title") or "Case file"),
            str(gap.get("title") or gap.get("gap") or "Document completeness"),
            " ".join(
                str(gap.get(key) or "")
                for key in ("finding", "forensic_finding", "description", "detail", "evidence")
            ) or str(gap.get("title") or ""),
        )
    fraud = data.get("fraud_abuse") or {}
    findings = fraud.get("findings") if isinstance(fraud, dict) else None
    if findings is None:
        findings = data.get("fraud_abuse_findings") or []
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        blob = " ".join(str(finding.get(key) or "") for key in ("category", "indicator", "evidence"))
        if _is_policy_opinion(blob):
            continue
        if not any(word in _norm(blob) for word in (
            "document", "mismatch", "missing", "absent", "barcode", "qr", "identity", "signature", "unrelated",
        )):
            continue
        add(
            "Traceability / identity",
            str(finding.get("indicator") or finding.get("category") or "Document indicator"),
            str(finding.get("evidence") or finding.get("indicator") or ""),
        )
    for index, row in enumerate(rows, start=1):
        row["sr"] = index
    return rows[:12]


def _identification(data: dict, rows: Sequence[dict], discrepancies: Sequence[dict]) -> Tuple[List[dict], str]:
    patient = data.get("patient_details") or {}
    claim = data.get("claim_details") or {}
    insurance = data.get("insurance_details") or {}
    codes = data.get("document_codes") if isinstance(data.get("document_codes"), dict) else {}
    disc_blob = _norm(" ".join(item["evidence"] for item in discrepancies))
    name = _dash(patient.get("name"))
    hospital = _dash(claim.get("hospital"))
    uhid = _dash(
        patient.get("hospital_reg_no")
        or patient.get("uhid")
        or patient.get("mrn")
        or (codes or {}).get("uhid")
    )
    ipd = _dash(patient.get("ip_number") or patient.get("ipd") or claim.get("ipd_number") or claim.get("admission_no"))
    claim_no = _dash(insurance.get("claim_incident_number"))
    policy = _dash(insurance.get("policy_number"))
    has_docs = any(row["available"] == "Y" for row in rows)

    def status(present: bool, conflict_words: Sequence[str]) -> str:
        if any(word in disc_blob for word in conflict_words):
            return "NC"
        if present:
            return "C"
        return "UC" if has_docs or name != "—" else "UC"

    checks = [
        ("Patient identification established", status(name != "—", ("different patient", "name mismatch", "identity")), f"Recorded name: {name}." if name != "—" else "Patient name was not stored."),
        ("Hospital/source identified", status(hospital != "—", ("hospital mismatch",)), f"Recorded hospital: {hospital}." if hospital != "—" else "Hospital was not stored."),
        ("UHID/IPD linkage established", status(uhid != "—" or ipd != "—", ("uhid mismatch", "ipd mismatch")), "UHID/IPD was stored." if uhid != "—" or ipd != "—" else "UHID/IPD was not stored."),
        ("Claim/policy linkage established", status(claim_no != "—" or policy != "—", ("claim mismatch", "policy mismatch")), "Claim or policy number was stored." if claim_no != "—" or policy != "—" else "Claim and policy numbers were not stored."),
        ("Document dates identifiable", status(bool(_parse_date(claim.get("date_of_admission")) or _parse_date(claim.get("date_of_discharge"))), ("impossible date", "date mismatch")), "Admission or discharge date was stored." if _parse_date(claim.get("date_of_admission")) or _parse_date(claim.get("date_of_discharge")) else "Document dates were not stored."),
        ("Relevant reference numbers identifiable", status(claim_no != "—" or policy != "—" or uhid != "—", ()), "A claim, policy, or UHID reference was stored." if claim_no != "—" or policy != "—" or uhid != "—" else "No reference number was stored."),
    ]
    table = [
        {"sr": index, "parameter": label, "status": flag, "observation": observation}
        for index, (label, flag, observation) in enumerate(checks, start=1)
    ]
    failed = [row["parameter"] for row in table if row["status"] == "NC"]
    unknown = [row["parameter"] for row in table if row["status"] == "UC"]
    if failed:
        finding = "Identification conflict recorded for: " + ", ".join(failed) + "."
    elif unknown:
        finding = "Identification is partial. Not stored: " + ", ".join(unknown) + "."
    else:
        finding = "Patient, hospital, and claim or policy linkage are recorded in the case file."
    return table, finding


def _consistency(data: dict, discrepancies: Sequence[dict], chronology_overall: str) -> Tuple[List[dict], str]:
    patient = data.get("patient_details") or {}
    claim = data.get("claim_details") or {}
    disc_blob = _norm(" ".join(f"{item['requirement']} {item['evidence']}" for item in discrepancies))
    specs = [
        ("Patient identity", "Patient details", ("name mismatch", "different patient", "identity"), bool(_dash(patient.get("name")) != "—")),
        ("Admission/discharge dates", "Claim dates / timeline", ("date mismatch", "impossible date"), chronology_overall == "Consistent"),
        ("Diagnosis", "Claim diagnosis", ("diagnosis mismatch", "conflicting diagnosis"), bool(_dash(claim.get("diagnosis") or claim.get("final_diagnosis")) != "—")),
        ("Clinical findings", "Clinical findings", ("finding mismatch",), bool(data.get("clinical_findings"))),
        ("Investigations", "Investigation records", ("investigation mismatch", "report absent", "report missing"), any(
            "investigation" in _norm(item["requirement"]) for item in discrepancies
        ) or bool(data.get("imaging_findings") or data.get("clinical_findings"))),
        ("Treatment", "Treatment records", ("treatment mismatch",), bool(_dash(claim.get("procedure_or_surgery")) != "—")),
        ("Medication", "Medication records", ("medication mismatch",), False),
        ("Procedure / Surgery", "Operative records", ("procedure mismatch",), _procedure_applicable(claim, _joined_sources(data))),
        ("ICU stay", "ICU records", ("icu mismatch",), False),
        ("Implant / Consumables", "Implant records", ("implant mismatch",), False),
        ("Final bill", "Billing records", ("bill mismatch", "amount mismatch"), False),
        ("Insurance records", "Policy / claim records", ("policy mismatch", "claim mismatch"), bool(_dash((data.get("insurance_details") or {}).get("policy_number")) != "—")),
    ]
    table = []
    ranks = []
    for parameter, compared, words, populated in specs:
        if any(word in disc_blob for word in words):
            rank = "MAT" if any(word in disc_blob for word in _DOC_CONFLICT_WORDS) else "MD"
            status = "Material Discrepancy" if rank == "MAT" else "Minor Discrepancy"
            observation = "A documentary discrepancy mentions this parameter."
        elif parameter == "Admission/discharge dates" and chronology_overall == "Material Discrepancy":
            status = "Material Discrepancy"
            observation = "Stored admission and discharge dates conflict."
            rank = "MAT"
        elif parameter == "Admission/discharge dates" and chronology_overall == "Gap Identified":
            status = "Minor Discrepancy"
            observation = "One hospitalization date is missing."
            rank = "MD"
        elif populated:
            status = "Consistent"
            observation = f"Compared with {compared.lower()} stored on the case."
            rank = "OK"
        else:
            status = "Unable to Conclude"
            observation = "This comparison was not stored as a separate record."
            rank = "UC"
        ranks.append(rank)
        table.append({
            "parameter": parameter,
            "records_compared": compared,
            "status": status,
            "observation": observation,
        })
    if "MAT" in ranks or chronology_overall == "Material Discrepancy":
        overall = "Material Discrepancy"
    elif "MD" in ranks:
        overall = "Minor Discrepancy"
    elif any(row["status"] == "Consistent" for row in table) and not any(
        row["status"] in {"Material Discrepancy", "Minor Discrepancy"} for row in table
    ):
        overall = "Consistent" if all(
            row["status"] in {"Consistent", "Unable to Conclude"} for row in table
        ) and any(row["status"] == "Consistent" for row in table) else "Unable to Conclude"
        if any(row["status"] == "Unable to Conclude" for row in table) and any(row["status"] == "Consistent" for row in table):
            overall = "Consistent"
    else:
        overall = "Unable to Conclude"
    return table, overall


def _cross_document(rows: Sequence[dict], discrepancies: Sequence[dict]) -> List[dict]:
    by_label = _row_map(rows)
    disc_blob = _norm(" ".join(item["evidence"] for item in discrepancies))
    pairs = (
        ("Admission Record", "Discharge Summary", "Admission details", ("admission", "discharge")),
        ("Clinical / Progress Notes", "Investigation Reports", "Investigation", ("investigation", "report")),
        ("Clinical / Progress Notes", "Medication / Treatment Records", "Treatment", ("medication", "treatment")),
        ("Procedure / Operative Records", "Anaesthesia Records", "Procedure", ("operative", "anaesthesia", "anesthesia")),
        ("Procedure / Operative Records", "Implant / Consumable Records", "Implant", ("implant",)),
        ("ICU Records", "Final Bill", "ICU period", ("icu",)),
        ("Clinical / Progress Notes", "Itemized Hospital Bill", "Service linkage", ("itemized", "bill")),
        ("Discharge Summary", "Final Bill", "Hospitalization/procedure", ("discharge", "bill")),
        ("Pre-authorization / Approval", "Claim Form", "Claim particulars", ("pre auth", "claim")),
    )
    table = []
    for left, right, parameter, words in pairs:
        left_row = by_label.get(left)
        right_row = by_label.get(right)
        left_na = not left_row or (left_row["available"] == "NA" and not left_row["applicable"])
        right_na = not right_row or (right_row["available"] == "NA" and not right_row["applicable"])
        if left_na or right_na:
            result = "NA"
            observation = "Not applicable on the records inspected."
        elif left_row["available"] == "N" or right_row["available"] == "N":
            result = "ND"
            observation = "A supporting document in this pair is not available."
        elif left_row["available"] != "Y" or right_row["available"] != "Y":
            result = "ND"
            observation = "A supporting document in this pair was not identified."
        elif any(word in disc_blob for word in words) and any(
            token in disc_blob for token in ("mismatch", "absent", "missing", "not enclosed")
        ):
            result = "D"
            observation = "A recorded discrepancy concerns this cross-check."
        else:
            result = "CV"
            observation = "Both records are present in the case file."
        table.append({
            "primary": left if left != "Clinical / Progress Notes" else "Clinical record",
            "corroborating": right,
            "parameter": parameter,
            "result": result,
            "observation": observation,
        })
    # Restore proforma primary labels for the two clinical rows.
    table[1]["primary"] = "Doctor's Order"
    table[2]["primary"] = "Doctor's Order"
    table[3]["primary"] = "Operative Note"
    table[4]["primary"] = "Operative Note"
    table[5]["primary"] = "ICU Record"
    table[6]["primary"] = "Clinical Record"
    return table


def _traceability(data: dict, rows: Sequence[dict]) -> Tuple[List[dict], str, str]:
    patient = data.get("patient_details") or {}
    claim = data.get("claim_details") or {}
    insurance = data.get("insurance_details") or {}
    codes = data.get("document_codes") if isinstance(data.get("document_codes"), dict) else {}
    unique = [item for item in (codes or {}).get("unique_codes") or [] if isinstance(item, dict)]
    barcode_present = any(str(item.get("format") or "").lower().find("qr") < 0 and item.get("payload") for item in unique) or any(
        "barcode" in _norm(item.get("format")) for item in unique
    )
    qr_present = any("qr" in _norm(item.get("format")) for item in unique)
    overall = str((codes or {}).get("overall") or "")

    def yn(present: bool, unknown_ok: bool = True) -> str:
        if present:
            return "Y"
        return "NA" if unknown_ok else "N"

    name = _dash(patient.get("name")) != "—"
    uhid = _dash(patient.get("hospital_reg_no") or patient.get("uhid")) != "—"
    ipd = _dash(patient.get("ip_number") or claim.get("ipd_number")) != "—"
    claim_no = _dash(insurance.get("claim_incident_number")) != "—"
    hospital = _dash(claim.get("hospital")) != "—"
    dated = bool(_parse_date(claim.get("date_of_admission")) or _parse_date(claim.get("date_of_discharge")))
    indicators = [
        ("Patient name", yn(name, unknown_ok=False), "Y" if name else "NA", "Stored on the case." if name else "Not stored."),
        ("UHID/MRN", yn(uhid), "Y" if uhid else "NA", "Stored on the case." if uhid else "Not stored."),
        ("IPD/Admission No.", yn(ipd), "Y" if ipd else "NA", "Stored on the case." if ipd else "Not stored."),
        ("Claim No.", yn(claim_no, unknown_ok=False), "Y" if claim_no else "NA", "Stored on the case." if claim_no else "Not stored."),
        ("Hospital identification", yn(hospital, unknown_ok=False), "Y" if hospital else "NA", "Stored on the case." if hospital else "Not stored."),
        ("Document/reference No.", yn(bool(data.get("document_sources"))), "Y" if data.get("document_sources") else "NA", "Uploaded file names are the reference."),
        ("Date/time", yn(dated), "Y" if dated else "NA", "Admission or discharge date stored." if dated else "Not stored."),
        ("Authorized signatory identification", "NA", "NA", "Signatory verification was not recorded."),
        ("Stamp/seal where applicable", "NA", "NA", "Stamp or seal verification was not recorded."),
        ("Laboratory accession No.", "NA", "NA", "Accession number was not stored as a separate field."),
        ("Invoice/bill No.", "Y" if any(row["document"] in _BILL_LABELS and row["available"] == "Y" for row in rows) else "NA", "NA", "A bill is present in the file." if any(row["document"] in _BILL_LABELS and row["available"] == "Y" for row in rows) else "Bill number was not stored."),
        ("Implant batch/lot/serial No.", "NA", "NA", "Batch or serial verification was not recorded."),
        ("Barcode", "Y" if barcode_present else "NA", "Y" if barcode_present else "NA", "Decoded from uploaded pages." if barcode_present else "No barcode was decoded."),
        ("QR Code", "Y" if qr_present else "NA", "Y" if qr_present else "NA", "Decoded from uploaded pages." if qr_present else "No QR code was decoded."),
        ("Digital signature/reference", "NA", "NA", "Digital-signature verification was not recorded."),
        ("Page continuity", "NA", "NA", "Page continuity was not scored as a separate check."),
    ]
    table = [
        {"indicator": label, "present": present, "verified": verified, "observation": observation}
        for label, present, verified, observation in indicators
    ]
    present_core = sum(1 for label in ("Patient name", "Claim No.", "Hospital identification") if any(
        row["indicator"] == label and row["present"] == "Y" for row in table
    ))
    if present_core == 3:
        trace = "Adequately Traceable"
    elif present_core >= 1:
        trace = "Partially Traceable"
    elif any(row["available"] == "Y" for row in rows):
        trace = "Not Traceable"
    else:
        trace = "Unable to Conclude"
    if overall == "mismatch":
        authenticity = "Discrepancy Identified"
    elif not (codes or {}).get("scanned"):
        authenticity = "Independent Source Authentication Not Performed"
    else:
        # Local decode does not fetch issuer portals.
        authenticity = "Independent Source Authentication Not Performed"
    return table, trace, authenticity


def _correlation(rows: Sequence[dict], discrepancies: Sequence[dict]) -> Tuple[List[dict], str]:
    by_label = _row_map(rows)
    disc_blob = _norm(" ".join(item["evidence"] for item in discrepancies))

    def corr(labels: Sequence[str], topic: str, required: bool) -> Tuple[str, str, str]:
        flag = _available(by_label, *labels)
        topic_hit = topic and topic in disc_blob and any(
            word in disc_blob for word in ("missing", "absent", "mismatch", "not enclosed")
        )
        applicable = any(by_label[label]["applicable"] for label in labels if label in by_label)
        if not applicable and flag != "Y":
            return "Not applicable", "Not Applicable", "Not applicable on the records inspected."
        unknown = [
            label for label in labels
            if label in by_label and by_label[label]["applicable"] and by_label[label]["available"] == "NA"
        ]
        if flag == "Y" and topic_hit:
            return "Case file", "Discrepancy Identified", "A recorded discrepancy concerns this item."
        if flag == "Y" and unknown:
            return "Case file", "Partially Correlated", "One supporting record is present and another was not identified."
        if flag == "Y":
            return "Case file", "Correlated", "A supporting record is present."
        if flag == "N":
            return "Not in file", "Insufficient Supporting Records", "The supporting record is not available."
        if required:
            return "Not identified", "Insufficient Supporting Records", "The supporting record was not identified."
        return "Not identified", "Not Applicable", "No separate supporting record was stored."

    specs = (
        ("Hospitalization", ("Admission Record", "Discharge Summary"), "admission", True),
        ("Investigation", ("Investigation Reports",), "investigation", True),
        ("Medicines", ("Medication / Treatment Records", "Pharmacy Records / Bills"), "medication", False),
        ("ICU", ("ICU Records",), "icu", False),
        ("Procedure", ("Procedure / Operative Records",), "procedure", False),
        ("Surgery", ("Procedure / Operative Records",), "surgery", False),
        ("Anaesthesia", ("Anaesthesia Records",), "anaesthesia", False),
        ("Implant", ("Implant / Consumable Records",), "implant", False),
        ("Consumables", ("Pharmacy Records / Bills", "Implant / Consumable Records"), "consumable", False),
        ("Consultation", ("Initial Assessment", "Pre-authorization / Approval"), "consult", False),
        ("Other charges", ("Itemized Hospital Bill", "Final Bill"), "bill", True),
    )
    table = []
    statuses = []
    for item, labels, topic, required in specs:
        support, status, observation = corr(labels, topic, required)
        statuses.append(status)
        table.append({
            "item": item,
            "support": support,
            "correlation": status,
            "observation": observation,
        })
    if "Discrepancy Identified" in statuses:
        overall = "Discrepancy Identified"
    elif "Partially Correlated" in statuses or (
        "Insufficient Supporting Records" in statuses and "Correlated" in statuses
    ):
        overall = "Partially Correlated"
    elif "Insufficient Supporting Records" in statuses:
        overall = "Insufficient Supporting Records"
    elif "Correlated" in statuses:
        overall = "Correlated"
    else:
        overall = "Not Applicable"
    return table, overall


def _conformity(
    completeness: str,
    chronology: str,
    consistency: str,
    discrepancies: Sequence[dict],
    rows: Sequence[dict],
) -> str:
    y_count = sum(1 for row in rows if row["available"] == "Y")
    n_count = sum(1 for row in rows if row["available"] == "N")
    mat = [item for item in discrepancies if item["classification"] == "MAT"]
    if y_count == 0 and n_count == 0:
        return "UC"
    if completeness == "Unable to Determine":
        return "UC"
    if chronology == "Material Discrepancy" or consistency == "Material Discrepancy" or len(mat) >= 2:
        return "NC"
    if completeness == "Incomplete" or n_count >= 3:
        return "NC"
    if mat or discrepancies or completeness == "Partially Complete" or chronology in {
        "Minor Discrepancy", "Gap Identified",
    } or consistency == "Minor Discrepancy":
        return "C-O"
    if completeness == "Complete":
        return "C"
    return "UC"


def _inspection_report_no(data: dict, report_dt: datetime) -> str:
    base = _default_ref(data, report_dt)
    if base.startswith("GMS/"):
        return "GMS/DV/" + base[len("GMS/"):]
    return f"GMS/DV/{base}"


def _claim_type(data: dict) -> str:
    blob = _norm(str(data.get("claim_details") or "") + " " + str(data.get("insurance_details") or ""))
    if "reimbursement" in blob:
        return "Reimbursement"
    if "cashless" in blob:
        return "Cashless"
    return "—"


def map_audit_to_inspection(data: dict) -> dict:
    """Build the inspection-proforma view of a stored audit report."""
    data = data or {}
    report_dt = _parse_report_date(data)
    dated = report_dt.strftime("%d/%m/%Y")
    patient = data.get("patient_details") or {}
    claim = data.get("claim_details") or {}
    insurance = data.get("insurance_details") or {}
    blob = _joined_sources(data)
    explicit = _explicit_checklist(data)
    gap_list = _gap_texts(data)
    inventory = _inventory_rows(data, blob, explicit, gap_list)
    discrepancies = _discrepancies(data)
    identification, identification_finding = _identification(data, inventory, discrepancies)
    chronology, chronology_overall, chronology_note = _chronology(data, inventory)
    consistency, consistency_overall = _consistency(data, discrepancies, chronology_overall)
    cross = _cross_document(inventory, discrepancies)
    trace_rows, trace_result, authenticity = _traceability(data, inventory)
    correlation, correlation_status = _correlation(inventory, discrepancies)
    completeness = _overall_completeness(inventory)
    conformity = _conformity(completeness, chronology_overall, consistency_overall, discrepancies, inventory)

    source_count = len([s for s in (data.get("document_sources") or []) if s]) or len(
        [r for r in inventory if r["available"] == "Y"]
    )
    age = _dash(patient.get("age"))
    if age != "—" and "year" not in age.lower() and "dob" not in age.lower():
        age = f"{age} years"
    diagnosis = _dash(claim.get("final_diagnosis") or claim.get("diagnosis") or claim.get("provisional_diagnosis"))
    report_no = _inspection_report_no(data, report_dt)
    assignment = _dash(data.get("audit_ref") or data.get("report_ref") or report_no)
    codes = data.get("document_codes") if isinstance(data.get("document_codes"), dict) else {}

    by_label = _row_map(inventory)
    completeness_rows = []
    groups = (
        ("Required admission records", ("Admission Record", "Claim Form")),
        ("Clinical records", ("Clinical / Progress Notes",)),
        ("Investigation records", ("Investigation Reports",)),
        ("Treatment/medication records", ("Medication / Treatment Records",)),
        ("Procedure/operative records", ("Procedure / Operative Records",)),
        ("ICU records, where applicable", ("ICU Records",)),
        ("Discharge records", ("Discharge Summary",)),
        ("Billing records", ("Itemized Hospital Bill", "Final Bill", "Pharmacy Records / Bills")),
        ("Insurance records", ("Pre-authorization / Approval", "Insurer / TPA Correspondence")),
        ("Other supporting documents", ("Other Supporting Records",)),
    )
    for label, keys in groups:
        flag = _available(by_label, *keys)
        mixed = [by_label[key]["available"] for key in keys if key in by_label]
        if flag == "Y" and "N" in mixed:
            status = "PC"
            observation = "Partly present in the case file."
        elif flag == "Y":
            status = "C"
            observation = "Present in the case file."
        elif flag == "N":
            status = "IC"
            observation = "Not available in the case file."
        else:
            status = "NA"
            observation = "Not identified or not applicable."
        completeness_rows.append({
            "parameter": label,
            "status": status,
            "observation": observation,
        })

    mat = [item for item in discrepancies if item["classification"] == "MAT"]
    basis = (
        f"Determined from the stored case file against document identification, completeness "
        f"({completeness}), chronology ({chronology_overall}), internal consistency "
        f"({consistency_overall}), and {len(discrepancies)} documentary observation(s). "
        f"Medical necessity, treatment appropriateness, and policy admissibility were not used."
    )
    if conformity == "UC":
        requirements = "The stored case file does not contain enough identified documents to determine conformity."
    elif any(row["available"] == "N" for row in inventory):
        requirements = "Not available: " + ", ".join(row["document"] for row in inventory if row["available"] == "N") + "."
    else:
        requirements = "No inspection checklist item was recorded as failed."
    if discrepancies:
        evidence = " ".join(f"{item['classification']}: {item['evidence']}" for item in discrepancies[:4])
    else:
        evidence = "No documentary discrepancy was recorded in the stored case file."

    finding_lines = {
        "identification": identification_finding,
        "completeness": completeness,
        "chronology": f"{chronology_overall}. {chronology_note}",
        "consistency": consistency_overall,
        "cross": _cross_summary(cross),
        "traceability": f"{trace_result}. Authenticity indicators: {authenticity}.",
        "correlation": correlation_status,
        "material": (
            "; ".join(item["evidence"] for item in mat)
            if mat else "None recorded."
        ),
    }
    remarks = _conclusion_remarks(conformity, completeness, discrepancies, chronology_note)
    prepared_name = os.getenv("AUDITOR_NAME", "DR. D.V. Saharan")
    prepared_role = os.getenv("AUDITOR_ROLE", "Advisor")

    return {
        "document_no": DOCUMENT_NO,
        "revision_no": REVISION_NO,
        "effective_date": _dash(os.getenv("INSPECTION_FORM_EFFECTIVE_DATE")),
        "report_no": report_no,
        "inspection_reference_no": assignment,
        "confidentiality": CONFIDENTIALITY,
        "report_date": dated,
        "client": {
            "organization": _dash(insurance.get("insurance_company")),
            "client_reference_no": _dash(insurance.get("policy_number")),
            "claim_reference_no": _dash(insurance.get("claim_incident_number")),
            "work_order_no": assignment,
            "date_of_receipt": "—",
            "date_of_inspection": dated,
            "date_of_report": dated,
            "mode_received": "Electronic",
            "document_count": str(source_count or "—"),
            "location_mode": "Desk review of electronic records",
        },
        "patient": {
            "name": _dash(patient.get("name")),
            "age_dob": age,
            "gender": _dash(patient.get("sex") or patient.get("gender")),
            "uhid": _dash(patient.get("hospital_reg_no") or patient.get("uhid") or patient.get("mrn")),
            "ipd": _dash(patient.get("ip_number") or patient.get("ipd") or claim.get("ipd_number") or claim.get("admission_no")),
            "policy": _dash(insurance.get("policy_number")),
            "claim_no": _dash(insurance.get("claim_incident_number")),
            "hospital": _dash(claim.get("hospital")),
            "admission": _dash(claim.get("date_of_admission")),
            "discharge": _dash(claim.get("date_of_discharge")),
            "diagnosis": diagnosis,
            "procedure": _dash(claim.get("procedure_or_surgery")),
            "claim_type": _claim_type(data),
        },
        "inventory": inventory,
        "identification": identification,
        "identification_finding": identification_finding,
        "completeness_rows": completeness_rows,
        "overall_completeness": completeness,
        "missing_documents": _missing_line(inventory),
        "chronology": chronology,
        "overall_chronology": chronology_overall,
        "chronology_observation": chronology_note,
        "consistency": consistency,
        "overall_consistency": consistency_overall,
        "cross_document": cross,
        "traceability": trace_rows,
        "traceability_result": trace_result,
        "authenticity_result": authenticity,
        "authenticity_note": _dash((codes or {}).get("summary")),
        "correlation": correlation,
        "correlation_status": correlation_status,
        "discrepancies": discrepancies,
        "conformity_code": conformity,
        "conformity_basis": basis,
        "requirements_not_fulfilled": requirements,
        "objective_evidence": _clip(evidence, 700),
        "overall_findings": finding_lines,
        "conclusion_code": conformity,
        "conclusion_remarks": remarks,
        "prepared_by": {
            "name": prepared_name,
            "designation": prepared_role,
            "authorization_no": "",
            "date": dated,
        },
        "limitations": list(LIMITATIONS),
    }


def _cross_summary(rows: Sequence[dict]) -> str:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    parts = [f"{code} {count}" for code, count in counts.items()]
    return "Cross-check results: " + ", ".join(parts) + "."


def _conclusion_remarks(code: str, completeness: str, discrepancies: Sequence[dict], chronology_note: str) -> str:
    count = len(discrepancies)
    lead = {
        "C": "The inspected documents are classified as conforming.",
        "C-O": "The inspected documents are classified as conforming with observation(s).",
        "NC": "The inspected documents are classified as non-conforming.",
        "UC": "The inspected documents are classified as unable to conclude.",
    }.get(code, "The inspected documents were classified from the stored case file.")
    return (
        f"{lead} Completeness is {completeness}. {chronology_note} "
        f"Documentary observations recorded: {count}. "
        "This conclusion is limited to document verification and does not approve or reject the insurance claim."
    )
