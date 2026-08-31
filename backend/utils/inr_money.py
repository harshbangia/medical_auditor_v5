"""INR amount parsing and financial-header recompute for Expert Opinion reports."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_EMPTY = {"", "na", "n/a", "none", "null", "-", "—", ".", "nil"}


def parse_inr(raw: Any) -> Optional[float]:
    """Parse Indian/Western rupee strings to float. Returns None if unusable."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if val >= 0 else None

    s = str(raw).strip()
    if not s or s.lower() in _EMPTY:
        return None

    # Prefer an explicit currency-prefixed amount when present
    m = re.search(
        r"(?:rs\.?|inr|₹)\s*([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)",
        s,
        re.I,
    )
    if not m:
        m = re.search(
            r"([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)",
            s,
        )
    if not m:
        return None
    token = m.group(1).replace(",", "")
    try:
        val = float(token)
    except ValueError:
        return None
    # Guard against ratios / junk (e.g. 1.09% mistaken as rupees when tiny vs bill)
    return val if val >= 0 else None


def format_inr(amount: float) -> str:
    whole = int(round(amount))
    return f"Rs. {whole:,}"


def billing_disallowance_rows(data: dict) -> List[dict]:
    rows = data.get("billing_disallowances") or data.get("recommended_deductions") or []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def row_amount(row: dict) -> Optional[float]:
    """Amount from structured fields, else from title text."""
    for key in ("amount", "deduction_amount", "rupees", "value"):
        n = parse_inr(row.get(key))
        if n is not None and n > 0:
            return n
    # Fallback: "Laparoscopy — Rs. 15,000" in title
    for key in ("title", "item", "category", "reason", "evidence"):
        n = parse_inr(row.get(key))
        if n is not None and n >= 100:  # ignore tiny noise in prose
            return n
    return None


def sum_disallowances(data: dict) -> float:
    total = 0.0
    for row in billing_disallowance_rows(data):
        n = row_amount(row)
        if n is not None:
            total += n
    return total


def recompute_financial_review(result: dict) -> dict:
    """Force §5 math from bill total − sum(billing_disallowances).

    Models often emit broken non_payable / net / claim_savings (e.g. Rs. 1 / 0.00)
    while itemised disallowance rows are correct. Always trust the rows.
    """
    if not isinstance(result, dict):
        return result

    claim = result.get("claim_details")
    if not isinstance(claim, dict):
        claim = {}
        result["claim_details"] = claim
    fin = result.get("financial_review")
    if not isinstance(fin, dict):
        fin = {}
        result["financial_review"] = fin

    total = parse_inr(
        fin.get("total_hospital_bill")
        or claim.get("total_hospital_bill")
        or claim.get("bill_amount")
        or (result.get("claim_savings") or {}).get("total_claim_amount")
    )
    if total is not None and total > 0:
        fin["total_hospital_bill"] = format_inr(total)
        claim["total_hospital_bill"] = format_inr(total)

    disallow_sum = sum_disallowances(result)
    model_np = parse_inr(fin.get("non_payable_amount"))

    # Prefer itemised sum; fall back to model non-payable only if no rows
    if disallow_sum > 0:
        non_payable = disallow_sum
    elif model_np is not None and model_np > 1.5:  # ignore junk like 1 / 1.09
        non_payable = model_np
    else:
        non_payable = None

    if non_payable is not None:
        fin["non_payable_amount"] = format_inr(non_payable)
        fin["patient_liability"] = format_inr(non_payable)
        if total is not None and total > 0:
            net = max(total - non_payable, 0.0)
            fin["net_claimable_amount"] = format_inr(net)
            fin["recommended_approval_amount"] = format_inr(net)
        else:
            # Keep model net only if plausible
            model_net = parse_inr(fin.get("net_claimable_amount"))
            if model_net is not None and model_net > 1.5:
                fin["net_claimable_amount"] = format_inr(model_net)
                fin["recommended_approval_amount"] = format_inr(
                    parse_inr(fin.get("recommended_approval_amount")) or model_net
                )

    # Never let a broken claim_savings.admissible_amount (0) win in the PDF
    if total is not None or non_payable is not None:
        net_s = fin.get("net_claimable_amount") or ""
        result["claim_savings"] = {
            "total_claim_amount": fin.get("total_hospital_bill") or "",
            "admissible_amount": net_s,
            "amount_saved": fin.get("non_payable_amount") or "",
            "savings_percentage": "",
        }

    return result


def dedupe_observations(observations: List[Any], *, max_items: int = 8) -> List[dict]:
    """Keep deep Q&As; drop seeded duplicates and near-duplicate questions."""
    if not isinstance(observations, list):
        return []

    skip_prefixes = (
        "documentation / forensic gap:",
        "is the billed item '",
    )
    cleaned: List[dict] = []
    seen_norm: set[str] = set()

    for obs in observations:
        if not isinstance(obs, dict):
            continue
        q = str(obs.get("question") or "").strip()
        analysis = str(obs.get("analysis") or obs.get("justification") or "").strip()
        answer = str(obs.get("answer") or "").strip()
        if not q:
            continue
        q_l = q.lower()
        if any(q_l.startswith(p) for p in skip_prefixes):
            continue
        # Drop ultra-short seeded stubs
        if analysis and len(analysis) < 80 and q_l.startswith("is the billed"):
            continue
        norm = re.sub(r"\W+", " ", q_l).strip()
        # Also skip if question largely repeats an earlier one
        if norm in seen_norm:
            continue
        duplicate = False
        for prev in seen_norm:
            if norm in prev or prev in norm:
                if min(len(norm), len(prev)) >= 24:
                    duplicate = True
                    break
        if duplicate:
            continue
        seen_norm.add(norm)
        cleaned.append(
            {
                "question": q,
                "answer": answer or "Insufficient Evidence",
                "analysis": analysis,
            }
        )
        if len(cleaned) >= max_items:
            break
    return cleaned
