"""Deterministic fraud / abuse / misrepresentation indicators for medical claims."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.utils.claim_details_extractor import filter_actionable_date_discrepancies
from backend.utils.clinical_fwa_signals import build_case180_style_findings


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _add(
    findings: List[dict],
    category: str,
    indicator: str,
    evidence: str,
    severity: str,
    recommendation: str = "",
) -> None:
    findings.append({
        "category": category,
        "indicator": indicator,
        "evidence": evidence,
        "severity": severity,
        "recommendation": recommendation,
    })


def detect_fraud_abuse(
    case_text: str,
    result: Optional[dict] = None,
    claim_facts: Optional[dict] = None,
) -> Dict[str, Any]:
    """Build fraud_abuse_findings from case text and structured audit result."""
    result = result or {}
    claim_facts = claim_facts or {}
    text = case_text or ""
    low = _norm(text)
    findings: List[dict] = []

    # 1. Non-disclosure / history contradiction (known case vs self-declaration)
    known_case = bool(re.search(
        r"known\s+case\s+of\s+(?:diabetes|hypertension|dm|htn|cad|ihd)",
        text,
        re.I,
    ))
    self_decl_neg = bool(re.search(
        r"(?:never\s+taken|no\s+prior\s+history|first\s+time\s+detected|"
        r"not\s+a\s+known\s+case|denies?\s+(?:any\s+)?(?:past|prior)\s+(?:history|illness))",
        text,
        re.I,
    ))
    if known_case and self_decl_neg:
        _add(
            findings,
            "misrepresentation",
            "Contradiction between documented medical history and self-declaration",
            "Records describe a known case of chronic disease while self-declaration "
            "denies prior history / medication — possible non-disclosure of material facts.",
            "High",
            "Hold claim pending investigation into duration of comorbidities and "
            "disclosure at policy inception / porting.",
        )

    # 2. Explicit fraud / investigation language (not policy boilerplate)
    if re.search(
        r"(?:suspected\s+fraud|fraudulent\s+claim|fraud\s+investigation|"
        r"investigation\s+(?:revealed|found|into).{0,60}fraud|"
        r"repudiat(?:ed|ion).{0,40}(?:fraud|misrepresentation)|"
        r"material\s+fact.{0,40}(?:conceal|withheld|not\s+disclosed))",
        text,
        re.I,
    ):
        _add(
            findings,
            "policy_compliance",
            "Fraud / non-disclosure language present in case file",
            "Case documents or investigation notes reference fraud, misrepresentation, "
            "or non-disclosure of material facts.",
            "High",
            "Review investigation report before approval; apply policy fraud clause if proven.",
        )

    # 3. Date discrepancies (exclude proposed vs actual admission — informational only)
    discrepancies = filter_actionable_date_discrepancies(
        result.get("date_discrepancies") or claim_facts.get("date_discrepancies") or []
    )
    if discrepancies:
        msgs = "; ".join(
            str(d.get("message") or d) for d in discrepancies[:3] if d
        )
        _add(
            findings,
            "documentation_abuse",
            "Conflicting dates across documents",
            msgs or "Multiple conflicting dates found across uploaded documents.",
            "Medium",
            "Reconcile admission / consultation / discharge dates with hospital.",
        )

    # 4. Pre-auth claimed but form missing
    tba = result.get("treatment_billing_audit") or {}
    cross = _norm(str(tba.get("cross_checked_with_preauth") or ""))
    has_preauth = bool(re.search(
        r"request\s+for\s+cashless|pre[\s-]?authori[sz]ation|preauth",
        text,
        re.I,
    ))
    if cross in ("yes", "y", "true") and not has_preauth:
        _add(
            findings,
            "billing_abuse",
            "Pre-authorization cross-check claimed without pre-auth form",
            "Billing audit states pre-auth was cross-checked but no pre-authorization "
            "form was found in uploaded records.",
            "Medium",
            "Obtain pre-auth form or correct billing audit statement.",
        )

    # 5. Room category upcoding signals
    admitted = _norm(str(tba.get("room_category_admitted") or ""))
    eligible = _norm(str(tba.get("room_category_eligible") or ""))
    if admitted and eligible and admitted != eligible:
        if "icu" in admitted and "icu" not in eligible:
            _add(
                findings,
                "billing_abuse",
                "Possible room category upcoding",
                f"Admitted room category '{tba.get('room_category_admitted')}' differs from "
                f"policy-eligible category '{tba.get('room_category_eligible')}'.",
                "Medium",
                "Verify ICU necessity and apply room-rent capping per policy.",
            )

    # 6. Unbundled OT / consumable billing language
    if re.search(
        r"(?:surgical\s+blades|boyles?\s+apparatus|admission\s+charges|"
        r"registration\s+charges|documentation\s+charges|medical\s+record\s+charges)"
        r".{0,40}(?:billed|charged|rs\.?)",
        text,
        re.I,
    ):
        _add(
            findings,
            "billing_abuse",
            "Possible unbundled / non-payable administrative charges",
            "Documents mention separately billed items commonly excluded or bundled "
            "(admission/registration/documentation charges, OT consumables).",
            "Low",
            "Deduct non-payable items per policy annexure.",
        )

    # 7. Claim amount vs sum insured (if both present)
    fin = result.get("financial_review") or {}
    bill = str(fin.get("total_hospital_bill") or "")
    if re.search(r"sum\s+insured", text, re.I) and bill:
        _add(
            findings,
            "financial_review",
            "Verify claim amount within sum insured",
            f"Hospital bill stated as {bill}; confirm against policy sum insured and "
            "cumulative bonus limits.",
            "Low",
            "Cross-check policy schedule sum insured before approval.",
        )

    # 8. Pancreatitis / alcohol / multi-hospital / pharmacy FWA (Case 180 style)
    for item in build_case180_style_findings(text, result):
        ind = str(item.get("indicator") or "").strip()
        if not ind:
            continue
        if any(_norm(f.get("indicator")) == _norm(ind) for f in findings):
            continue
        findings.append(item)

    # 9. LLM-seeded fraud findings (if any)
    for item in result.get("fraud_abuse_findings") or []:
        if not isinstance(item, dict):
            continue
        ind = str(item.get("indicator") or "").strip()
        if not ind:
            continue
        if any(_norm(f.get("indicator")) == _norm(ind) for f in findings):
            continue
        findings.append({
            "category": item.get("category") or "clinical_abuse",
            "indicator": ind,
            "evidence": item.get("evidence") or "",
            "severity": item.get("severity") or "Medium",
            "recommendation": item.get("recommendation") or "",
        })

    high = sum(1 for f in findings if _norm(f.get("severity")) == "high")
    if high:
        risk = "High"
        summary = (
            f"{len(findings)} fraud/abuse indicator(s) identified, including {high} high-severity. "
            "Do not approve until indicators are resolved."
        )
    elif findings:
        risk = "Medium"
        summary = (
            f"{len(findings)} fraud/abuse indicator(s) identified. "
            "Review recommendations before claim settlement."
        )
    else:
        risk = "Low"
        summary = "No deterministic fraud/abuse indicators detected from uploaded records."

    return {
        "risk_level": risk,
        "summary": summary,
        "findings": findings,
    }
