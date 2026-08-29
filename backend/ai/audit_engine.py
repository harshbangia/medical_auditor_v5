import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import backend.config  # noqa: F401 — load .env before LLM client

from backend.ai.case_profiler import normalize_str_list, profile_to_audit_context
from backend.ai.drug_normalizer import build_medication_evidence_section
from backend.utils.case_facts_ledger import format_ledger_for_audit
from backend.agents.orchestrator import mcr_context_for_audit
from backend.llm_client import ImageInput, get_llm_provider, model_for

_VISION_BATCH_SIZE = 1  # one image per API call — avoids multimodal 400 errors

_VISION_PROMPT = """You are assisting an INSURANCE MEDICAL AUDITOR reviewing hospital claim documents.

Each image is one page of a claim file. It may be:
- a clinical image (X-ray, CT/MRI slice, ultrasound, wound photo, histopath slide, ECG, etc.)
- a handwritten doctor's consultation note, prescription, or operative note

For other document types — typed radiology reports, typed lab reports, hospital
letters, money receipts, ID cards, etc. — the text has ALREADY been transcribed
upstream and forwarded to the auditor. Do NOT re-transcribe and do NOT critique
the print/scan resolution of typed pages. Reply only:
"Page N: Typed document (already transcribed) — Informational"

For genuine clinical images, do all of the following:
1. Describe ONLY what is visible — do not invent findings.
2. State whether findings SUPPORT or CONTRADICT typical documentation for the stated diagnosis.
3. Flag clinically relevant quality issues (poor exposure, wrong view, missing comparison,
   illegible anatomical labels) — but NEVER critique a page just because the PDF render is
   low resolution. Image quality complaints must be about the underlying clinical capture,
   not the document scan.
4. Note if the image alone is INSUFFICIENT to justify the billed procedure/admission.

For handwritten clinical notes, transcribe the medically meaningful content (dates,
diagnoses, drugs, dosages, plan) and state how it bears on the claim. Do not critique
handwriting quality.

If the page is genuinely blank or shows only a logo with no readable content, say
"Blank page — no audit content."

Format per image (multi-line is fine):
Page N: [Document type] — [Key clinical content] — [Support / Challenge / Insufficient / Informational] — [Notes for auditor]
"""

_CHALLENGE_ANSWERS = (
    "Supported",
    "Partially Supported",
    "Not Supported",
    "Insufficient Evidence",
)


def _normalize_image(img, fallback_page=1):
    if isinstance(img, dict):
        return {
            "base64": img.get("base64") or img.get("image_base64") or "",
            "page": img.get("page") or fallback_page,
            "source": img.get("source") or img.get("filename") or "",
        }
    return {"base64": str(img), "page": fallback_page, "source": ""}


