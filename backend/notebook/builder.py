"""Build Case Notebook and merge grounded findings into audit result."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.notebook.assessor_parser import merge_assessor_from_chunks
from backend.notebook.contradictions import run_contradiction_checks
from backend.notebook.models import CaseNotebook
from backend.notebook.page_index import chunks_from_case_text, chunks_from_doc_blocks
from backend.notebook.validators import validate_ids_from_corpus
from backend.utils.clinical_fwa_signals import build_case180_style_findings

logger = logging.getLogger("medical_auditor.notebook")


def build_case_notebook(
    *,
    case_text: str,
    doc_blocks: Optional[List[Tuple[str, str]]] = None,
    expected_patient_name: str = "",
    current_claim: str = "",
    current_policy: str = "",
) -> CaseNotebook:
    if doc_blocks:
        chunks = chunks_from_doc_blocks(doc_blocks)
    else:
        chunks = chunks_from_case_text(case_text or "")

    # Document index
    docs: Dict[str, Dict[str, Any]] = {}
    for ch in chunks:
        d = docs.setdefault(
            ch.doc_id,
            {
                "doc_id": ch.doc_id,
                "filename": ch.filename,
                "doc_type": ch.doc_type,
                "pages": set(),
                "chars": 0,
            },
        )
        if ch.page:
            d["pages"].add(ch.page)
        d["chars"] += len(ch.text or "")
    documents = []
    for d in docs.values():
        documents.append({
            "doc_id": d["doc_id"],
            "filename": d["filename"],
            "doc_type": d["doc_type"],
            "page_count": len(d["pages"]) or 1,
            "chars": d["chars"],
        })

    assessor = merge_assessor_from_chunks(chunks)
    corpus = case_text or "\n\n".join(c.text for c in chunks)
    validated = validate_ids_from_corpus(
        corpus,
        assessor=assessor,
        current_claim=current_claim,
        current_policy=current_policy,
    )

    patient = expected_patient_name or assessor.get("patient_name") or ""
    contradictions = run_contradiction_checks(chunks, expected_patient_name=patient)

    # Legacy Case180 pancreatitis signals on full corpus
    clinical = build_case180_style_findings(
        corpus,
        {"claim_details": {"diagnosis": assessor.get("diagnosis") or ""}},
    )

    fwa: List[Dict[str, Any]] = []
    fwa.extend(assessor.get("fwa_alerts") or [])
    fwa.extend(contradictions)
    fwa.extend(clinical)

    finance_hints: Dict[str, Any] = {}
    if assessor.get("claimed_amount"):
        finance_hints["claimed_amount"] = assessor["claimed_amount"]
    if assessor.get("finance_bills"):
        finance_hints["bills"] = assessor["finance_bills"][:30]
        try:
            total = sum(
                float(str(b.get("amount") or "0").replace(",", ""))
                for b in assessor["finance_bills"]
            )
            if total >= 5000:
                finance_hints["sum_of_bill_rows"] = f"{total:,.2f}"
        except ValueError:
            pass

    nb = CaseNotebook(
        chunks=chunks,
        documents=documents,
        assessor=assessor,
        contradictions=contradictions,
        fwa_findings=fwa,
        validated_ids=validated,
        finance_hints=finance_hints,
    )
    logger.info(
        "Case notebook built: docs=%s chunks=%s fwa=%s assessor=%s",
        len(documents),
        len(chunks),
        len(fwa),
        bool(assessor.get("is_assessor")),
    )
    return nb


def _merge_fwa_into_result(result: dict, findings: List[dict]) -> None:
    fa = result.setdefault("fraud_abuse", {})
    existing = fa.get("findings") if isinstance(fa.get("findings"), list) else []
    seen = {
        str(f.get("indicator") or "").lower()
        for f in existing
        if isinstance(f, dict)
    }
    for item in findings:
        if not isinstance(item, dict):
            continue
        ind = str(item.get("indicator") or "").strip()
        if not ind or ind.lower() in seen:
            continue
        seen.add(ind.lower())
        existing.append({
            "category": item.get("category") or "clinical_abuse",
            "indicator": ind,
            "evidence": item.get("evidence") or "",
            "severity": item.get("severity") or "Medium",
            "recommendation": item.get("recommendation") or "",
            "citation": item.get("citation") or {},
        })
    fa["findings"] = existing
    high = sum(1 for f in existing if str(f.get("severity") or "").lower() == "high")
    if high:
        fa["risk_level"] = "High"
        fa["summary"] = (
            f"{len(existing)} FWA indicator(s), including {high} high-severity "
            "(Case Notebook grounded). Do not approve until resolved."
        )
    elif existing:
        fa["risk_level"] = fa.get("risk_level") or "Medium"
        fa["summary"] = fa.get("summary") or (
            f"{len(existing)} FWA indicator(s) from Case Notebook review."
        )
    result["fraud_abuse_findings"] = existing


def _seed_notebook_observations(result: dict, notebook: CaseNotebook) -> None:
    observations = result.get("observations")
    if not isinstance(observations, list):
        observations = []
        result["observations"] = observations
    existing_q = " ".join(
        str(o.get("question") or "") for o in observations if isinstance(o, dict)
    ).lower()

    seeds: List[dict] = []
    for f in notebook.fwa_findings[:8]:
        ind = str(f.get("indicator") or "")
        if not ind or ind.lower() in existing_q:
            continue
        cite = f.get("citation") or {}
        cite_lbl = ""
        if cite.get("filename"):
            cite_lbl = f" Source: {cite.get('filename')}"
            if cite.get("page"):
                cite_lbl += f" p.{cite['page']}"
        seeds.append({
            "question": ind + "?",
            "answer": "Supported" if str(f.get("severity")).lower() == "high" else "Partially Supported",
            "analysis": (
                str(f.get("evidence") or "")
                + (" " + str(f.get("recommendation") or "") if f.get("recommendation") else "")
                + cite_lbl
            ).strip(),
        })
        existing_q += " " + ind.lower()

    # Drop contradictory "missing patient/hospital" ask-noise when we have structured data
    cleaned = []
    has_patient = bool((result.get("patient_details") or {}).get("name"))
    has_hospital = bool((result.get("claim_details") or {}).get("hospital"))
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        blob = " ".join(str(obs.get(k) or "") for k in ("question", "answer", "analysis")).lower()
        if has_patient and has_hospital and re.search(
            r"lack(?:s|ing)?\s+(?:details\s+on\s+)?patient|missing\s+crucial\s+patient|"
            r"no\s+information\s+on\s+the\s+bill\s+amount|"
            r"documents\s+lack\s+specific\s+patient\s+data",
            blob,
        ):
            continue
        cleaned.append(obs)
    result["observations"] = seeds + cleaned


def apply_notebook_to_result(result: dict, notebook: CaseNotebook) -> dict:
    """Merge notebook validated IDs, finance hints, FWA, and observations into audit JSON."""
    if not result or result.get("error"):
        return result

    result["case_notebook"] = {
        "document_count": len(notebook.documents),
        "chunk_count": len(notebook.chunks),
        "documents": notebook.documents,
        "assessor": {
            k: notebook.assessor.get(k)
            for k in (
                "is_assessor", "claim_number", "sub_claim_number", "policy_number",
                "claimed_amount", "claim_type", "hospital", "diagnosis", "source_files",
            )
        },
        "validated_ids": notebook.validated_ids,
        "finance_hints": notebook.finance_hints,
        "contradiction_count": len(notebook.contradictions),
        "fwa_count": len(notebook.fwa_findings),
    }

    ins = result.setdefault("insurance_details", {})
    claim = result.setdefault("claim_details", {})
    fin = result.setdefault("financial_review", {})

    vid = notebook.validated_ids or {}
    if vid.get("claim_incident_number"):
        cur = str(ins.get("claim_incident_number") or "")
        new = vid["claim_incident_number"]
        # Overwrite when empty, invalid year, or near-OCR mismatch
        if not cur or new.split(".")[0][:4] in {"2025", "2026", "2027"}:
            if (
                not cur
                or cur.split(".")[0][:4] not in {"2025", "2026", "2027"}
                or cur.split(".")[0] != new.split(".")[0]
            ):
                ins["claim_incident_number"] = new.split(".")[0] if "." not in new or len(new) > 16 else new
                # Prefer root without suffix for proforma Claim Incident No. field
                root = new.split(".", 1)[0]
                ins["claim_incident_number"] = root

    if vid.get("policy_number"):
        ins["policy_number"] = vid["policy_number"]

    if notebook.assessor.get("hospital") and not claim.get("hospital"):
        claim["hospital"] = notebook.assessor["hospital"]
    if notebook.assessor.get("diagnosis") and not claim.get("diagnosis"):
        claim["diagnosis"] = notebook.assessor["diagnosis"]

    # Finance: prefer assessor claimed amount over tiny / placeholder totals
    claimed = notebook.finance_hints.get("claimed_amount") or vid.get("claimed_amount")
    if claimed:
        try:
            amt = float(str(claimed).replace(",", ""))
            cur_raw = str(
                fin.get("total_hospital_bill")
                or claim.get("total_hospital_bill")
                or "0"
            )
            cur_digits = re.sub(r"[^\d.]", "", cur_raw) or "0"
            cur_amt = float(cur_digits)
            if amt >= 5000 and (cur_amt < 5000 or amt >= cur_amt * 1.2 or cur_amt in (50000.0, 0.0)):
                # Don't blindly replace a carefully extracted larger final bill
                if cur_amt < amt or cur_amt in (0.0, 50000.0):
                    labeled = f"Rs. {amt:,.2f}".rstrip("0").rstrip(".")
                    fin["total_hospital_bill"] = labeled
                    claim["total_hospital_bill"] = labeled
        except ValueError:
            pass

    _merge_fwa_into_result(result, notebook.fwa_findings)

    # Verdict nudges from high-severity notebook FWA
    high_inds = " ".join(
        str(f.get("indicator") or "")
        for f in notebook.fwa_findings
        if str(f.get("severity") or "").lower() == "high"
    ).lower()
    if any(
        k in high_inds
        for k in (
            "non-disclosure",
            "foreign patient",
            "name mismatch",
            "diagnostic admission",
            "physiologically incompatible",
            "anion gap",
            "alcohol-withdrawal",
        )
    ):
        result["claim_recommended"] = "No"
        result["claim_not_recommended"] = "Yes"
        if "non-compliant" not in str(result.get("compliance_verdict") or "").lower():
            result["compliance_verdict"] = "Non-Compliant"
        existing = str(result.get("auditor_conclusion") or result.get("inference") or "")
        if not re.search(r"repudiat|reject(?:ion|ed)|do not recommend", existing, re.I):
            result["inference"] = (
                "Case Notebook review identified high-severity FWA / integrity findings "
                "(identity mismatch, physiological contradictions, material non-disclosure, "
                "and/or diagnostic-only admission). Claim is not recommended for approval; "
                "apply policy disclosure and diagnostic-admission exclusions after human review."
            )
            result["auditor_conclusion"] = result["inference"]

    _seed_notebook_observations(result, notebook)

    # Structured FWA panel for PDF
    result["fwa_investigation"] = [
        {
            "indicator": f.get("indicator"),
            "severity": f.get("severity"),
            "evidence": f.get("evidence"),
            "recommendation": f.get("recommendation"),
            "citation": f.get("citation") or {},
        }
        for f in (result.get("fraud_abuse") or {}).get("findings") or []
        if isinstance(f, dict)
    ][:12]

    return result
