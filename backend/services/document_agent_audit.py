"""AI Studio–style document agent audit → Glowix Expert Opinion JSON/PDF.

Mirrors the working Google AI Studio Playground multimodal flow:
  upload case PDFs → Gemini reads PDFs → structured Expert Opinion fields

Output is the same JSON shape the Glowix letterhead PDF generator expects
(not HTML). UI cards and Download Expert Opinion PDF both use these fields.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from backend.config import env
from backend.llm.models import model_for

ProgressFn = Callable[[str, int, str], None]

_AGENTS_MD = (
    Path(__file__).resolve().parents[2]
    / "agents"
    / "glowix-medical-auditor"
    / ".agents"
    / "AGENTS.md"
)
_STARTER = (
    Path(__file__).resolve().parents[2]
    / "agents"
    / "glowix-medical-auditor"
    / "STARTER_PROMPT.txt"
)


def _noop(phase: str, progress: int, message: str) -> None:
    pass


def _read_text(path: Path, fallback: str = "") -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return fallback


def _system_instruction() -> str:
    base = _read_text(
        _AGENTS_MD,
        fallback="You are Glowix Medical Services senior insurance medical auditor.",
    )
    schema = """

## Output for Glowix app (MANDATORY)

Return ONLY one JSON object (no markdown fences, no HTML) with this shape:

{
  "compliance_verdict": "Compliant|Partially Compliant|Non-Compliant|Cannot Determine",
  "claim_recommended": "Yes|No",
  "claim_not_recommended": "Yes|No",
  "patient_details": {
    "name": "",
    "age": "",
    "sex": "",
    "hospital_reg_no": ""
  },
  "insurance_details": {
    "insurance_company": "",
    "policy_number": "",
    "policy_period": "",
    "claim_incident_number": "",
    "tpa": "NA"
  },
  "claim_details": {
    "hospital": "",
    "consultation_date": "",
    "date_of_admission": "",
    "date_of_discharge": "",
    "nature_of_admission": "Emergency|Planned / Elective|Day Care|Unknown",
    "provisional_diagnosis": "",
    "final_diagnosis": "",
    "diagnosis": "",
    "procedure_or_surgery": "",
    "procedure_date": "",
    "total_hospital_bill": ""
  },
  "treatment_billing_audit": {
    "room_category_admitted": "",
    "room_category_eligible": "",
    "procedures_performed": "",
    "cross_checked_with_preauth": "",
    "excluded_items_billed": "",
    "charges_appropriate": "YES|NO|NA"
  },
  "financial_review": {
    "total_hospital_bill": "",
    "non_payable_amount": "",
    "net_claimable_amount": "",
    "recommended_approval_amount": "",
    "patient_liability": ""
  },
  "clinical_checklist": [
    {"area": "Pre-authorization Approval Letter", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Admission Request Form", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Policy Copy / ID Card", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Indoor Case Papers", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Discharge Summary", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Lab / Radiology Reports/X-Ray", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Operation Notes (if any)", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Pharmacy Bills", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Implant Stickers (if any)", "available": "YES|NO|NA", "remarks": ""},
    {"area": "Prescriptions", "available": "YES|NO|NA", "remarks": ""}
  ],
  "observations": [
    {
      "question": "",
      "answer": "Supported|Partially Supported|Not Supported|Insufficient Evidence",
      "analysis": "Deep multi-paragraph evidence essay; name source PDF filenames"
    }
  ],
  "auditor_observation_summary": "",
  "fraud_abuse": {
    "risk_level": "Low|Medium|High|",
    "summary": "",
    "findings": [
      {
        "category": "",
        "indicator": "",
        "evidence": "",
        "severity": "High|Medium|Low",
        "recommendation": ""
      }
    ]
  },
  "documentation_gaps": [],
  "timeline": [{"date": "", "event": ""}],
  "clinical_findings": [
    {"parameter": "", "value": "", "normal_range": "", "comment": "", "source": ""}
  ],
  "inference": "",
  "auditor_conclusion": "",
  "remarks": "",
  "report_summary": []
}

