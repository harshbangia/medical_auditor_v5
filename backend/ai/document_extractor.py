"""Per-document map step — structured fact extraction before the main audit LLM."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

import backend.config  # noqa: F401
from backend.ai.case_profiler import normalize_case_profile, normalize_str_list, stringify_item
from backend.ai.llm_helpers import extract_response_text
from backend.llm_client import get_openai_client
from backend.utils.demographics_normalizer import (
    extract_typed_demographics,
    sanitize_mapped_facts,
)

ProgressFn = Callable[[str, int, str], None]
_MAX_DOC_CHARS = 12000
_MAP_MODEL = "gpt-4o-mini"


def _parse_json(text: str) -> dict:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _normalize_doc_facts(raw: dict, filename: str) -> dict:
    facts = dict(raw or {})
    facts["source_file"] = str(facts.get("source_file") or filename).strip()
    for key in (
        "patient_name", "age", "sex", "hospital", "diagnosis",
        "admission_date", "discharge_date", "consultation_date",
        "nature_of_admission", "chief_complaint", "bill_amount",
        "policy_number", "claim_number", "document_type", "summary",
    ):
        val = facts.get(key)
        facts[key] = stringify_item(val) if val is not None else ""
    for key in (
        "procedures", "medications", "key_labs", "imaging_findings",
        "documentation_gaps", "notable_findings",
    ):
        facts[key] = normalize_str_list(facts.get(key))
    return facts


def extract_document_facts(filename: str, text: str) -> dict:
    """Map one uploaded document to structured facts (single small LLM call)."""
    excerpt = (text or "").strip()[:_MAX_DOC_CHARS]
    if len(excerpt) < 40:
        return _normalize_doc_facts(
            {"source_file": filename, "summary": "No readable text extracted"},
            filename,
        )

    typed = extract_typed_demographics(excerpt)
    typed_hint = ""
    if any(typed.values()):
        typed_hint = (
            "\nTYPED HIS / LETTERHEAD HINTS (prefer these over unclear handwriting):\n"
            f"- patient_name: {typed.get('patient_name') or '—'}\n"
            f"- age: {typed.get('age') or '—'}\n"
            f"- sex: {typed.get('sex') or '—'}\n"
            f"- hospital: {typed.get('hospital') or '—'}\n"
            f"- uhid (NOT policy): {typed.get('uhid') or '—'}\n"
        )

    prompt = f"""You are extracting facts from ONE medical insurance case document for audit.
Document filename: {filename}
{typed_hint}
Return ONLY JSON:
{{
  "source_file": "{filename}",
  "document_type": "discharge_summary|bill|preauth|query_letter|lab|radiology|indoor|prescription|clinical|other",
  "patient_name": "",
  "age": "",
  "sex": "",
  "hospital": "",
  "diagnosis": "",
  "procedures": [],
  "medications": [],
  "admission_date": "",
  "discharge_date": "",
  "consultation_date": "",
  "nature_of_admission": "",
  "chief_complaint": "",
  "key_labs": [],
  "imaging_findings": [],
  "bill_amount": "",
  "policy_number": "",
  "claim_number": "",
  "documentation_gaps": [],
  "notable_findings": [],
  "summary": ""
}}

Rules:
- Extract ONLY what THIS document explicitly states. Use "" or [] if not in this file.
- Do NOT infer from other documents. Do NOT guess patient name or diagnosis.
- age: years only as a number 1–120. From "49 Y 0 M 0 D" or "49Y/M" use "49". Never invent ages like 149.
- patient_name: prefer typed Patient Name / UHID banner. Do not split names ("GaGa DEEP").
- hospital: full facility name from letterhead. Never "Certified Hospital" / ISO / NABH alone.
- policy_number: labeled Policy No / Insured ID only. UHID/IPD/LMH… is NOT a policy number.
- procedures: CURRENT admission procedures only. H/O / Past History surgeries (e.g. H/O TURP) go in notable_findings, NOT procedures.
- bill_amount: labeled grand total / sum total expected cost only (include Rs.). Ignore bare "20" from drug strengths.
- summary: 2-3 factual sentences about what this specific document contains.
- notable_findings: audit-relevant facts stated in this file (with values if labs/imaging).
- medications: brand/generic names as written in this document only.