def _select_images_for_audit(images, max_images=12):
    """Prefer spread across documents and pages; keep clinical diversity."""
    if not images:
        return []
    normalized = [_normalize_image(img, i + 1) for i, img in enumerate(images)]
    normalized = [img for img in normalized if img.get("base64")]
    if len(normalized) <= max_images:
        return normalized

    # Group by source file, take top pages from each
    by_source = {}
    for img in normalized:
        src = img.get("source") or "_default"
        by_source.setdefault(src, []).append(img)

    selected = []
    per_file = max(2, max_images // max(len(by_source), 1))
    for src, imgs in by_source.items():
        step = max(1, len(imgs) // per_file)
        for i in range(0, len(imgs), step):
            if len(selected) >= max_images:
                break
            selected.append(imgs[i])
        if len(selected) >= max_images:
            break

    if len(selected) < max_images:
        for img in normalized:
            if img not in selected:
                selected.append(img)
            if len(selected) >= max_images:
                break
    return selected[:max_images]


def _analyze_image_batch(batch, case_hint: str = "") -> str:
    """Analyze images one per request — provider-agnostic multimodal."""
    parts = []
    provider = get_llm_provider()
    vision_model = model_for("vision")
    for img in batch:
        b64 = (img.get("base64") or "").strip()
        if not b64:
            continue
        label = f"Page {img['page']}"
        if img.get("source"):
            label += f" ({img['source']})"
        text_parts = [_VISION_PROMPT]
        if case_hint:
            text_parts.append(f"Case context: {case_hint[:800]}")
        text_parts.append(label)
        try:
            text = provider.complete(
                model=vision_model,
                text_parts=text_parts,
                images=[ImageInput(b64=b64, detail="low")],
            )
            if text:
                parts.append(text)
        except Exception as exc:
            parts.append(f"[VISION ERROR page {img['page']}]: {exc}")
    return "\n\n".join(parts).strip()


def analyze_case_images(images, case_hint: str = "") -> str:
    """Batch vision analysis — fewer API calls, faster than one image per call."""
    selected = _select_images_for_audit(images, max_images=int(os.getenv("MAX_VISION_IMAGES", "12")))
    if not selected:
        return ""

    batches = [
        selected[i : i + _VISION_BATCH_SIZE]
        for i in range(0, len(selected), _VISION_BATCH_SIZE)
    ]

    parts = []
    workers = min(3, len(batches))
    if workers <= 1:
        for batch in batches:
            parts.append(_analyze_image_batch(batch, case_hint))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_analyze_image_batch, batch, case_hint) for batch in batches]
            for fut in as_completed(futures):
                parts.append(fut.result())

    return "\n\n".join(p for p in parts if p.strip())


def _parse_audit_json(raw_output: str) -> dict:
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    cleaned = re.sub(r'"\s*\n\s*"', '",\n"', cleaned)
    cleaned = re.sub(r",\s*}", "}", cleaned)
    cleaned = re.sub(r",\s*]", "]", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"error": "Invalid AI response", "raw_output": cleaned, "parse_error": str(exc)}


def _ensure_challenge_fields(data: dict) -> dict:
    """Ensure adversarial sections exist without injecting bland filler."""
    data.setdefault("guideline_deviations", [])
    data.setdefault("challenge_points", [])
    data.setdefault("compliance_verdict", "")
    data.setdefault("imaging_findings", [])

    obs = data.get("observations") or []
    if not isinstance(obs, list):
        obs = []

    challenge_count = 0
    for item in obs:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer") or "").strip()
        analysis = str(item.get("analysis") or "").lower()
        if answer in ("Not Supported", "Partially Supported", "Insufficient Evidence"):
            challenge_count += 1
        elif any(w in analysis for w in ("deviat", "contradict", "not support", "gap", "missing", "unjustif", "question")):
            challenge_count += 1

    if not data.get("challenge_points") and obs:
        for item in obs[:5]:
            if isinstance(item, dict) and item.get("question"):
                ans = str(item.get("answer") or "")
                if ans in ("Not Supported", "Partially Supported", "Insufficient Evidence"):
                    data["challenge_points"].append(
                        f"{item['question']} → {ans}: requires hospital clarification."
                    )

    if not data.get("compliance_verdict"):
        if challenge_count >= 3:
            data["compliance_verdict"] = "Partially Compliant"
        elif challenge_count >= 1:
            data["compliance_verdict"] = "Partially Compliant"
        else:
            data["compliance_verdict"] = "Cannot Determine — review challenge points"

    return data


def _format_insurance_facts_block(facts: dict) -> str:
    if not facts:
        return ""
    lines = ["=== INSURANCE FACTS (extracted from claim letters — use these for insurance_details) ==="]
    for key, label in (
        ("insurance_company", "Insurance company"),
        ("policy_number", "Policy number"),
        ("claim_incident_number", "Claim / incident number"),
        ("member_code", "Member code"),
        ("policy_period", "Policy period"),
    ):
        val = str((facts or {}).get(key) or "").strip()
        if val:
            lines.append(f"{label}: {val}")
    if len(lines) <= 1:
        return ""
    lines.append(
        "Populate insurance_details from this section. Do NOT leave insurance_company blank "
        "if it is listed here."
    )
    return "\n".join(lines)