Rules:
- Fill patient_details, insurance_details, claim_details from Assessor → Aadhaar → bill → discharge (priority).
- Never leave patient name / age / claim / policy blank if present in Assessor or KYC.
- Produce 4–6 deep observations (NotebookLM depth).
- Do not invent clause numbers, IDs, ages, or amounts.
- OCR name spelling variants of the same patient are Low KYC, not High fraud.
"""
    return base + schema


def _user_prompt(guideline_names: Sequence[str]) -> str:
    starter = _read_text(
        _STARTER,
        fallback="Audit this case as Glowix Medical Services.",
    )
    # Override HTML-only line from starter if present
    starter = re.sub(
        r"(?i)return only the full html report\.?",
        "Return ONLY the JSON Expert Opinion object.",
        starter,
    )
    gl = ", ".join(n for n in guideline_names if n) or "(none — use uploaded guideline PDFs if any)"
    return (
        f"{starter}\n\n"
        f"Guidelines selected in Glowix UI: {gl}\n"
        f"All attached PDFs are the case file (and optional guidelines/policy). "
        f"Read every clinically or financially relevant page.\n"
        f"Return ONLY the JSON object for the Glowix Expert Opinion PDF."
    )


def _wait_file_active(client: Any, uploaded: Any, timeout_s: float = 180.0) -> Any:
    name = getattr(uploaded, "name", None) or ""
    deadline = time.time() + timeout_s
    current = uploaded
    while time.time() < deadline:
        state = str(getattr(current, "state", "") or "")
        if not state or "ACTIVE" in state.upper():
            if "FAILED" in state.upper() or "ERROR" in state.upper():
                raise RuntimeError(f"Gemini file processing failed for {name}: {state}")
            if not state or state.upper().endswith("ACTIVE"):
                return current
        time.sleep(1.5)
        try:
            current = client.files.get(name=name)
        except Exception:
            return uploaded
    return current


def _upload_pdfs(
    client: Any,
    file_items: List[Tuple[str, bytes]],
    progress: ProgressFn,
) -> Tuple[List[Any], List[str]]:
    from google.genai import types

    uploaded: List[Any] = []
    temp_paths: List[str] = []
    total = len(file_items)
    for idx, (name, data) in enumerate(file_items):
        pct = 10 + int(50 * idx / max(total, 1))
        progress("upload", pct, f"Uploading to Gemini {idx + 1}/{total}: {name}")
        suffix = Path(name).suffix or ".pdf"
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        temp_paths.append(path)
        with open(path, "wb") as f:
            f.write(data)
        mime = "application/pdf"
        if suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }[suffix.lower()]
        try:
            fobj = client.files.upload(
                file=path,
                config=types.UploadFileConfig(mime_type=mime, display_name=name[:120]),
            )
        except TypeError:
            fobj = client.files.upload(file=path)
        except Exception:
            # Fallback without UploadFileConfig
            fobj = client.files.upload(file=path)
        fobj = _wait_file_active(client, fobj)
        uploaded.append(fobj)
        print(f"✅ Gemini file ready: {name} → {getattr(fobj, 'name', '')}", flush=True)
    return uploaded, temp_paths


def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def _pick(*vals: Any) -> str:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s and s.upper() not in {"NA", "N/A", "NONE", "NULL", "-", "—", "."}:
            return s
    return ""


def _case_text_from_result(result: dict) -> str:
    """Compact text corpus for Ask follow-ups (no HTML)."""
    parts: List[str] = []
    p = result.get("patient_details") or {}
    i = result.get("insurance_details") or {}
    c = result.get("claim_details") or {}
    parts.append(
        f"Patient: {p.get('name')} | Age: {p.get('age')} | Sex: {p.get('sex')}\n"
        f"Hospital: {c.get('hospital')} | Diagnosis: {c.get('diagnosis') or c.get('final_diagnosis')}\n"
        f"DOA: {c.get('date_of_admission')} | DOD: {c.get('date_of_discharge')}\n"
        f"Insurer: {i.get('insurance_company')} | Policy: {i.get('policy_number')} | "
        f"Claim: {i.get('claim_incident_number')}\n"
        f"Bill: {c.get('total_hospital_bill')}\n"
        f"Verdict: {result.get('compliance_verdict')} | Recommended: {result.get('claim_recommended')}\n"
        f"Conclusion: {result.get('auditor_conclusion') or result.get('inference')}\n"
        f"Remarks: {result.get('remarks')}"
    )
    for obs in result.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        parts.append(
            f"Q: {obs.get('question')}\nA: {obs.get('answer')}\n"
            f"{obs.get('analysis') or obs.get('justification') or ''}"
        )
    return "\n\n".join(parts).strip()


def _normalize_result(data: dict, file_items: List[Tuple[str, bytes]], guidelines: Sequence[str]) -> dict:
    """Ensure Glowix PDF + UI always get required sections."""
    result = dict(data or {})
    patient = result.setdefault("patient_details", {})
    if not isinstance(patient, dict):
        patient = {}
        result["patient_details"] = patient
    insurance = result.setdefault("insurance_details", {})
    if not isinstance(insurance, dict):
        insurance = {}
        result["insurance_details"] = insurance
    claim = result.setdefault("claim_details", {})
    if not isinstance(claim, dict):
        claim = {}
        result["claim_details"] = claim
    fin = result.setdefault("financial_review", {})
    if not isinstance(fin, dict):
        fin = {}
        result["financial_review"] = fin

    # Flatten common alternate keys Gemini may emit
    patient["name"] = _pick(
        patient.get("name"), patient.get("patient_name"), result.get("patient_name")
    ) or patient.get("name") or ""
    patient["age"] = _pick(patient.get("age"), patient.get("patient_age")) or patient.get("age") or ""
    patient["sex"] = _pick(
        patient.get("sex"), patient.get("gender"), patient.get("patient_sex")
    ) or patient.get("sex") or ""

    insurance["insurance_company"] = _pick(
        insurance.get("insurance_company"),
        insurance.get("company"),
        insurance.get("insurer"),
    ) or insurance.get("insurance_company") or ""
    insurance["policy_number"] = _pick(
        insurance.get("policy_number"), insurance.get("policy_no"), insurance.get("policy")
    ) or insurance.get("policy_number") or ""
    insurance["claim_incident_number"] = _pick(
        insurance.get("claim_incident_number"),
        insurance.get("claim_number"),
        insurance.get("claim_no"),
        insurance.get("claim_id"),
    ) or insurance.get("claim_incident_number") or ""

    claim["hospital"] = _pick(
        claim.get("hospital"), claim.get("hospital_name"), claim.get("name_of_hospital")
    ) or claim.get("hospital") or ""
    claim["date_of_admission"] = _pick(
        claim.get("date_of_admission"), claim.get("doa"), claim.get("admission_date")
    ) or claim.get("date_of_admission") or ""
    claim["date_of_discharge"] = _pick(
        claim.get("date_of_discharge"), claim.get("dod"), claim.get("discharge_date")
    ) or claim.get("date_of_discharge") or ""

    # Sync diagnosis aliases
    claim["diagnosis"] = _pick(
        claim.get("diagnosis"),
        claim.get("final_diagnosis"),
        claim.get("provisional_diagnosis"),
    ) or claim.get("diagnosis") or ""
    if claim["diagnosis"] and not claim.get("final_diagnosis"):
        claim["final_diagnosis"] = claim["diagnosis"]

    bill = _pick(
        claim.get("total_hospital_bill"),
        fin.get("total_hospital_bill"),
        claim.get("bill_amount"),
    )
    if bill:
        claim["total_hospital_bill"] = bill
        fin["total_hospital_bill"] = bill

    rec = str(result.get("claim_recommended") or "").strip()
    not_rec = str(result.get("claim_not_recommended") or "").strip()
    if rec.lower() in {"yes", "y"}:
        result["claim_recommended"] = "Yes"
        result["claim_not_recommended"] = "No"
    elif rec.lower() in {"no", "n"} or not_rec.lower() in {"yes", "y"}:
        result["claim_recommended"] = "No"
        result["claim_not_recommended"] = "Yes"
    else:
        # Infer from verdict
        v = str(result.get("compliance_verdict") or "").lower()
        if "non" in v:
            result["claim_recommended"] = "No"
            result["claim_not_recommended"] = "Yes"
        elif "partial" in v or "compliant" == v.strip():
            result["claim_recommended"] = "Yes"
            result["claim_not_recommended"] = "No"

    if not result.get("auditor_conclusion"):
        result["auditor_conclusion"] = result.get("inference") or result.get("compliance_verdict") or ""
    if not result.get("inference"):
        result["inference"] = result.get("auditor_conclusion") or ""

    # FWA list for Glowix §6
    fraud = result.get("fraud_abuse")
    if not isinstance(fraud, dict):
        fraud = {"risk_level": "", "findings": [], "summary": ""}
        result["fraud_abuse"] = fraud
    findings = fraud.get("findings") or result.get("fraud_abuse_findings") or []
    if isinstance(findings, list):
        fraud["findings"] = findings
        result["fwa_investigation"] = findings[:12]

    if not isinstance(result.get("observations"), list):
        result["observations"] = []
    if not isinstance(result.get("clinical_checklist"), list):
        result["clinical_checklist"] = []

    result["report_format"] = "expert_opinion_pdf"
    result.pop("report_html", None)
    result["audit_engine"] = "document_agent"
    result["guidelines_used"] = list(guidelines or [])
    result["document_sources"] = [{"filename": n} for n, _ in file_items]
    result["report_date"] = datetime.utcnow().strftime("%d-%m-%Y")
    result.setdefault("session_id", str(uuid4()))
    return result


def run_document_agent_audit(
    file_items: List[Tuple[str, bytes]],
    *,
    guidelines: Optional[List[str]] = None,
    progress: ProgressFn = _noop,
    guideline_pdf_items: Optional[List[Tuple[str, bytes]]] = None,
) -> dict:
    """Multimodal Gemini audit → Glowix Expert Opinion JSON for letterhead PDF."""
    from google import genai
    from google.genai import types

    progress("starting", 5, "Starting Gemini document agent…")
    key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is required for document_agent pipeline")

    model = env("DOCUMENT_AGENT_MODEL") or model_for("audit")
    timeout_ms = int(env("GEMINI_HTTP_TIMEOUT_MS") or "600000")

    try:
        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
    except Exception:
        client = genai.Client(api_key=key)

    all_files = list(file_items)
    if guideline_pdf_items:
        all_files.extend(guideline_pdf_items)

    uploaded: List[Any] = []
    temp_paths: List[str] = []
    try:
        uploaded, temp_paths = _upload_pdfs(client, all_files, progress)
        progress("ai_audit", 70, f"Running Gemini document agent ({model})…")

        contents: List[Any] = list(uploaded)
        contents.append(_user_prompt(guidelines or []))

        config_kwargs: Dict[str, Any] = {
            "system_instruction": _system_instruction(),
            "response_mime_type": "application/json",
            "automatic_function_calling": {"disable": True},
            "max_output_tokens": int(env("DOCUMENT_AGENT_MAX_OUTPUT_TOKENS") or "65536"),
        }
        config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        raw = getattr(response, "text", None) or ""
        if not raw:
            chunks = []
            for cand in getattr(response, "candidates", None) or []:
                content = getattr(cand, "content", None)
                for part in getattr(content, "parts", None) or []:
                    t = getattr(part, "text", None)
                    if t:
                        chunks.append(str(t))
            raw = "\n".join(chunks)

        data = _parse_json(raw)
        if not data:
            raise RuntimeError(
                "Document agent returned empty/invalid JSON. "
                f"Raw snippet: {(raw or '')[:400]}"
            )

        progress("verify", 92, "Assembling Glowix Expert Opinion…")
        result = _normalize_result(data, file_items, guidelines or [])
        # Fail loud if identity still empty (UI blank cards)
        name = str((result.get("patient_details") or {}).get("name") or "").strip()
        if not name or name.upper() in {"NA", "N/A", "-", "—"}:
            print("⚠️ Document agent JSON missing patient name — check Assessor PDF", flush=True)
        progress("done", 100, "Document agent audit complete")
        return result
    finally:
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        for fobj in uploaded:
            try:
                name = getattr(fobj, "name", None)
                if name:
                    client.files.delete(name=name)
            except Exception:
                pass


def audit_pipeline_mode() -> str:
    raw = (env("AUDIT_PIPELINE") or "document_agent").strip().lower()
    if raw in {"legacy", "classic", "ocr", "notebook"}:
        return "legacy"
    return "document_agent"
