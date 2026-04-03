from openai import OpenAI
import json
from dotenv import load_dotenv
import os
import fitz
import base64

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

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

def run_audit(case_text, guideline_text, user_question=None, images=None):
    print("Running audit engine")
    image_analysis_text = ""

    if images:
        for img in images[:3]:  # limit to 3 images

            try:
                response = client.responses.create(
                    model="gpt-4o",
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": """You are a clinical medical auditor.

                Analyze this image carefully.

                - Describe only visible findings
                - Do NOT hallucinate
                - Use cautious medical tone
                - If not relevant → say 'No clinical relevance'
                """
                                },
                                {
                                    "type": "input_image",
                                    "image_base64": img["base64"]
                                }
                            ]
                        }
                    ]
                )

                # ✅ IMPORTANT FIX — READ ONCE
                image_analysis = ""

                if hasattr(response, "output") and response.output:
                    for item in response.output:
                        if hasattr(item, "content"):
                            for c in item.content:
                                if hasattr(c, "text"):
                                    image_analysis += c.text

                image_analysis = image_analysis.strip()

                image_analysis_text += f"""
    [IMAGE EVIDENCE FOUND - Page {img['page']}]
    {image_analysis}
    """

            except Exception as e:
                image_analysis_text += f"\n[IMAGE ERROR]: {str(e)}\n"

    if image_analysis_text.strip():
        case_text = "[IMAGING PRESENT]\n" + case_text + "\n" + image_analysis_text

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

- If [IMAGE ANALYSIS] sections are present in CASE:
    ✔ Treat them as AVAILABLE clinical images
    ✔ DO NOT mark "clinical picture missing"
    ✔ DO NOT add image-related items in documentation_gaps

- If NO [IMAGE ANALYSIS] is present:
    ✔ THEN AND ONLY THEN mark images as missing


- You may receive partial case data due to chunking
- Infer missing continuity carefully
- Do NOT assume missing data as absence
    IMAGE ANALYSIS = EVIDENCE OF IMAGE PROVIDED
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
        "sex": "",
        "claim_number": "",
        "policy_number": "",
        "policy_period": ""
      }},

      "claim_details": {{
        "hospital": "",
        "consultation_date": "",
        "date_of_admission": "",
        "date_of_discharge": "",
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

      "observations": [
        {{
          "question": "",
          "analysis": "DETAILED clinical reasoning (2–4 lines minimum)",
          "answer": ""
        }}
      ],

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

        data = json.loads(cleaned)

    except Exception as e:
        print("❌ JSON ERROR:", e)
        print("❌ CLEANED OUTPUT:", cleaned)
        return {"error": "Invalid AI response"}

    # Ensure minimum observation depth
    for obs in data.get("observations", []):
        if len(obs.get("analysis", "")) < 50:
            obs["analysis"] += " (Further clinical correlation is advised.)"

    return data

