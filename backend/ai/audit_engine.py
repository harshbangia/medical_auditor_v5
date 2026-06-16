import base64
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import backend.config  # noqa: F401 — load .env before OpenAI client

from backend.ai.llm_helpers import extract_response_text, image_input_part
from backend.ai.case_profiler import normalize_str_list, profile_to_audit_context
from backend.ai.drug_normalizer import build_medication_evidence_section
from backend.llm_client import get_openai_client

_VISION_BATCH_SIZE = 1  # one image per API call — avoids multimodal 400 errors
_VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini")
_AUDIT_MODEL = os.getenv("AUDIT_MODEL", "gpt-4o")

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
    """Analyze images one per request — reliable format for Responses API vision."""
    parts = []
    for img in batch:
        b64 = (img.get("base64") or "").strip()
        if not b64:
            continue
        label = f"Page {img['page']}"
        if img.get("source"):
            label += f" ({img['source']})"
        content = [
            {"type": "input_text", "text": _VISION_PROMPT},
        ]
        if case_hint:
            content.append({"type": "input_text", "text": f"Case context: {case_hint[:800]}"})
        content.append({"type": "input_text", "text": label})
        content.append(image_input_part(b64, detail="low"))
        try:
            response = get_openai_client().responses.create(
                model=_VISION_MODEL,
                input=[{"role": "user", "content": content}],
            )
            text = extract_response_text(response)
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


