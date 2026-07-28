"""Claim-identity agent — dedicated vision+regex pass for preauth / cashless forms.

Scanned IFFCO-Tokio (and similar) cashless request forms often have ZERO native PDF
text. Generic OCR then misreads:
  - Age 49 → 2  (from Insured ID H7583101-2-0 or partial digit)
  - IFFCO-TOKIO → YOKIO
  - LN Medical College & JK Hospital → Islekar Hospital
  - Policy H7583101 missed entirely

This agent always vision-reads preauth/cashless pages with a field-focused prompt
and merges the results into insurance_facts / claim_facts before the audit LLM.
"""

from __future__ import annotations

import base64
import json
import os
import re
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import fitz
from PIL import Image

from backend.utils.demographics_normalizer import (
    normalize_age,
    normalize_bill_amount,
    normalize_hospital_name,
    normalize_patient_name,
    normalize_policy_number,
)
from backend.utils.insurance_extractor import (
    _is_valid_claim_incident,
    _is_valid_policy_number,
    canonicalize_insurer_name,
    find_insurer_in_text,
)

_PREAUTH_NAME_RE = re.compile(
    r"pre[\s_-]?auth|cashless|request\s+for\s+cashless|authorization\s+form|"
    r"hospitalization\s+for\s+medical\s+insurance",
    re.I,
)

_IDENTITY_PROMPT = """You are extracting CLAIM IDENTITY fields from one page of an Indian
health-insurance cashless / pre-authorization form (often handwritten in blue ink).

Read carefully. Printed letterhead beats handwriting when both are present.

Return ONLY JSON (no markdown):
{
  "insurance_company": "",
  "policy_number": "",
  "insured_id": "",
  "claim_incident_number": "",
  "patient_name": "",
  "age_years": "",
  "sex": "",
  "hospital": "",
  "bill_amount": "",
  "admission_date": "",
  "provisional_diagnosis": "",
  "procedure": ""
}

Rules (critical):
- insurance_company: from PRINTED logo/header (e.g. "IFFCO-TOKIO GENERAL INSURANCE
  COMPANY LIMITED"). Never invent. Never output OCR garbage like "YOKIO".
- policy_number: labeled Policy Number / Policy No only (e.g. H7583101).
  Do NOT use UHID / IPD / hospital IDs.
- insured_id: labeled Insured ID Number (may look like H7583101-2-0). This is NOT age.
- claim_incident_number: only if explicitly labeled Claim Incident / Claim No with a
  long numeric ID. Leave "" if absent (common on fresh cashless requests).
- age_years: ONLY the Age / years box value (1–120). Never take the middle digit of
  Insured ID (the "-2-" in H7583101-2-0 is NOT age).
- hospital: Name of the Hospital field OR rubber stamp (e.g. "L.N. Medical College &
  J.K. Hospital"). Never "Certified Hospital" / ISO / NABH alone.
- bill_amount: Sum Total Expected Cost / Grand Total only (include Rs.).
- If a field is unreadable, use "".
"""