def _format_claim_facts_block(facts: dict) -> str:
    if not facts:
        return ""
    lines = [
        "=== CLAIM FACTS (deterministic extraction — prefer pre-auth / clinical over query letter) ==="
    ]
    for key, label in (
        ("hospital", "Hospital"),
        ("consultation_date", "Consultation date"),
        ("date_of_admission", "Date of admission (actual)"),
        ("proposed_hospitalization_date", "Proposed hospitalization date (query letter)"),
        ("date_of_discharge", "Date of discharge"),
    ):
        val = str((facts or {}).get(key) or "").strip()
        if val:
            src = str((facts or {}).get(f"{key}_source") or "").strip()
            lines.append(f"{label}: {val}" + (f" [from {src}]" if src else ""))
    discrepancies = facts.get("date_discrepancies") or []
    for item in discrepancies:
        if isinstance(item, dict) and item.get("message"):
            lines.append(f"DATE DISCREPANCY: {item['message']}")
    if len(lines) <= 1:
        return ""
    lines.append(
        "Use these values and sources for claim_details. If DATE DISCREPANCY lines appear, "
        "mention them in documentation_gaps and challenge_points — do NOT hide conflicting dates."
    )
    return "\n".join(lines)


def _build_audit_prompt(
    case_context: str,
    guideline_text: str,
    guideline_name: str,
    image_analysis: str,
    user_question: Optional[str],
    insurance_facts_block: str = "",
    clinical_synthesis: str = "",
    claim_facts_block: str = "",
    guidelines_used: Optional[List[str]] = None,
    case_evidence_block: str = "",
) -> str:
    imaging_block = image_analysis.strip() or "No clinical images were extracted from uploaded PDFs."
    has_images = bool(image_analysis.strip())
    multi_guideline = bool(guidelines_used and len(guidelines_used) > 1)
    guidelines_json = json.dumps(guidelines_used or [guideline_name])

    multi_block = ""
    if multi_guideline:
        multi_block = """
MULTIPLE GUIDELINES APPLY to this case. Cross-examine the hospital against EACH guideline separately.
- Use the section headers (=== GUIDELINE: filename ===) to identify which source supports each rule.
- In guideline_deviations, set source_guideline to the specific guideline filename when known.
- If guidelines conflict, state the conflict and which standard is stricter for claim denial.
"""

    if user_question:
        guideline_header = f"GUIDELINE(S) ({guideline_name})"
        return f"""You are a SENIOR INSURANCE MEDICAL AUDITOR answering a follow-up question.

Answer ONLY from case documents, image analysis, and guideline excerpts. Be direct and evidence-based.
If the hospital's position is weak, say so clearly.
{multi_block}
Return ONLY JSON:
{{
  "mode": "qa",
  "question": "{user_question}",
  "answer": "",
  "justification": "",
  "evidence_used": []
}}

CASE:
{case_context}

IMAGE ANALYSIS:
{imaging_block}

{guideline_header}:
{guideline_text}
"""

    return f"""You are a SENIOR INSURANCE MEDICAL AUDITOR preparing an OFFICIAL medico-legal audit report.

YOUR PRIMARY DUTY: CHALLENGE the hospital's clinical and billing decisions against the attached guideline(s).
You represent the payer/insurer — NOT the hospital. Default stance: SKEPTICAL until documentation proves compliance.
{multi_block}
You MUST:
- Cross-examine whether admission, procedures, investigations, and charges are medically necessary per guideline
- Identify deviations, missing prerequisites, and documentation that fails to justify the claim
- Use image analysis when provided — do NOT claim imaging is "missing" if IMAGE ANALYSIS section has content
- Use the MEDICATION EVIDENCE section (if present) when judging prior medical therapy — do NOT claim a drug
  class was "never tried" if the section lists a brand from that class
- Use the INSURANCE FACTS section for insurance_details — never leave insurance_company, policy_number,
  or claim_incident_number blank if listed there (query/reply letters are the preferred source)
- Use the CLAIM FACTS section for claim_details dates and hospital — never leave consultation_date
  or date_of_admission blank if listed there; use date_of_admission for the ACTUAL admission date
  from pre-auth/clinical documents; put the query letter proposed hospitalization date in
  proposed_hospitalization_date only — never substitute it for date_of_admission when a pre-auth
  admission date is present
- Hospital name: prefer the hospital from the insurer query/reply letter (often on the Member Code
  line). NEVER use an address fragment (e.g. "Near Civil Hospital", "Opp. Bus Stand") as hospital
- Use the CLINICAL VISIT SYNTHESIS section when reporting symptom duration vs treatment course — these are
  DIFFERENT facts from DIFFERENT pages; report BOTH separately in clinical_findings and observations
- Produce at least 5 observations; minimum 3 must challenge or question the hospital (answer: Not Supported, Partially Supported, or Insufficient Evidence)
- Every observation analysis MUST name the source document(s) reviewed, using the exact
  filename from "=== Source document: <filename> ===" markers (e.g. "In Accord 5, Discharge
  summary with final bill.pdf …"). Pinpoint what that document shows or fails to show.
- guideline_deviations MUST follow: Guideline expectation → Case evidence → Source document(s).
- Cite specific case facts AND specific guideline expectations in every observation

Observation "answer" MUST be exactly one of: {_CHALLENGE_ANSWERS}

compliance_verdict MUST be one of: Compliant | Partially Compliant | Non-Compliant | Cannot Determine

claim_details.nature_of_admission MUST be one of:
  Planned / Elective | Emergency | Day Care | Maternity | Unknown
RULES for nature_of_admission:
- Use "Emergency" ONLY when the documents explicitly use the words "emergency", "ER",
  "casualty", "walk-in", "trauma", or describe an acute event (e.g. MI, stroke, RTA, sepsis,
  status epilepticus, acute abdomen, acute vertebral/compression fracture after fall with
  immobility) where admission happened within 24 hours of onset.
- Acute traumatic fractures managed with inpatient medical management (IV analgesics/steroids,
  observation) are Emergency admissions — do NOT challenge them as elective surgery or demand
  a failed outpatient "conservative care" trial first.
- A pre-authorization request filed days in advance for a chronic condition (trigeminal
  neuralgia, OA knee, cataract, BPH, planned CABG/PTCA, elective hernia, planned hysterectomy,
  etc.) is "Planned / Elective" — even if the patient is admitted to an ICU/HDU post-op.
- If the documents do not indicate either, use "Unknown" — never guess "Emergency".

DO NOT write generic boilerplate. DO NOT defend the hospital when evidence is weak.
DO NOT hallucinate facts not in the case. When data is missing, use "Insufficient Evidence" and list in documentation_gaps.
DO NOT critique typed radiology/lab reports as "low quality images" — they are typed
documents; quality complaints must be about the underlying clinical capture, not the PDF scan.
DO NOT treat uploaded policy wordings or clinical guideline PDFs as patient clinical records.
Diagnosis rules (critical):
- claim_details.diagnosis MUST come from Provisional/Final/Primary Diagnosis or Nature of Illness —
  NEVER from radiology Impression text (thalamic bleed measurements, HU values, midline shift wording).
- Do NOT deny a claim solely for OCR name variants of the same patient (Gagan Deep / Gagandeep / Gulati).
- For acute ICH / unconscious ICU admissions, prioritize clinical queries on hospitalization duration,
  antibiotic justification, and line of management — not identity fraud.

clinical_findings MUST include SEPARATE rows when documented:
  1. Symptom duration at first/s subsequent consult (e.g. "Symptom duration at presentation: 3 days")
  2. Prescribed medication course duration (e.g. "Medical therapy course: 7 days — drug names from prescription")
  3. Follow-up interval (e.g. "Follow-up advised after: 2 weeks")
Do NOT copy drug names from these prompt examples — use ONLY drugs documented in THIS case.
Do NOT report only the shorter duration when both 1-month symptom history AND 2-month
treatment course appear on different pages.

observations MUST echo EVERY row you place in clinical_findings — no observation may
cite only one duration when clinical_findings lists several:
  • Each observation analysis MUST cite which uploaded document supports or contradicts the point.
  • Observations MUST NOT contradict clinical_findings.
  • Do NOT mention drugs (e.g. anticonvulsants, PPIs) unless they appear in THIS case's prescriptions.

Before finalising JSON, self-check: every clinical_findings row appears verbatim or paraphrased
in at least one observations[].analysis block.

clinical_checklist rules (specialty-aware — only include clinically relevant rows):
- Include "MRI Report" ONLY for neurology / brain / spine / neuralgia / vertebral fracture cases where MRI is indicated.
  NEVER include MRI Report for cardiology, ACS, CABG, hypoglycemia, or general medical cases.
- Mark "CT Scan Report" as YES ONLY when a CT/HRCT report with impression is present in clinical files.
  NEVER mark CT YES for lumbar X-ray/MRI-only trauma cases without a CT report.
- Mark "Antibiotic Therapy" as YES when antibiotics are prescribed or culture/sensitivity is reported.
- Mark "Cardiac Assessment" as YES ONLY when ECG, echocardiography, troponin, Holter, or CAG planning is documented
  AND the case is cardiac. NEVER mark Cardiac YES for pure musculoskeletal/spine trauma.
- Mark "Medication Trials" as YES if prescription documents Zenoxa/Tegretol/Lyrica etc. (neuralgia cases only).
  Do NOT include Medication Trials for acute trauma medical-management admissions.
- Do NOT mark medication trials NO when oxcarbazepine/carbamazepine brands are in prescriptions.
- Adult serum creatinine is typically 0.5–15 mg/dl; if OCR shows >15, re-read the lab report for a decimal point (e.g. 1.9 not 19).
- Do NOT challenge missing spirometry unless COPD is explicitly diagnosed in the case documents.

Medication / billing rules (critical):
- Pantoprazole / Pan / Pantop / Pantocid / PPI is ROUTINE inpatient ulcer prophylaxis.
  NEVER list pantoprazole (or OCR variant "pentaprazole") as unadvised, excluded, non-payable,
  or unnecessary medicine. Do not put PPI in excluded_items_billed or non_payable_amount.
- Only flag medicines that are truly contraindicated (allergy), policy-excluded by name, or
  clinically inappropriate for the diagnosis.

Fraud / abuse section:
- Populate fraud_abuse_findings for misrepresentation, non-disclosure of PED, conflicting history,
  date discrepancies, unbundled billing, room upcoding, and fraudulent statements — with evidence.

Financial savings:
- Populate claim_savings_line_items with billed vs admissible amounts and amount_saved per item.
- financial_review must include total_hospital_bill, non_payable_amount, net_claimable_amount,
  amount_saved, and savings_percentage (e.g. "12.5%").

Inference and report summary (critical):
- "inference" must be a clear 2–4 sentence auditor inference: whether treatment appears medically
  necessary, overall compliance stance, and whether to approve / hold / deny — with the main reason.
  Do NOT write vague lines like "partial compliance with guidelines" alone.
- "report_summary" must be 5–8 short bullets giving a gist of the WHOLE report:
  patient/hospital/diagnosis, key clinical points, fraud/abuse or documentation risks,
  financial claim vs amount saved, and final recommendation.
- Do NOT include doctor registration validation (handled manually outside this report).

Typed MRI in case text: if IMPRESSION mentions neurovascular conflict / grade III, populate
imaging_findings from that report — do NOT claim MRI report is missing when CT/HRCT is present instead.

{"Clinical images WERE analyzed — use IMAGE ANALYSIS below as imaging evidence." if has_images else "No image analysis available — flag missing imaging in documentation_gaps if clinically required by guideline."}

Return ONLY JSON:
{{
  "mode": "audit",
  "guideline_used": "{guideline_name}",
  "guidelines_used": {guidelines_json},
  "compliance_verdict": "",
  "guideline_deviations": [
    {{"issue": "", "guideline_expectation": "", "case_evidence": "", "severity": "High|Medium|Low", "source_guideline": ""}}
  ],
  "challenge_points": [
    "Specific question the hospital must answer to justify the claim"
  ],
  "patient_details": {{"name": "", "age": "", "sex": ""}},
  "insurance_details": {{"insurance_company": "", "policy_number": "", "policy_period": "", "claim_incident_number": ""}},
  "claim_details": {{"hospital": "", "consultation_date": "", "date_of_admission": "", "date_of_discharge": "", "nature_of_admission": "", "procedure_or_surgery": "", "diagnosis": ""}},
  "imaging_findings": [{{"type": "", "finding": "", "clinical_correlation": "", "consistency_with_diagnosis": ""}}],
  "clinical_findings": [{{"parameter": "", "value": "", "normal_range": "", "comment": "", "source": "exact source PDF filename"}}],
  "documentation_gaps": ["Specific gap and why it matters for claim validity"],
  "clinical_checklist": [{{"area": "", "available": "YES or NO", "remarks": ""}}],
  "timeline": [{{"date": "", "event": ""}}],
  "observations": [
    {{"question": "Sharp audit question challenging hospital action", "analysis": "4+ sentences: case fact → guideline rule → gap/deviation → claim impact", "answer": "Supported|Partially Supported|Not Supported|Insufficient Evidence"}}
  ],
  "auditor_observation_summary": "Direct narrative: what the hospital did, what guideline requires, where they fall short or must prove more",
  "treatment_billing_audit": {{"room_category_admitted": "", "room_category_eligible": "", "procedures_performed": "", "cross_checked_with_preauth": "", "excluded_items_billed": "", "charges_appropriate": ""}},
  "financial_review": {{"total_hospital_bill": "", "non_payable_amount": "", "net_claimable_amount": "", "recommended_approval_amount": "", "patient_liability": "", "amount_saved": "", "savings_percentage": ""}},
  "fraud_abuse_findings": [{{"category": "misrepresentation|billing_abuse|documentation_abuse|policy_compliance", "indicator": "", "evidence": "", "severity": "High|Medium|Low", "recommendation": ""}}],
  "claim_savings_line_items": [{{"item": "", "billed_amount": "", "admissible_amount": "", "amount_saved": "", "reason": ""}}],
  "inference": "2-4 sentences: clinical necessity + claim stance (approve / hold / deny) with the main reason",
  "report_summary": [
    "Brief bullet covering patient / hospital / diagnosis",
    "Brief bullet on key clinical or documentation finding",
    "Brief bullet on fraud/abuse or compliance risk",
    "Brief bullet on financial claim vs amount saved",
    "Brief bullet on final recommendation"
  ],
  "auditor_conclusion": "",
  "remarks": "",
  "qa_section": []
}}

{insurance_facts_block}

{claim_facts_block}

{case_evidence_block}

{clinical_synthesis}

CASE:
{case_context}

IMAGE ANALYSIS:
{imaging_block}

GUIDELINE EXCERPTS ({guideline_name}):
{guideline_text}
"""