DOCUMENT TEXT:
{excerpt}
"""

    try:
        client = get_openai_client()
        response = client.responses.create(
            model=_MAP_MODEL,
            input=prompt,
            text={"format": {"type": "json_object"}},
        )
        raw = extract_response_text(response)
        if not raw and hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for c in item.content:
                        if hasattr(c, "text"):
                            raw += c.text
    except Exception as exc:
        base = {
            "source_file": filename,
            "summary": f"Extraction failed: {exc}",
            "documentation_gaps": ["Automated per-document extraction failed"],
            "patient_name": typed.get("patient_name") or "",
            "age": typed.get("age") or "",
            "sex": typed.get("sex") or "",
            "hospital": typed.get("hospital") or "",
        }
        return sanitize_mapped_facts(_normalize_doc_facts(base, filename), excerpt)

    facts = _normalize_doc_facts(_parse_json(raw), filename)
    return sanitize_mapped_facts(facts, excerpt)


def map_case_documents(
    documents: List[Tuple[str, str]],
    progress: Optional[ProgressFn] = None,
    max_workers: int = 3,
) -> List[dict]:
    """Run map step over each (filename, text) pair in parallel."""
    if not documents:
        return []

    unique: List[Tuple[str, str]] = []
    seen = set()
    for name, text in documents:
        if name not in seen:
            seen.add(name)
            unique.append((name, text))

    total = len(unique)
    results: List[Optional[dict]] = [None] * total
    workers = max(1, min(max_workers, total))

    def _report(done: int, msg: str) -> None:
        if progress:
            pct = 59 + int(6 * done / max(total, 1))
            progress("map", pct, msg)

    _report(0, f"Mapping facts from {total} document(s)…")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(extract_document_facts, name, text): idx
            for idx, (name, text) in enumerate(unique)
        }
        done = 0
        for fut in as_completed(future_map):
            idx = future_map[fut]
            name = unique[idx][0]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = _normalize_doc_facts(
                    {"source_file": name, "summary": f"Error: {exc}"},
                    name,
                )
            done += 1
            _report(done, f"Mapped {done}/{total}: {name}")

    return [r for r in results if r]


def ledger_to_case_profile(ledger: dict) -> dict:
    """Convert merged ledger into case_profile shape for RAG / audit."""
    merged = (ledger or {}).get("merged") or {}
    profile = {
        "diagnosis": merged.get("diagnosis") or "",
        "age": merged.get("age") or "",
        "gender": merged.get("sex") or "",
        "procedures": normalize_str_list(merged.get("procedures")),
        "admission_type": merged.get("nature_of_admission") or "",
        "chief_complaint": merged.get("chief_complaint") or "",
        "key_labs": normalize_str_list(merged.get("key_labs")),
        "imaging_mentioned": normalize_str_list(merged.get("imaging_findings")),
        "timeline_events": [],
        "billing_flags": normalize_str_list(merged.get("billing_flags")),
        "documentation_weaknesses": normalize_str_list(merged.get("documentation_gaps")),
    }
    for doc in (ledger or {}).get("documents") or []:
        if not isinstance(doc, dict):
            continue
        for ev in doc.get("notable_findings") or []:
            s = stringify_item(ev)
            if s and s not in profile["documentation_weaknesses"]:
                profile["documentation_weaknesses"].append(s)
    if merged.get("admission_date"):
        profile["timeline_events"].append({
            "date": merged["admission_date"],
            "event": "Date of admission",
        })
    if merged.get("discharge_date"):
        profile["timeline_events"].append({
            "date": merged["discharge_date"],
            "event": "Date of discharge",
        })
    return normalize_case_profile(profile)
