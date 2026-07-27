"""Evidence verifier — drop or downgrade findings without document grounding."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set


_GENERIC_NO_EVIDENCE = re.compile(
    r"^(not\s+documented|insufficient\s+evidence|unknown|n/?a|—|-)$",
    re.I,
)


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


def _collect_source_filenames(case_text: str, source_summaries: Optional[List[dict]]) -> Set[str]:
    names: Set[str] = set()
    for src in source_summaries or []:
        if isinstance(src, dict) and src.get("filename"):
            names.add(str(src["filename"]).strip().lower())
    for m in re.finditer(r"=== Source document:\s*(.+?)\s*===", case_text or "", re.I):
        names.add(m.group(1).strip().lower())
    return names


def _mentions_source(text: str, filenames: Set[str]) -> bool:
    blob = _norm(text)
    if not blob:
        return False
    for name in filenames:
        # Match filename stem (without extension) to be resilient
        stem = name.rsplit(".", 1)[0]
        if stem and stem in blob:
            return True
        if name and name in blob:
            return True
    # Soft credit for explicit citation language
    if re.search(r"\b(discharge\s+summary|pre[\s-]?auth|bill|invoice|lab\s+report|page\s+\d+)\b", blob):
        return True
    return False


def _observation_supported(obs: dict, filenames: Set[str], case_text: str) -> bool:
    analysis = str(obs.get("analysis") or "")
    question = str(obs.get("question") or "")
    answer = _norm(obs.get("answer") or "")
    if answer in {"insufficient evidence", "not supported"} and not analysis.strip():
        return False
    if _mentions_source(analysis + " " + question, filenames):
        return True
    # If analysis quotes a distinctive 40+ char substring from case text, accept
    snippet = re.sub(r"\s+", " ", analysis).strip()
    if len(snippet) >= 40 and snippet[:40].lower() in (case_text or "").lower():
        return True
    return False


def verify_audit_result(
    result: dict,
    case_text: str = "",
    source_summaries: Optional[List[dict]] = None,
    case_facts_ledger: Optional[dict] = None,
) -> dict:
    """Post-audit QA pass: annotate confidence and strip hollow findings."""
    if not result or result.get("error"):
        return result

    filenames = _collect_source_filenames(case_text, source_summaries)
    qa_notes: List[str] = []

    # Observations: keep all, but flag unsupported for reviewer
    observations = result.get("observations") or []
    if isinstance(observations, list):
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            supported = _observation_supported(obs, filenames, case_text)
            conf = 0.75 if supported else 0.4
            obs["evidence_supported"] = supported
            obs["confidence"] = conf
            if not supported:
                qa_notes.append(
                    f"Observation may lack document citation: {(obs.get('question') or '')[:80]}"
                )

    # Guideline deviations: require case_evidence content
    deviations = result.get("guideline_deviations") or []
    cleaned_devs = []
    if isinstance(deviations, list):
        for dev in deviations:
            if not isinstance(dev, dict):
                continue
            evidence = str(dev.get("case_evidence") or "").strip()
            if not evidence or _GENERIC_NO_EVIDENCE.match(evidence):
                qa_notes.append(
                    f"Dropped deviation without case evidence: {(dev.get('issue') or '')[:80]}"
                )
                continue
            if filenames and not _mentions_source(
                evidence + " " + str(dev.get("issue") or ""), filenames
            ):
                # Keep but lower confidence / add note
                dev["confidence"] = 0.45
                qa_notes.append(
                    f"Deviation missing explicit source file: {(dev.get('issue') or '')[:80]}"
                )
            else:
                dev["confidence"] = float(dev.get("confidence") or 0.8)
            cleaned_devs.append(dev)
        result["guideline_deviations"] = cleaned_devs

    # Clinical findings: require non-empty value
    findings = result.get("clinical_findings") or []
    if isinstance(findings, list):
        kept = []
        for row in findings:
            if not isinstance(row, dict):
                continue
            val = str(row.get("value") or "").strip()
            if not val or _GENERIC_NO_EVIDENCE.match(val):
                qa_notes.append(
                    f"Dropped empty clinical finding: {(row.get('parameter') or '')[:60]}"
                )
                continue
            if not str(row.get("source") or "").strip():
                # Prefer ledger diagnosis source as fallback hint
                merged = (case_facts_ledger or {}).get("merged") or {}
                row["source"] = merged.get("diagnosis_source") or "case documents"
                row["confidence"] = 0.5
            else:
                row["confidence"] = float(row.get("confidence") or 0.75)
            kept.append(row)
        result["clinical_findings"] = kept

    # Completeness gate notes from ledger
    merged = (case_facts_ledger or {}).get("merged") or {}
    if not (merged.get("patient_name") or (result.get("patient_details") or {}).get("name")):
        qa_notes.append("Patient name missing from structured extraction")
    if not (merged.get("diagnosis") or (result.get("claim_details") or {}).get("diagnosis")):
        qa_notes.append("Diagnosis missing from structured extraction")

    result["verification"] = {
        "passed": len([n for n in qa_notes if n.startswith("Dropped")]) == 0,
        "notes": qa_notes[:20],
        "observations_flagged": sum(
            1 for o in (result.get("observations") or [])
            if isinstance(o, dict) and o.get("evidence_supported") is False
        ),
    }
    return result
