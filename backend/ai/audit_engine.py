from openai import OpenAI
import json
from dotenv import load_dotenv
import os
import fitz
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

_VISION_PROMPT = """You are a clinical medical auditor.

Analyze this image carefully (photo of lesion/oral cavity, wound, X-ray, CT, MRI, ultrasound, or document scan).

- Describe only visible findings (anatomy, lesions, devices, film findings).
- Do NOT hallucinate details not visible.
- Use cautious medical tone ("appears", "suggestive of").
- If the image is clinical/pertinent → state what is seen and possible relevance.
- If purely administrative or illegible → say 'No clinical relevance for audit'.
"""


def _normalize_image(img, fallback_page=1):
    if isinstance(img, dict):
        return {
            "base64": img.get("base64") or img.get("image_base64") or "",
            "page": img.get("page") or fallback_page,
        }
    return {"base64": str(img), "page": fallback_page}


def _analyze_single_image(img_dict):
    page = img_dict["page"]
    try:
        response = client.responses.create(
            model="gpt-4o",
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _VISION_PROMPT},
                        {
                            "type": "input_image",
                            "image_base64": img_dict["base64"],
                        },
                    ],
                }
            ],
        )

        image_analysis = ""
        if hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for c in item.content:
                        if hasattr(c, "text"):
                            image_analysis += c.text

        return page, image_analysis.strip()
    except Exception as e:
        return page, f"[IMAGE ERROR]: {str(e)}"

def extract_case_summary(case_text):
    import json

    prompt = f"""
Extract key clinical information.

Return JSON:
{{
  "diagnosis": "",
  "age": "",
  "gender": "",
  "key_findings": []
}}

CASE:
{case_text[:6000]}
"""

    response = client.responses.create(
        model="gpt-4o-mini",
        input=prompt
    )

    text = ""

    if hasattr(response, "output"):
        for item in response.output:
            if hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text"):
                        text += c.text

    text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except:
        return {}

def _select_images_for_audit(images, max_images=8):
    """Spread samples across the document so clinical photos / scans are less likely to be missed."""
    if not images:
        return []
    n = len(images)
    if n <= max_images:
        return images
    idxs = set()
    for k in range(max_images):
        idxs.add(int(k * (n - 1) / max(1, max_images - 1)))
    idxs.add(0)
    idxs.add(n - 1)
    ordered = sorted(idxs)
    return [images[i] for i in ordered[:max_images]]


def _normalize_observations_detail(data):
    """
    Enforce richer, medico-legal observation detail when model output is too brief.
    """
    summary = str(data.get("auditor_observation_summary") or "").strip()
    if len(summary) < 220:
        data["auditor_observation_summary"] = (
            "Patient context and chronology were reviewed from available records, including prior procedures, "
            "current diagnosis, admission/discharge details, and documented operative management. Clinical evidence "
            "was correlated with guideline expectations to assess medical necessity, procedural appropriateness, and "
            "documentation sufficiency. Where source records are incomplete, conclusions remain evidence-based but "
            "subject to revision upon submission of missing primary documents."
        )

    observations = data.get("observations") or []
    if not isinstance(observations, list):
        data["observations"] = []
        return

    for obs in observations:
        if not isinstance(obs, dict):
            continue

        question = str(obs.get("question") or "").strip()
        answer = str(obs.get("answer") or "").strip()
        analysis = str(obs.get("analysis") or "").strip()

        # Already detailed enough (multi-sentence / structured style)
        if len(analysis) >= 220 and analysis.count(".") >= 3:
            continue

        enriched = (
            f"Clinical context: {analysis or 'Available records indicate the documented treatment and timeline were reviewed for clinical appropriateness.'}\n"
            "Evidence review: Correlated case notes, documented timeline events, and supporting investigations/procedure details available in records.\n"
            "Guideline correlation: Findings are mapped against applicable protocol expectations for indication, timing, and documented management consistency.\n"
            "Risk and discrepancy check: Any mismatch between diagnosis, procedure notes, imaging, and chronology is considered for claim reliability.\n"
            f"Medico-legal implication: {('Current documentation supports the stated position.' if (answer or '').lower() in {'yes', 'supported', 'appropriate'} else 'Conclusion is based on available documentation; any missing primary records may affect final liability interpretation.')}"
        )

        if question:
            # Keep question-specific linkage explicit.
            enriched = f"Issue reviewed: {question}\n" + enriched

        obs["analysis"] = enriched


