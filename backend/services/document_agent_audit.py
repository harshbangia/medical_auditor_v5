"""AI Studio–style document agent audit.

Mirrors the working Google AI Studio Playground flow:
  upload case PDFs → Gemini multimodal → HTML Expert Opinion report

Bypasses the legacy OCR → RAG → JSON pipeline when
AUDIT_PIPELINE=document_agent (default when set).
"""

from __future__ import annotations

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
_SAMPLE_HTML = Path.home() / "Downloads" / "ai_studio_code (1).html"


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
    style = """

## Output for Glowix web (mandatory)

Return a single complete HTML document (start with <!DOCTYPE html>) for print/PDF.
Match the visual quality of a Glowix letterhead claim audit:
- Company header Glowix Medical Services Private Limited
- Numbered section headers with deep clinical/policy analysis
- Tables for patient/policy/admission/financials/checklist
- Detailed auditor observations with evidence and source filenames
- Final verdict and recommendations
- Evidence & source references

Do NOT wrap the HTML in markdown fences.
Do NOT invent claim IDs, ages, amounts, diagnoses, or policy clauses.
Prefer Assessor → Aadhaar → Final bill → Discharge → clinical docs when sealing facts.
OCR name spelling variants of the same patient are Low KYC notes, not High fraud.
"""
    return base + style


def _user_prompt(guideline_names: Sequence[str]) -> str:
    starter = _read_text(
        _STARTER,
        fallback="Audit this case as Glowix Medical Services. Produce a full Expert Opinion.",
    )
    gl = ", ".join(n for n in guideline_names if n) or "(none — use uploaded guideline PDFs if any)"
    return (
        f"{starter}\n\n"
        f"Guidelines selected in Glowix UI: {gl}\n"
        f"All attached PDFs are the case file (and optional guidelines/policy). "
        f"Read every page that is clinically or financially relevant.\n"
        f"Return ONLY the full HTML report."
    )


def _wait_file_active(client: Any, uploaded: Any, timeout_s: float = 180.0) -> Any:
    """Poll Files API until the upload is ACTIVE (or return as-is if no state)."""
    name = getattr(uploaded, "name", None) or ""
    deadline = time.time() + timeout_s
    current = uploaded
    while time.time() < deadline:
        state = str(getattr(current, "state", "") or "")
        # Enum may be FileState.ACTIVE or "ACTIVE"
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
            # Older SDK signature
            fobj = client.files.upload(file=path)
        fobj = _wait_file_active(client, fobj)
        uploaded.append(fobj)
        print(f"✅ Gemini file ready: {name} → {getattr(fobj, 'name', '')}", flush=True)
    return uploaded, temp_paths


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:html|HTML)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_meta(html: str) -> Dict[str, Any]:
    """Best-effort header fields for Glowix UI from HTML report."""

    def _cell_after(label: str) -> str:
        pat = re.compile(
            rf"{re.escape(label)}</td>\s*<td[^>]*>\s*(.*?)\s*</td>",
            re.I | re.S,
        )
        m = pat.search(html)
        if not m:
            return ""
        raw = re.sub(r"<[^>]+>", " ", m.group(1))
        return re.sub(r"\s+", " ", raw).strip()

    name = _cell_after("Patient Name") or _cell_after("Insured Patient Name")
    # Strip age/sex suffix like "(40Y / Female)"
    name_clean = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    age_sex = ""
    m = re.search(r"\(([^)]*Female|[^)]*Male|[^)]*\d+\s*[Yy])", name)
    if m:
        age_sex = m.group(1)
    age = ""
    sex = ""
    am = re.search(r"(\d{1,3})\s*[Yy]", age_sex or name)
    if am:
        age = am.group(1)
    if re.search(r"female", name + age_sex, re.I):
        sex = "Female"
    elif re.search(r"male", name + age_sex, re.I):
        sex = "Male"

    claim_ref = _cell_after("Claim Reference") or _cell_after("Claim Incident No")
    claim_no = re.sub(r"\s*/.*$", "", claim_ref).strip()
    claim_no = re.sub(r"[^\d.]", "", claim_no.split()[0]) if claim_no else ""

    policy = _cell_after("Policy Number / Type") or _cell_after("Policy Number")
    policy = re.sub(r"\s*/.*$", "", policy).strip()
    pol_m = re.search(r"(H[A-Z0-9]{5,12})", policy, re.I)
    if pol_m:
        policy = pol_m.group(1).upper()

    hospital = _cell_after("Hospital / Location") or _cell_after("Name of Hospital")
    hospital = re.sub(r"\s*,.*$", "", hospital).strip() if hospital else ""

    admit = _cell_after("Admission / Discharge") or _cell_after("Date of Admission")
    doa = ""
    dod = ""
    if " to " in admit.lower():
        parts = re.split(r"\s+to\s+", admit, flags=re.I)
        doa = parts[0].strip()
        dod = parts[1].strip() if len(parts) > 1 else ""
    else:
        doa = admit

    diagnosis = _cell_after("Primary Diagnosis") or _cell_after("Final Diagnosis")
    billed = _cell_after("Total Claimed / Billed") or _cell_after("Total Hospital Bill")
    verdict_raw = (
        _cell_after("Final Audit Decision")
        or _cell_after("Final Claim Verdict")
        or ""
    )
    verdict_l = verdict_raw.lower()
    if "non" in verdict_l and "approv" in verdict_l:
        compliance = "Non-Compliant"
        recommended = "No"
    elif "partial" in verdict_l:
        compliance = "Partially Compliant"
        recommended = "Yes"
    elif "approv" in verdict_l or "recommend" in verdict_l:
        compliance = "Compliant"
        recommended = "Yes"
    elif "reject" in verdict_l or "repudiat" in verdict_l or "not recommend" in verdict_l:
        compliance = "Non-Compliant"
        recommended = "No"
    else:
        compliance = "Cannot Determine"
        recommended = "No"

    insurer = _cell_after("Insurer / Sum Insured") or _cell_after("Insurance Company")
    insurer = re.sub(r"\s*/.*$", "", insurer).strip()

    return {
        "patient_details": {
            "name": name_clean or name or "NA",
            "age": age,
            "sex": sex,
        },
        "insurance_details": {
            "insurance_company": insurer,
            "policy_number": policy,
            "claim_incident_number": claim_no.split(".")[0] if claim_no else "",
            "tpa": "NA",
        },
        "claim_details": {
            "hospital": hospital,
            "date_of_admission": doa,
            "date_of_discharge": dod,
            "diagnosis": diagnosis,
            "total_hospital_bill": billed,
            "nature_of_admission": "",
            "procedure_or_surgery": "",
        },
        "financial_review": {
            "total_hospital_bill": billed,
            "recommended_approval_amount": "",
            "net_claimable_amount": "",
        },
        "compliance_verdict": compliance,
        "claim_recommended": recommended,
        "claim_not_recommended": "Yes" if recommended == "No" else "No",
        "auditor_conclusion": verdict_raw or compliance,
        "inference": verdict_raw or compliance,
    }


