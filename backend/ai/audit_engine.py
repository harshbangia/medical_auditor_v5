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
    print("🚀 Running audit engine")

    # ✅ LIMIT SIZE (CRITICAL FIX)
    case_text = case_text[:12000] if case_text else ""
    guideline_text = guideline_text[:8000] if guideline_text else ""

    image_analysis_text = ""

    # ---------------- IMAGE ANALYSIS ----------------
    if images:
        selected_images = images[:2] + images[len(images)//2:len(images)//2+2] + images[-2:]

        for img in selected_images:
            try:
                response = client.responses.create(
                    model="gpt-4o",
                    input=[{
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Describe visible clinical findings only."},
                            {"type": "input_image", "image_base64": img["base64"]}
                        ]
                    }]
                )

                image_text = ""
                if response.output:
                    for item in response.output:
                        for c in getattr(item, "content", []):
                            image_text += getattr(c, "text", "")

                image_analysis_text += f"\n[IMAGE PAGE {img['page']}]\n{image_text}"

            except Exception as e:
                image_analysis_text += f"\n[IMAGE ERROR]: {str(e)}\n"

    if image_analysis_text.strip():
        case_text += "\n" + image_analysis_text

    # ---------------- PROMPT ----------------
    prompt = f"""
You are a medical auditor.

Return ONLY valid JSON.

CASE:
{case_text}

GUIDELINE:
{guideline_text}

USER QUESTION:
{user_question if user_question else "NONE"}

OUTPUT FORMAT:

{{
  "mode": "audit",
  "patient_details": {{"name": "", "age": "", "sex": ""}},
  "clinical_findings": [],
  "observations": [],
  "auditor_conclusion": "",
  "remarks": "",
  "qa_section": []
}}
"""

    print("📄 Case length:", len(case_text))
    print("📘 Guideline length:", len(guideline_text))

    # ---------------- OPENAI CALL ----------------
    try:
        response = client.responses.create(
            model="gpt-4o",
            input=[{
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}]
            }],
            response_format={"type": "json_object"}  # 🔥 IMPORTANT FIX
        )
    except Exception as e:
        print("❌ OpenAI error:", e)
        return {"error": str(e)}

    # ---------------- EXTRACT ----------------
    raw_output = ""

    if response.output:
        for item in response.output:
            for c in getattr(item, "content", []):
                raw_output += getattr(c, "text", "")

    raw_output = raw_output.strip()

    print("🧠 RAW OUTPUT:", raw_output[:500])

    # ---------------- EMPTY RESPONSE FIX ----------------
    if not raw_output:
        print("❌ EMPTY AI RESPONSE")

        return {
            "mode": "audit",
            "patient_details": {"name": "N/A", "age": "N/A", "sex": "N/A"},
            "clinical_findings": [],
            "observations": [],
            "auditor_conclusion": "AI failed to generate output",
            "remarks": "Empty response from model",
            "qa_section": []
        }

    # ---------------- PARSE ----------------
    import json

    try:
        data = json.loads(raw_output)
    except Exception as e:
        print("❌ JSON PARSE ERROR:", e)

        return {
            "mode": "audit",
            "patient_details": {"name": "Error", "age": "-", "sex": "-"},
            "clinical_findings": [],
            "observations": [],
            "auditor_conclusion": "Parsing failed",
            "remarks": raw_output[:200],
            "qa_section": []
        }

    print("✅ FINAL KEYS:", data.keys())

    return data