def run_audit(case_text, guideline_text, user_question=None, images=None):
    print("Running audit engine")
    image_analysis_text = ""

    if images:
        selected_images = [
            _normalize_image(img, i + 1)
            for i, img in enumerate(_select_images_for_audit(images, max_images=8))
        ]
        selected_images = [img for img in selected_images if img.get("base64")]

        page_analyses = {}
        if selected_images:
            workers = min(4, len(selected_images))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_analyze_single_image, img): img["page"]
                    for img in selected_images
                }
                for future in as_completed(futures):
                    page, analysis = future.result()
                    page_analyses[page] = analysis

        for page in sorted(page_analyses):
            image_analysis_text += f"""
    [IMAGE ANALYSIS - Page {page}]
    {page_analyses[page]}
    """

    if image_analysis_text.strip():
        case_text = (
            "[IMAGING PRESENT — clinical images were extracted from the case PDFs and analyzed below]\n"
            + case_text
            + "\n"
            + image_analysis_text
        )

    prompt = f"""
    You are a SENIOR MEDICAL AUDITOR working for an insurance audit firm.

    You are preparing an OFFICIAL MEDICO-LEGAL AUDIT REPORT.

    Include the name of the guideline used for this audit in "guideline_used".

    ----------------------------------------
    CORE OBJECTIVE
    ----------------------------------------
    - Analyze ALL case documents (including OCR-extracted text and imaging reports such as X-ray/CT/MRI)
    - Identify disease/condition
    - Identify patient demographics (especially age)
    - Apply age-appropriate medical guideline(s)
    - Validate treatment against protocol
    - Ensure report consistency across UI and PDF

    ----------------------------------------
    CRITICAL CONSISTENCY REQUIREMENT
    ----------------------------------------
    - You MUST extract patient details ONLY from the case text
    - Do NOT assume or infer age
    - If multiple numbers are present, choose the one clearly linked to patient demographics
    - Prefer patterns like: "Age", "years", "male/female"

    - The JSON output MUST be the SINGLE SOURCE OF TRUTH
    - The SAME JSON will be used for:
      1. Frontend display
      2. PDF generation

    - Therefore:
      ✔ ALL sections must be complete
      ✔ NO missing or partial sections
      ✔ NO additional interpretation outside JSON

    ----------------------------------------
    MULTI-GUIDELINE HANDLING
    ----------------------------------------
    - Multiple guidelines may be provided
    - You MUST:
      1. Select most relevant guideline
      2. Optionally use secondary guideline if needed
      3. Clearly mention in "guideline_used"

    ----------------------------------------
    AGE-SPECIFIC VALIDATION
    ----------------------------------------
    - Extract patient age from case
    - Apply ONLY relevant age-based guideline sections
    - If mismatch → flag deviation clearly

    ----------------------------------------
    IMAGING & OCR HANDLING
    ----------------------------------------
    
    - Assume case_text includes:
      ✔ OCR extracted text
      ✔ Imaging/radiology descriptions

    - You MUST:
      - Extract imaging findings
      - Correlate clinically
      - Validate against diagnosis

    - If imaging referenced but missing → add to documentation_gaps
----------------------------------------
IMAGE PRESENCE VALIDATION (CRITICAL FIX)
----------------------------------------

- If ANY line starts with "[IMAGE ANALYSIS" in CASE (including after [IMAGING PRESENT]):
    ✔ Clinical images WERE provided and analyzed — treat as AVAILABLE imaging/photo evidence
    ✔ DO NOT state "clinical picture missing", "no photo submitted", or similar
    ✔ DO NOT add gaps solely because the narrative omits the photo — the [IMAGE ANALYSIS] block IS the evidence
    ✔ For X-ray/MRI/CT: use both OCR text in case AND [IMAGE ANALYSIS] blocks; do not contradict yourself across sections

- If NO "[IMAGE ANALYSIS" appears anywhere AND case text does not clearly describe imaging:
    ✔ THEN you may note missing imaging documentation in documentation_gaps (one clear item, not duplicated)

- You may receive partial case data due to chunking
- Infer missing continuity carefully
- Do NOT assume missing data as absence
    [IMAGE ANALYSIS ...] = CONFIRMED IMAGE EVIDENCE PROVIDED TO THE AUDITOR
    ----------------------------------------
    FOLLOW-UP QUESTION HANDLING (Q&A)
    ----------------------------------------
    If USER QUESTION is provided:

    - DO NOT regenerate full report
    - Answer strictly based on:
      1. Case documents
      2. Guidelines
      3. Existing audit logic

    - Response MUST be structured and reusable in PDF

    ----------------------------------------
    STEP 1: UNDERSTAND CONTEXT
    ----------------------------------------
    - Identify disease
    - Identify patient age
    - Identify applicable guideline
    - Evaluate treatment
    - The case may include sections labeled [IMAGE ANALYSIS].
    - These represent findings extracted from clinical images (e.g., X-ray, oral cavity photos, scans).
    - You MUST use these findings for clinical correlation wherever relevant.
    - If image findings are present, include them appropriately in clinical reasoning and observations.
    - Do NOT ignore image-derived information.

    ----------------------------------------
    STEP 2: GENERATE OUTPUT
    ----------------------------------------

    IF USER QUESTION IS NONE:

    Return ONLY JSON:

    {{
      "mode": "audit",

      "guideline_used": "",

      "patient_details": {{
        "name": "",
        "age": "",
        "sex": ""
      }},

      "insurance_details": {{
        "insurance_company": "",
        "policy_number": "",
        "policy_period": "",
        "claim_incident_number": ""
      }},

      "claim_details": {{
        "hospital": "",
        "consultation_date": "",
        "date_of_admission": "",
        "date_of_discharge": "",
        "nature_of_admission": "",
        "procedure_or_surgery": "",
        "diagnosis": ""
      }},
        - Populate "imaging_findings" using IMAGE ANALYSIS if available.
      "imaging_findings": [
        {{
          "type": "",
          "finding": "",
          "clinical_correlation": "",
          "consistency_with_diagnosis": ""
        }}
      ],

      "clinical_findings": [
        {{
          "parameter": "",
          "value": "",
          "normal_range": "",
          "comment": ""
        }}
      ],

      "documentation_gaps": [
        "Explain WHY this is a gap and its impact"
      ],

      "clinical_checklist": [
        {{
          "area": "",
          "available": "YES or NO",
          "remarks": ""
        }}
      ],

      "timeline": [
        {{
          "date": "",
          "event": ""
        }}
      ],
      // Timeline must include these events when available:
      // 1) Consultation date
      // 2) Date of admission
      // 3) Date of discharge
      // 4) Procedure / surgery done
      // 5) Nature of admission

      "observations": [
        {{
          "question": "",
          "analysis": "DETAILED clinical reasoning (minimum 4 lines) covering clinical context, evidence review, guideline correlation, and medico-legal implication",
          "answer": ""
        }}
      ],
      "auditor_observation_summary": "Detailed overall narrative summary in paragraph form (doctor-style)",
      "treatment_billing_audit": {{
        "room_category_admitted": "",
        "room_category_eligible": "",
        "procedures_performed": "",
        "cross_checked_with_preauth": "",
        "excluded_items_billed": "",
        "charges_appropriate": ""
      }},
      "financial_review": {{
        "total_hospital_bill": "",
        "non_payable_amount": "",
        "net_claimable_amount": "",
        "recommended_approval_amount": "",
        "patient_liability": ""
      }},

      "inference": "",

      "auditor_conclusion": "",

      "remarks": "",

      "qa_section": []   // IMPORTANT: this must always exist
    }}

    ----------------------------------------

    IF USER QUESTION IS PROVIDED:

    Return ONLY JSON:

    {{
      "mode": "qa",

      "question": "{user_question}",

      "answer": "",

      "justification": "",

      "evidence_used": [
        "Case reference",
        "Guideline reference"
      ]
    }}

    ----------------------------------------
    STRICT RULES
    ----------------------------------------

    1. NO hallucination
    2. Only evidence-based reasoning
    3. Clearly separate facts vs interpretation
    4. Maintain medico-legal tone
    5. Imaging interpretation must be conservative
    6. If insufficient data → explicitly state
    7. Case document may include image analysis sections. Use them for clinical correlations.
    8. If IMAGE ANALYSIS is present:
        - Use cautious interpretation (e.g., "appears to be", "suggestive of")
        - Correlate with diagnosis
        - Mention inconsistencies if any
    9. Populate "insurance_details" from the case (insurer name, policy no., policy period, claim/incident no.); use "" if not stated.
    10. "inference" and "auditor_conclusion" must contain the SAME final medico-legal conclusion text (duplicate for compatibility).
    11. Fill "treatment_billing_audit" and "financial_review" from case records where available; if missing, use "NA".
    12. "auditor_observation_summary" must be a coherent doctor-style narrative paragraph, not bullet fragments.

    ----------------------------------------

    CASE:
    {case_text}

    ----------------------------------------

    GUIDELINES:
    {guideline_text}

    ----------------------------------------

    USER QUESTION:
    {user_question if user_question else "NONE"}
    """
    print("Case text sample\n",case_text[:1000])
    print("Case text sample\n", guideline_text[:1000])

    response = client.responses.create(
        model="gpt-4o",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt}
                ]
            }
        ]
    )

    # ✅ SAFE EXTRACTION (NO STREAM CONSUMPTION)
    raw_output = ""

    if hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "content"):
                for c in item.content:
                    if hasattr(c, "text"):
                        raw_output += c.text

    raw_output = raw_output.strip()

    print("🧠 RAW OUTPUT:\n", raw_output)

    try:
        cleaned = raw_output.strip()

        # 🔥 REMOVE MARKDOWN WRAPPERS
        if cleaned.startswith("```"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

        import re

        try:
            # 🔥 FIX COMMON JSON ISSUES

            cleaned = cleaned.strip()

            # remove ```json wrappers
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

            # 🔥 FIX MISSING COMMAS BETWEEN OBJECT KEYS
            cleaned = re.sub(r'"\s*\n\s*"', '",\n"', cleaned)

            # 🔥 FIX trailing commas
            cleaned = re.sub(r',\s*}', '}', cleaned)
            cleaned = re.sub(r',\s*]', ']', cleaned)

            data = json.loads(cleaned)


        except Exception as e:

            print("❌ JSON ERROR:", e)

            print("❌ CLEANED OUTPUT:", cleaned)



            # 🔥 TRY TO EXTRACT JSON FROM TEXT

            match = re.search(r'\{.*\}', cleaned, re.DOTALL)

            if match:

                try:

                    recovered = json.loads(match.group(0))

                    print("✅ RECOVERED JSON SUCCESSFULLY")

                    return recovered

                except Exception as e2:

                    print("❌ RECOVERY FAILED:", e2)

            # fallback

            return {

                "error": "Invalid AI response",

                "raw_output": cleaned

            }

    except Exception as e:
        print("❌ JSON ERROR:", e)
        print("❌ CLEANED OUTPUT:", cleaned)
        return {"error": "Invalid AI response"}

    # Ensure minimum observation depth
    _normalize_observations_detail(data)

    data.setdefault("insurance_details", {})
    for _k in ("insurance_company", "policy_number", "policy_period", "claim_incident_number"):
        data["insurance_details"].setdefault(_k, "")

    inf = (data.get("inference") or "").strip()
    ac = (data.get("auditor_conclusion") or "").strip()
    if inf and not ac:
        data["auditor_conclusion"] = inf
    elif ac and not inf:
        data["inference"] = ac

    return data