def _call_audit_llm(prompt: str) -> str:
    """Call audit model with JSON output via configured LLM provider."""
    provider = get_llm_provider()
    return provider.complete(
        model=model_for("audit"),
        text_parts=[prompt],
        json_mode=True,
        temperature=0.2,
    )


def run_audit(
    case_text,
    guideline_text,
    user_question=None,
    images=None,
    case_profile=None,
    guideline_name="",
    guidelines_used=None,
    image_analysis_text=None,
    insurance_facts=None,
    clinical_synthesis=None,
    claim_facts=None,
    case_evidence_block=None,
    case_facts_ledger=None,
):
    """
    Run adversarial audit. Pass image_analysis_text if already computed (pipeline parallelism).
    """
    print("Running adversarial audit engine")

    if image_analysis_text is None and images:
        hint = ""
        if case_profile:
            hint = f"{case_profile.get('diagnosis', '')} | {', '.join(normalize_str_list(case_profile.get('procedures')))}"
        image_analysis_text = analyze_case_images(images, case_hint=hint)
    else:
        image_analysis_text = image_analysis_text or ""

    if case_profile:
        case_context = profile_to_audit_context(case_profile, case_text)
    else:
        case_context = case_text[:12000]

    ledger_block = format_ledger_for_audit(case_facts_ledger or {})
    if ledger_block:
        case_context = ledger_block + "\n\n" + case_context

    mcr_block = mcr_context_for_audit(case_facts_ledger, claim_facts)
    if mcr_block:
        case_context = mcr_block + "\n\n" + case_context

    if case_facts_ledger:
        case_context = (
            "IMPORTANT: Use MEDICAL CASE RECORD and CASE FACTS LEDGER as authoritative for "
            "patient name, diagnosis, dates, and per-document summaries. Do NOT contradict "
            "these facts unless a specific source document excerpt clearly overrides them. "
            "Every observation MUST cite source document filename(s).\n\n"
            + case_context
        )

    if image_analysis_text.strip():
        case_context = (
            "[IMAGING ANALYZED — see IMAGE ANALYSIS section]\n" + case_context
        )

    drug_evidence = build_medication_evidence_section(case_text)
    if drug_evidence:
        case_context = drug_evidence + "\n\n" + case_context

    insurance_block = _format_insurance_facts_block(insurance_facts or {})
    claim_block = _format_claim_facts_block(claim_facts or {})
    synthesis_block = (clinical_synthesis or "").strip()
    if case_evidence_block is None:
        from backend.utils.case_evidence_detector import format_case_evidence_block
        case_evidence_block = format_case_evidence_block(case_text)

    prompt = _build_audit_prompt(
        case_context,
        guideline_text,
        guideline_name or "Clinical Guideline",
        image_analysis_text,
        user_question,
        insurance_facts_block=insurance_block,
        clinical_synthesis=synthesis_block,
        claim_facts_block=claim_block,
        case_evidence_block=case_evidence_block or "",
        guidelines_used=guidelines_used,
    )

    raw_output = _call_audit_llm(prompt)
    if not raw_output:
        print("❌ Audit LLM returned empty output")
        return {"error": "Invalid AI response", "detail": "Model returned empty output"}

    data = _parse_audit_json(raw_output)
    if data.get("error"):
        print("❌ Audit JSON parse failed:", data.get("parse_error", data.get("error")))
        print("❌ Raw snippet:", raw_output[:500])
        return data

    data = _ensure_challenge_fields(data)

    if guidelines_used:
        data["guidelines_used"] = list(guidelines_used)
        if not data.get("guideline_used"):
            data["guideline_used"] = "; ".join(guidelines_used)

    data.setdefault("insurance_details", {})
    for key in ("insurance_company", "policy_number", "policy_period", "claim_incident_number"):
        data["insurance_details"].setdefault(key, "")

    inf = (data.get("inference") or "").strip()
    ac = (data.get("auditor_conclusion") or "").strip()
    if inf and not ac:
        data["auditor_conclusion"] = inf
    elif ac and not inf:
        data["inference"] = ac

    return data