def _page_b64(pdf_path: str, page_num: int, dpi: int = 220) -> str:
    try:
        doc = fitz.open(pdf_path)
        try:
            if page_num < 1 or page_num > len(doc):
                return ""
            zoom = dpi / 72.0
            pix = doc[page_num - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        finally:
            doc.close()
    except Exception as exc:
        print(f"⚠️ claim_identity render failed {pdf_path} p{page_num}: {exc}")
        return ""


def _native_chars(pdf_path: str) -> int:
    try:
        doc = fitz.open(pdf_path)
        try:
            return sum(len((p.get_text() or "").strip()) for p in doc)
        finally:
            doc.close()
    except Exception:
        return 0


def _is_preauth_candidate(filename: str, case_text: str = "") -> bool:
    name = filename or ""
    if _PREAUTH_NAME_RE.search(name):
        return True
    # Content hint from already-OCR'd case text for this file
    if filename and case_text:
        window = ""
        for marker in (f"({filename})", filename):
            if marker in case_text:
                i = case_text.find(marker)
                window = case_text[max(0, i - 100): i + 4000]
                break
        if _PREAUTH_NAME_RE.search(window) or re.search(
            r"request\s+for\s+cashless|insured\s+id\s+number|sum\s*total\s*expected",
            window,
            re.I,
        ):
            return True
    return False


def _parse_json(raw: str) -> dict:
    cleaned = (raw or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _vision_extract_page(image_b64: str) -> dict:
    if not image_b64:
        return {}
    try:
        from backend.ai.llm_helpers import extract_response_text, image_input_part
        from backend.llm_client import get_openai_client
    except Exception as exc:
        print(f"⚠️ claim_identity vision unavailable: {exc}")
        return {}

    content = [
        {"type": "input_text", "text": _IDENTITY_PROMPT},
        image_input_part(image_b64, detail="high"),
    ]
    try:
        client = get_openai_client()
        model = os.getenv("VISION_OCR_MODEL", "gpt-4o")
        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": content}],
            text={"format": {"type": "json_object"}},
        )
        return _parse_json(extract_response_text(response) or "")
    except Exception as exc:
        print(f"⚠️ claim_identity vision call failed: {exc}")
        return {}


def _normalize_identity(raw: dict) -> Dict[str, str]:
    out: Dict[str, str] = {
        "insurance_company": "",
        "policy_number": "",
        "insured_id": "",
        "claim_incident_number": "",
        "patient_name": "",
        "age": "",
        "sex": "",
        "hospital": "",
        "bill_amount": "",
        "admission_date": "",
        "diagnosis": "",
        "procedure": "",
    }
    if not raw:
        return out

    company = canonicalize_insurer_name(str(raw.get("insurance_company") or ""))
    if not company:
        company = canonicalize_insurer_name(find_insurer_in_text(str(raw.get("insurance_company") or "")))
    out["insurance_company"] = company

    policy = normalize_policy_number(raw.get("policy_number") or "")
    if not policy:
        # Insured ID base H7583101-2-0 → H7583101
        insured = str(raw.get("insured_id") or "").strip()
        m = re.match(r"^(H\d{5,8})", insured, re.I)
        if m and _is_valid_policy_number(m.group(1)):
            policy = m.group(1).upper()
    elif not _is_valid_policy_number(policy):
        policy = ""
    out["policy_number"] = policy

    insured = str(raw.get("insured_id") or "").strip()
    if re.match(r"^H\d{5,8}(?:-\d+)*$", insured, re.I):
        out["insured_id"] = insured.upper()

    claim = str(raw.get("claim_incident_number") or "").strip()
    claim = re.sub(r"\s+", "", claim)
    if _is_valid_claim_incident(claim):
        out["claim_incident_number"] = claim

    out["patient_name"] = normalize_patient_name(raw.get("patient_name") or "")
    out["age"] = normalize_age(raw.get("age_years") or raw.get("age") or "")
    sex = str(raw.get("sex") or "").strip().lower()
    if sex in {"m", "male"}:
        out["sex"] = "Male"
    elif sex in {"f", "female"}:
        out["sex"] = "Female"

    hosp = normalize_hospital_name(raw.get("hospital") or "")
    if not hosp:
        # Soft accept LN / JK hospital OCR variants
        raw_h = re.sub(r"\s+", " ", str(raw.get("hospital") or "").strip())
        if re.search(r"(?:l\.?\s*n\.?|ln).{0,20}(?:medical|college).{0,20}(?:j\.?\s*k\.?|jk).{0,10}hospital", raw_h, re.I):
            hosp = "L.N. Medical College & J.K. Hospital"
        elif re.search(r"j\.?\s*k\.?\s*hospital", raw_h, re.I) and re.search(r"medical\s+college|l\.?\s*n", raw_h, re.I):
            hosp = "L.N. Medical College & J.K. Hospital"
    out["hospital"] = hosp

    out["bill_amount"] = normalize_bill_amount(raw.get("bill_amount") or "")
    out["admission_date"] = str(raw.get("admission_date") or "").strip()
    out["diagnosis"] = str(raw.get("provisional_diagnosis") or raw.get("diagnosis") or "").strip()
    out["procedure"] = str(raw.get("procedure") or "").strip()
    return out


def _merge_identity(base: Dict[str, str], new: Dict[str, str]) -> Dict[str, str]:
    out = dict(base)
    for k, v in (new or {}).items():
        if not v:
            continue
        if not out.get(k):
            out[k] = v
            continue
        # Prefer longer/cleaner company & hospital names
        if k in {"insurance_company", "hospital", "patient_name"} and len(v) > len(out[k]):
            out[k] = v
        elif k == "age":
            # Prefer plausible adult ages over tiny OCR crumbs (e.g. 2 from Insured ID)
            try:
                cur_i, new_i = int(out[k]), int(v)
            except ValueError:
                continue
            if cur_i < 12 <= new_i <= 120:
                out[k] = v
            elif 12 <= new_i <= 120 and abs(new_i - cur_i) > 0 and new_i > cur_i:
                # Prefer HIS/typed-looking ages when both valid — keep existing if already adult
                pass
    return out


def extract_claim_identity(
    pdf_paths: Optional[List[Tuple[str, str]]] = None,
    case_text: str = "",
    max_pages_per_doc: int = 2,
) -> Dict[str, str]:
    """Run dedicated identity extraction on preauth/cashless (and scan-only) PDFs."""
    merged: Dict[str, str] = {
        "insurance_company": "",
        "policy_number": "",
        "insured_id": "",
        "claim_incident_number": "",
        "patient_name": "",
        "age": "",
        "sex": "",
        "hospital": "",
        "bill_amount": "",
        "admission_date": "",
        "diagnosis": "",
        "procedure": "",
    }
    if not pdf_paths:
        # Still try regex on combined case text for company/policy
        from backend.utils.insurance_extractor import extract_insurance_from_text
        text_facts = extract_insurance_from_text(case_text or "", source="combined")
        merged["insurance_company"] = canonicalize_insurer_name(text_facts.get("insurance_company") or "")
        merged["policy_number"] = text_facts.get("policy_number") or ""
        merged["claim_incident_number"] = text_facts.get("claim_incident_number") or ""
        return merged

    candidates: List[Tuple[str, str, int]] = []
    for path, fname in pdf_paths:
        native = _native_chars(path)
        is_preauth = _is_preauth_candidate(fname, case_text)
        # Always vision-read preauth; also vision-read near-empty PDFs that look insurance-related
        if is_preauth or native < 80:
            priority = 100 if is_preauth else 40
            candidates.append((path, fname, priority))

    candidates.sort(key=lambda x: -x[2])
    # Cap to avoid cost blow-ups
    candidates = candidates[:4]

    for path, fname, _pri in candidates:
        try:
            doc = fitz.open(path)
            n_pages = len(doc)
            doc.close()
        except Exception:
            n_pages = 1
        for page_num in range(1, min(n_pages, max_pages_per_doc) + 1):
            b64 = _page_b64(path, page_num)
            raw = _vision_extract_page(b64)
            norm = _normalize_identity(raw)
            if any(norm.values()):
                print(
                    f"✅ Claim identity ({fname} p{page_num}): "
                    f"company={norm.get('insurance_company')!r} "
                    f"policy={norm.get('policy_number')!r} "
                    f"age={norm.get('age')!r} hospital={norm.get('hospital')!r}"
                )
            merged = _merge_identity(merged, norm)

    # Regex backfill from case_text (vision OCR transcription of preauth)
    from backend.utils.insurance_extractor import extract_insurance_from_text
    text_facts = extract_insurance_from_text(case_text or "", source="combined")
    if not merged.get("insurance_company"):
        merged["insurance_company"] = canonicalize_insurer_name(text_facts.get("insurance_company") or "")
    else:
        merged["insurance_company"] = canonicalize_insurer_name(merged["insurance_company"])
    if not merged.get("policy_number") and text_facts.get("policy_number"):
        merged["policy_number"] = text_facts["policy_number"]
    if not merged.get("claim_incident_number") and text_facts.get("claim_incident_number"):
        merged["claim_incident_number"] = text_facts["claim_incident_number"]

    return merged


def apply_claim_identity_to_facts(
    identity: Dict[str, str],
    insurance_facts: Optional[dict] = None,
    claim_facts: Optional[dict] = None,
) -> Tuple[dict, dict]:
    """Force-merge identity into insurance_facts and claim_facts (identity wins)."""
    ins = dict(insurance_facts or {})
    claim = dict(claim_facts or {})
    identity = identity or {}

    if identity.get("insurance_company"):
        ins["insurance_company"] = identity["insurance_company"]
    if identity.get("policy_number"):
        ins["policy_number"] = identity["policy_number"]
    if identity.get("claim_incident_number"):
        ins["claim_incident_number"] = identity["claim_incident_number"]
    if identity.get("insured_id"):
        ins["member_code"] = identity["insured_id"]
        # If no claim incident yet, surface insured ID as secondary reference
        if not ins.get("claim_incident_number"):
            # Keep claim empty — don't put member code in claim_incident (invalid format)
            pass

    if identity.get("hospital"):
        claim["hospital"] = identity["hospital"]
    if identity.get("bill_amount"):
        claim["total_hospital_bill"] = identity["bill_amount"]
    if identity.get("admission_date") and not claim.get("date_of_admission"):
        claim["date_of_admission"] = identity["admission_date"]
    if identity.get("diagnosis") and not claim.get("diagnosis"):
        claim["diagnosis"] = identity["diagnosis"]
    if identity.get("procedure") and not claim.get("procedure_or_surgery"):
        claim["procedure_or_surgery"] = identity["procedure"]

    # Stash demographics for enricher
    for k in ("patient_name", "age", "sex"):
        if identity.get(k):
            claim[f"_identity_{k}"] = identity[k]

    return ins, claim