def _build_audit_prompt(
    case_context: str,
    guideline_text: str,
    guideline_name: str,
    image_analysis: str,
    user_question: Optional[str],
) -> str:
    imaging_block = image_analysis.strip() or "No clinical images were extracted from uploaded PDFs."
    has_images = bool(image_analysis.strip())

    if user_question:
        return f"""You are a SENIOR INSURANCE MEDICAL AUDITOR answering a follow-up question.

Answer ONLY from case documents, image analysis, and guideline excerpts. Be direct and evidence-based.
If the hospital's position is weak, say so clearly.

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

GUIDELINE ({guideline_name}):
{guideline_text}
"""

    return f"""You are a SENIOR INSURANCE MEDICAL AUDITOR preparing an OFFICIAL medico-legal audit report.

YOUR PRIMARY DUTY: CHALLENGE the hospital's clinical and billing decisions against the attached guideline.
You represent the payer/insurer — NOT the hospital. Default stance: SKEPTICAL until documentation proves compliance.

You MUST:
- Cross-examine whether admission, procedures, investigations, and charges are medically necessary per guideline
- Identify deviations, missing prerequisites, and documentation that fails to justify the claim
- Use image analysis when provided — do NOT claim imaging is "missing" if IMAGE ANALYSIS section has content
- Use the MEDICATION EVIDENCE section (if present) when judging prior medical therapy — do NOT claim a drug
  class was "never tried" if the section lists a brand from that class
- Produce at least 5 observations; minimum 3 must challenge or question the hospital (answer: Not Supported, Partially Supported, or Insufficient Evidence)
- Cite specific case facts AND specific guideline expectations in every observation

Observation "answer" MUST be exactly one of: {_CHALLENGE_ANSWERS}

compliance_verdict MUST be one of: Compliant | Partially Compliant | Non-Compliant | Cannot Determine

claim_details.nature_of_admission MUST be one of:
  Planned / Elective | Emergency | Day Care | Maternity | Unknown
RULES for nature_of_admission:
- Use "Emergency" ONLY when the documents explicitly use the words "emergency", "ER",
  "casualty", "walk-in", "trauma", or describe an acute event (e.g. MI, stroke, RTA, sepsis,
  status epilepticus, acute abdomen) where admission happened within 24 hours of onset.
- A pre-authorization request filed days in advance for a chronic condition (trigeminal
  neuralgia, OA knee, cataract, BPH, planned CABG/PTCA, elective hernia, planned hysterectomy,
  etc.) is "Planned / Elective" — even if the patient is admitted to an ICU/HDU post-op.
- If the documents do not indicate either, use "Unknown" — never guess "Emergency".

DO NOT write generic boilerplate. DO NOT defend the hospital when evidence is weak.
DO NOT hallucinate facts not in the case. When data is missing, use "Insufficient Evidence" and list in documentation_gaps.
DO NOT critique typed radiology/lab reports as "low quality images" — they are typed
documents; quality complaints must be about the underlying clinical capture, not the PDF scan.

{"Clinical images WERE analyzed — use IMAGE ANALYSIS below as imaging evidence." if has_images else "No image analysis available — flag missing imaging in documentation_gaps if clinically required by guideline."}

Return ONLY JSON:
{{
  "mode": "audit",
  "guideline_used": "{guideline_name}",
  "compliance_verdict": "",
  "guideline_deviations": [
    {{"issue": "", "guideline_expectation": "", "case_evidence": "", "severity": "High|Medium|Low"}}
  ],
  "challenge_points": [
    "Specific question the hospital must answer to justify the claim"
  ],
  "patient_details": {{"name": "", "age": "", "sex": ""}},
  "insurance_details": {{"insurance_company": "", "policy_number": "", "policy_period": "", "claim_incident_number": ""}},
  "claim_details": {{"hospital": "", "consultation_date": "", "date_of_admission": "", "date_of_discharge": "", "nature_of_admission": "", "procedure_or_surgery": "", "diagnosis": ""}},
  "imaging_findings": [{{"type": "", "finding": "", "clinical_correlation": "", "consistency_with_diagnosis": ""}}],
  "clinical_findings": [{{"parameter": "", "value": "", "normal_range": "", "comment": ""}}],
  "documentation_gaps": ["Specific gap and why it matters for claim validity"],
  "clinical_checklist": [{{"area": "", "available": "YES or NO", "remarks": ""}}],
  "timeline": [{{"date": "", "event": ""}}],
  "observations": [
    {{"question": "Sharp audit question challenging hospital action", "analysis": "4+ sentences: case fact → guideline rule → gap/deviation → claim impact", "answer": "Supported|Partially Supported|Not Supported|Insufficient Evidence"}}
  ],
  "auditor_observation_summary": "Direct narrative: what the hospital did, what guideline requires, where they fall short or must prove more",
  "treatment_billing_audit": {{"room_category_admitted": "", "room_category_eligible": "", "procedures_performed": "", "cross_checked_with_preauth": "", "excluded_items_billed": "", "charges_appropriate": ""}},
  "financial_review": {{"total_hospital_bill": "", "non_payable_amount": "", "net_claimable_amount": "", "recommended_approval_amount": "", "patient_liability": ""}},
  "inference": "",
  "auditor_conclusion": "",
  "remarks": "",
  "qa_section": []
}}

CASE:
{case_context}

IMAGE ANALYSIS:
{imaging_block}

GUIDELINE EXCERPTS ({guideline_name}):
{guideline_text}
"""


def _call_audit_llm(prompt: str) -> str:
    """Call audit model with JSON output; fall back to Chat Completions if needed."""
    client = get_openai_client()

    try:
        response = client.responses.create(
            model=_AUDIT_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            text={"format": {"type": "json_object"}},
        )
        text = extract_response_text(response)
        if text:
            return text
        print("⚠️ Responses audit returned empty text; trying chat completions fallback")
    except Exception as exc:
        print(f"⚠️ Responses audit error: {exc}; trying chat completions fallback")

    response = client.chat.completions.create(
        model=_AUDIT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()


def run_audit(
    case_text,
    guideline_text,
    user_question=None,
    images=None,
    case_profile=None,
    guideline_name="",
    image_analysis_text=None,
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

    if image_analysis_text.strip():
        case_context = (
            "[IMAGING ANALYZED — see IMAGE ANALYSIS section]\n" + case_context
        )

    drug_evidence = build_medication_evidence_section(case_text)
    if drug_evidence:
        case_context = drug_evidence + "\n\n" + case_context

    prompt = _build_audit_prompt(
        case_context,
        guideline_text,
        guideline_name or "Clinical Guideline",
        image_analysis_text,
        user_question,
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
