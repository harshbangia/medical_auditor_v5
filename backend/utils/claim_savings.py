"""Build claim savings table: billed vs admissible amounts and % savings."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _parse_amount(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if val >= 0 else None
    s = str(val)
    # Prefer first currency-like number
    m = re.search(r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)", s, re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _fmt_inr(amount: Optional[float]) -> str:
    if amount is None:
        return "—"
    if amount == int(amount):
        return f"Rs. {int(amount):,}"
    return f"Rs. {amount:,.2f}"


def _pct(savings: float, billed: float) -> str:
    if billed <= 0:
        return "0%"
    return f"{(savings / billed) * 100:.1f}%"


# Common non-payable / routinely deductible categories for Indian health policies.
_DEFAULT_DEDUCTION_HINTS = [
    ("Admission / registration charges", 0.01, "Administrative charges typically non-payable"),
    ("Documentation / medical record charges", 0.005, "File / certificate charges typically non-payable"),
]


def _extract_line_items_from_text(case_text: str) -> List[Tuple[str, float]]:
    """Best-effort line items from pre-auth estimates / bills."""
    items: List[Tuple[str, float]] = []
    patterns = [
        re.compile(
            r"(ot\s*charges?|icu(?:\s*charges?)?|investigations?|room\s*rent|"
            r"pharmacy|medicines?|consumables?|procedure\s*charges?|"
            r"surgeon(?:'s)?\s*fee|nursing\s*charges?)"
            r"\s*[:.\-]?\s*(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
            re.I,
        ),
    ]
    for pat in patterns:
        for m in pat.finditer(case_text or ""):
            label = re.sub(r"\s+", " ", m.group(1).strip()).title()
            amt = _parse_amount(m.group(2))
            if amt and amt >= 100:
                items.append((label, amt))
    return items


def _deductions_from_excluded(excluded_text: str, total: float) -> List[dict]:
    rows: List[dict] = []
    text = excluded_text or ""
    if not text.strip():
        return rows

    # Split on common separators
    parts = re.split(r"[,;\n]| and ", text)
    for part in parts:
        part = part.strip(" .-")
        if len(part) < 3:
            continue
        # Skip routine PPIs — never treat as savings line
        if re.search(r"panto(?:prazole)?|pentaprazole|\bpan\b|\bppi\b", part, re.I):
            continue
        amt = _parse_amount(part)
        if amt is None and total > 0:
            # Allocate a small placeholder only when amount unknown — skip tiny noise
            continue
        if amt is None or amt < 100:
            continue
        rows.append({
            "item": re.sub(r"(?:rs\.?|inr|₹)\s*[\d,]+(?:\.\d{1,2})?", "", part, flags=re.I).strip(" -"),
            "billed_amount": _fmt_inr(amt),
            "admissible_amount": _fmt_inr(0),
            "amount_saved": _fmt_inr(amt),
            "reason": "Non-payable / inadmissible per policy or clinical audit",
        })
    return rows


def build_claim_savings(
    result: dict,
    case_text: str = "",
    claim_facts: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Produce claim_savings section:
      - line items table
      - total billed, admissible, saved, savings_percentage
    """
    claim_facts = claim_facts or {}
    fin = result.setdefault("financial_review", {})
    tba = result.get("treatment_billing_audit") or {}

    total = (
        _parse_amount(fin.get("total_hospital_bill"))
        or _parse_amount(claim_facts.get("total_hospital_bill"))
    )
    non_payable = _parse_amount(fin.get("non_payable_amount"))
    net_claimable = _parse_amount(fin.get("net_claimable_amount"))
    recommended = _parse_amount(fin.get("recommended_approval_amount"))

    rows: List[dict] = []

    # Prefer LLM-provided line items when present
    for row in result.get("claim_savings_line_items") or []:
        if not isinstance(row, dict):
            continue
        item = str(row.get("item") or "").strip()
        if not item:
            continue
        if re.search(r"panto(?:prazole)?|pentaprazole|\bpan\b|\bppi\b", item, re.I):
            continue
        rows.append({
            "item": item,
            "billed_amount": row.get("billed_amount") or "—",
            "admissible_amount": row.get("admissible_amount") or "—",
            "amount_saved": row.get("amount_saved") or "—",
            "reason": row.get("reason") or "",
        })

    # From excluded items text
    rows.extend(_deductions_from_excluded(str(tba.get("excluded_items_billed") or ""), total or 0))

    # From bill line items in text
    line_items = _extract_line_items_from_text(case_text)
    for label, amt in line_items:
        # Mark common non-payable admin labels as full deduction
        if re.search(r"admission|registration|documentation|medical\s+record", label, re.I):
            rows.append({
                "item": label,
                "billed_amount": _fmt_inr(amt),
                "admissible_amount": _fmt_inr(0),
                "amount_saved": _fmt_inr(amt),
                "reason": "Administrative / non-payable per typical policy annexure",
            })

    # Compute totals
    saved_from_rows = 0.0
    for row in rows:
        s = _parse_amount(row.get("amount_saved"))
        if s:
            saved_from_rows += s

    if total is None and line_items:
        total = sum(a for _, a in line_items)

    if non_payable is None and saved_from_rows > 0:
        non_payable = saved_from_rows

    if total is not None and non_payable is not None and net_claimable is None:
        net_claimable = max(total - non_payable, 0)

    if recommended is None and net_claimable is not None:
        recommended = net_claimable

    savings = None
    if total is not None and net_claimable is not None:
        savings = max(total - net_claimable, 0)
    elif non_payable is not None:
        savings = non_payable

    # If we have a total but no deductions identified, still show the summary row
    if total is not None and not rows:
        rows.append({
            "item": "Hospital claim (total)",
            "billed_amount": _fmt_inr(total),
            "admissible_amount": _fmt_inr(net_claimable if net_claimable is not None else total),
            "amount_saved": _fmt_inr(savings if savings is not None else 0),
            "reason": (
                "No itemised non-payable deductions identified from documents; "
                "admissible amount equals billed pending detailed bill audit"
                if not savings else
                "Aggregate non-payable amount from audit"
            ),
        })
    elif total is not None:
        # Summary row at top
        rows.insert(0, {
            "item": "TOTAL HOSPITAL CLAIM",
            "billed_amount": _fmt_inr(total),
            "admissible_amount": _fmt_inr(net_claimable if net_claimable is not None else total),
            "amount_saved": _fmt_inr(savings if savings is not None else 0),
            "reason": "Aggregate claim vs admissible after audit deductions",
        })

    savings_pct = _pct(savings or 0, total or 0) if total else "—"

    # Write back key financial fields when empty
    if total is not None and not str(fin.get("total_hospital_bill") or "").strip():
        fin["total_hospital_bill"] = _fmt_inr(total)
    if non_payable is not None and not str(fin.get("non_payable_amount") or "").strip():
        fin["non_payable_amount"] = _fmt_inr(non_payable)
    if net_claimable is not None and not str(fin.get("net_claimable_amount") or "").strip():
        fin["net_claimable_amount"] = _fmt_inr(net_claimable)
    if recommended is not None and not str(fin.get("recommended_approval_amount") or "").strip():
        fin["recommended_approval_amount"] = _fmt_inr(recommended)

    fin["amount_saved"] = _fmt_inr(savings if savings is not None else 0)
    fin["savings_percentage"] = savings_pct

    return {
        "total_claim_amount": _fmt_inr(total),
        "admissible_amount": _fmt_inr(net_claimable if net_claimable is not None else total),
        "amount_saved": _fmt_inr(savings if savings is not None else 0),
        "savings_percentage": savings_pct,
        "highlight": True,
        "line_items": rows,
        "notes": (
            "Amount saved = Total hospital claim − Admissible amount after deducting "
            "inadmissible medicines / procedures / non-payable charges."
        ),
    }
