"""NotebookLM-style deep narrative Q&A after identity/finance are sealed.

Runs a second Gemini pass that expands auditor observations into evidence-rich
answers (timelines, guideline thresholds, policy clauses) without inventing
facts not present in the sealed case corpus.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from backend.llm_client import get_llm_provider, model_for


_DEEP_NARRATIVE_ENABLED = os.getenv("DEEP_NARRATIVE_ENABLED", "1") not in (
    "0",
    "false",
    "False",
    "",
)


def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        return {}
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


def _sealed_facts_block(result: dict) -> str:
    patient = result.get("patient_details") or {}
    claim = result.get("claim_details") or {}
    ins = result.get("insurance_details") or {}
    fin = result.get("financial_review") or {}
    lines = [
        f"Patient: {patient.get('name') or 'NA'}",
        f"Age / Sex: {patient.get('age') or 'NA'} / {patient.get('sex') or 'NA'}",
        f"DOB: {patient.get('date_of_birth') or 'NA'}",
        f"Hospital: {claim.get('hospital') or 'NA'}",
        f"DOA: {claim.get('date_of_admission') or 'NA'}",
        f"DOD: {claim.get('date_of_discharge') or 'NA'}",
        f"Diagnosis: {claim.get('diagnosis') or 'NA'}",
        f"Procedure: {claim.get('procedure') or claim.get('procedure_surgery') or 'NA'}",
        f"Nature of admission: {claim.get('nature_of_admission') or 'NA'}",
        f"Insurer: {ins.get('insurance_company') or 'NA'}",
        f"Policy: {ins.get('policy_number') or 'NA'}",
        f"Claim incident: {ins.get('claim_incident_number') or 'NA'}",
        f"Total hospital bill: {fin.get('total_hospital_bill') or claim.get('total_hospital_bill') or 'NA'}",
    ]
    return "\n".join(lines)


def _build_prompt(
    *,
    sealed_facts: str,
    corpus: str,
    guideline_excerpt: str,
    existing_questions: List[str],
) -> str:
    existing = "\n".join(f"- {q}" for q in existing_questions[:8]) or "- (none)"
    return f"""You are a SENIOR INSURANCE MEDICAL AUDITOR writing NotebookLM-depth Expert Opinion Q&A.

SEALED FACTS (authoritative — do not contradict these headers):
{sealed_facts}

EXISTING QUESTIONS ALREADY ASKED (expand/replace with deeper answers; you may add new ones):
{existing}

CASE CORPUS (OCR / vision transcription — ground every fact here; cite source filenames):
{corpus[:50000]}

GUIDELINE / POLICY EXCERPTS (cite clause numbers or guideline thresholds when present):
{guideline_excerpt[:12000] or "(none provided)"}

Write 6–8 deep observations as JSON only:
{{
  "observations": [
    {{
      "question": "Specific clinical, policy, or forensic-billing question",
      "answer": "Supported|Partially Supported|Not Supported|Insufficient Evidence",
      "analysis": "Multi-paragraph (≥180 words when evidence exists): timeline, guideline thresholds, policy clauses, radiological/lab anchors, line-item bill vs notes, rupee amounts, quotes. Name source documents. Do NOT invent facts."
    }}
  ],
  "billing_disallowances": [
    {{
      "title": "Short disallowance name",
      "amount": "Rs. 0",
      "reason": "Billed vs records",
      "evidence": "Quote / filename / date",
      "audit_action": "Disallow / proportionate deduct / query"
    }}
  ],
  "documentation_gaps": [
    {{
      "title": "Gap name",
      "finding": "What is missing or misstated",
      "evidence": "Source + quote",
      "audit_action": "Query / withhold"
    }}
  ],
  "auditor_observation_summary": "Direct narrative of what hospital did vs what guideline/policy requires",
  "conclusion": "1–3 sentence clinical/policy conclusion aligned with sealed facts"
}}

Rules:
- Prefer NotebookLM forensic depth: bill line-item anomalies (role miscodes, unrendered equipment fees), billed-but-missing lab reports, missing progress-note date ranges, discharge omitting OT pathology — ONLY when supported by corpus.
- At least 2 observations must be forensic billing or documentation-gap questions when corpus supports them.
- Every analysis must reference at least one source filename from the corpus markers.
- If evidence is missing, say Insufficient Evidence and list what is missing.
- Do not invent policy clause numbers that are not in the excerpts.
- Do not change sealed patient name, age, claim number, policy, or bill amounts.
"""


def deepen_observations(
    result: dict,
    *,
    corpus_text: str,
    guideline_text: str = "",
) -> dict:
    """Replace/expand observations with a deep narrative LLM pass. No-op on failure."""
    if not _DEEP_NARRATIVE_ENABLED:
        return result
    if not result or result.get("error"):
        return result
    corpus = (corpus_text or "").strip()
    if len(corpus) < 200:
        return result

    existing = result.get("observations") or []
    questions = [
        str(o.get("question") or "").strip()
        for o in existing
        if isinstance(o, dict) and str(o.get("question") or "").strip()
    ]

    prompt = _build_prompt(
        sealed_facts=_sealed_facts_block(result),
        corpus=corpus,
        guideline_excerpt=guideline_text or "",
        existing_questions=questions,
    )

    try:
        raw = get_llm_provider().complete(
            model=model_for("audit"),
            text_parts=[prompt],
            json_mode=True,
        )
    except Exception as exc:
        print(f"⚠️ Deep narrative pass failed: {exc}", flush=True)
        return result

    data = _parse_json(raw)
    new_obs = data.get("observations") if isinstance(data, dict) else None
    if not isinstance(new_obs, list) or not new_obs:
        return result

    cleaned: List[Dict[str, Any]] = []
    for o in new_obs:
        if not isinstance(o, dict):
            continue
        q = str(o.get("question") or "").strip()
        analysis = str(o.get("analysis") or o.get("answer_detail") or "").strip()
        ans = str(o.get("answer") or "").strip()
        if not q or not analysis:
            continue
        if len(analysis) < 80:
            continue
        cleaned.append({
            "question": q,
            "answer": ans or "Insufficient Evidence",
            "analysis": analysis,
        })
        if len(cleaned) >= 8:
            break

    if len(cleaned) < 2:
        return result

    result["observations"] = cleaned
    summary = str(data.get("auditor_observation_summary") or "").strip()
    if summary:
        result["auditor_observation_summary"] = summary
    conclusion = str(data.get("conclusion") or "").strip()
    if conclusion:
        if len(str(result.get("auditor_conclusion") or "")) < 80:
            result["auditor_conclusion"] = conclusion
        if len(str(result.get("inference") or "")) < 80:
            result["inference"] = conclusion

    for key in ("billing_disallowances", "documentation_gaps"):
        extra = data.get(key)
        if isinstance(extra, list) and extra:
            existing_extra = result.get(key)
            if not isinstance(existing_extra, list) or not existing_extra:
                result[key] = [x for x in extra if isinstance(x, dict)]

    result["deep_narrative"] = {"applied": True, "count": len(cleaned)}
    print(f"✅ Deep narrative: {len(cleaned)} observation(s)", flush=True)
    return result
