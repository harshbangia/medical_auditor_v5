from openai import OpenAI
import json
from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

def run_audit(case_text, guideline_text):
    prompt = f"""
    You are a SENIOR MEDICAL AUDITOR working for an insurance audit firm.

    You are preparing an OFFICIAL MEDICO-LEGAL AUDIT REPORT.

    Include the name of the guideline used for this audit in "guideline_used".

    The report must be:
    - Clinically accurate
    - Legally defensible
    - Written in formal audit language
    - Strict, objective, and evidence-based

    DO NOT sound like AI.
    DO NOT summarize loosely.
    DO NOT guess.

    ----------------------------------------
    STEP 1: UNDERSTAND CONTEXT
    ----------------------------------------
    - Identify disease/condition from case
    - Understand applicable medical guidelines
    - Evaluate treatment vs standard protocol

    ----------------------------------------
    STEP 2: GENERATE REPORT IN STRICT FORMAT
    ----------------------------------------

    Return ONLY JSON:

    {{
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

      "clinical_findings": [
        {{
          "parameter": "",
          "value": "",
          "normal_range": "",
          "comment": "STRICTLY factual (avoid interpretation unless necessary)"
        }}
      ],

      "documentation_gaps": [
        "Explain WHY this is a gap and its impact"
      ],

      "clinical_checklist": [
        {{
          "area": "Blood Pressure / Examination / History / Consultation etc.",
          "available": "YES or NO",
          "remarks": "Brief justification"
        }}
      ],

      "timeline": [
        {{
          "date": "",
          "event": "Use formal audit language like 'Date of Admission (D.O.A)'"
        }}
      ],

      "observations": [
        {{
          "question": "Frame clinically relevant audit question",
          "analysis": "DETAILED reasoning like a senior doctor (2–4 lines minimum)",
          "answer": "Conclusion based on reasoning (not just YES/NO)"
        }}
      ],

      "inference": "Write a professional clinical summary of the case in audit tone",

      "auditor_conclusion": "Clear medico-legal conclusion (e.g. 'Treatment appears to be in accordance with standard treatment protocol')",

      "remarks": "Formal advisory remarks for hospital/insurer"
    }}

    ----------------------------------------
    STRICT RULES
    ----------------------------------------

    1. Observations MUST show deep clinical reasoning:
       - Consider alternative diagnoses
       - Evaluate lab values critically
       - Question assumptions

    2. Do NOT over-interpret:
       - If evidence is insufficient → say so

    3. Tone MUST match:
       - Insurance audit reports
       - Medico-legal documentation

    4. Use formal phrasing like:
       - "appears to be"
       - "is suggestive of"
       - "based on available records"

    5. Checklist MUST be realistic and relevant to case

    ----------------------------------------

    CASE:
    {case_text}

    ----------------------------------------

    GUIDELINE:
    {guideline_text}
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}]
    )

    data = json.loads(response.choices[0].message.content)

    # Ensure minimum observation depth
    for obs in data.get("observations", []):
        if len(obs.get("analysis", "")) < 50:
            obs["analysis"] += " (Further clinical correlation is advised.)"

    return data