def run_document_agent_audit(
    file_items: List[Tuple[str, bytes]],
    *,
    guidelines: Optional[List[str]] = None,
    progress: ProgressFn = _noop,
    guideline_pdf_items: Optional[List[Tuple[str, bytes]]] = None,
) -> dict:
    """Run Studio-equivalent multimodal audit; return Glowix-compatible result + HTML."""
    from google import genai
    from google.genai import types

    progress("starting", 5, "Starting Gemini document agent…")
    key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is required for document_agent pipeline")

    model = env("DOCUMENT_AGENT_MODEL") or model_for("audit")
    timeout_ms = int(env("GEMINI_HTTP_TIMEOUT_MS") or "600000")  # 10 min for multi-PDF

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

        config = types.GenerateContentConfig(
            system_instruction=_system_instruction(),
            automatic_function_calling={"disable": True},
            # Large HTML reports
            max_output_tokens=int(env("DOCUMENT_AGENT_MAX_OUTPUT_TOKENS") or "65536"),
        )
        # Gemini 3.x may reject temperature — omit for gemini-3*
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

        html = _strip_fences(raw)
        if "<html" not in html.lower():
            # Model returned markdown/text — wrap minimally
            html = (
                "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                "<title>Glowix Medical Audit</title></head><body>"
                f"<pre style='white-space:pre-wrap;font-family:system-ui'>{html}</pre>"
                "</body></html>"
            )

        progress("verify", 92, "Assembling Glowix report…")
        meta = _extract_meta(html)
        session_id = str(uuid4())
        today = datetime.utcnow().strftime("%d-%m-%Y")
        result: Dict[str, Any] = {
            **meta,
            "report_format": "html",
            "report_html": html,
            "audit_engine": "document_agent",
            "session_id": session_id,
            "report_date": today,
            "observations": [],
            "clinical_findings": [],
            "fraud_abuse": {"risk_level": "", "findings": []},
            "document_sources": [
                {"filename": n, "pages": None} for n, _ in file_items
            ],
            "guidelines_used": list(guidelines or []),
        }
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
    """legacy | document_agent"""
    raw = (env("AUDIT_PIPELINE") or "document_agent").strip().lower()
    if raw in {"legacy", "classic", "ocr", "notebook"}:
        return "legacy"
    return "document_agent"
