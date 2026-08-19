"""Authoritative identity + finance seal (final word before PDF).

Problem this solves
-------------------
LLM + OCR emit near-duplicate claim/policy IDs and round-number bills (₹50,000).
Assessor OCR is useful but often *itself* wrong by 1–2 digits. Blindly preferring
Assessor locked in Bency errors like:

  claim  2026077000347  → true 2026071700347   (DOA 17/07/2026 embeds as 20260717)
  policy H1677879       → true H1677679
  bill   Rs. 50,000     → Assessor claimed 80,800

Design
------
1. Collect candidates with provenance (assessor_labeled > labeled > bare).
2. Score claims with IFFCO date embedding (YYYYMMDD from DOA) beating Assessor.
3. Score policies by weighted labeled votes + digit-column majority in twin clusters.
4. Bills: Assessor claimed amount / real extracts beat round placeholders.
5. ``apply_identity_seal`` runs LAST in the audit pipeline and overwrites headers.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.notebook.contradictions import _names_equivalent, _norm_name
from backend.notebook.validators import (
    _claim_year_ok,
    _hamming,
    normalize_claim_incident,
)
from backend.utils.demographics_normalizer import normalize_policy_number

# Round figures the LLM loves to invent when it has no bill
_PLACEHOLDER_BILLS = {0.0, 25000.0, 50000.0, 75000.0, 100000.0, 150000.0, 200000.0}


@dataclass
class IdentitySeal:
    claim_incident_number: str = ""
    policy_number: str = ""
    total_hospital_bill: str = ""
    claimed_amount: str = ""
    pack_mismatch: bool = False
    assessor_patient: str = ""
    expected_patient: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)


def detect_assessor_patient_mismatch(
    assessor: Optional[dict],
    expected_patient_name: str,
) -> Optional[Dict[str, str]]:
    """If Assessor insured name is a different person than the audit patient, return details.

    Chandra Kant Upadhyay + Bency Assessor pack is the canonical failure: seal must NOT
    stamp Bency claim/policy onto Chandra's report.
    """
    assessor = assessor or {}
    expected = (expected_patient_name or "").strip()
    assessor_name = str(assessor.get("patient_name") or "").strip()
    if len(_norm_name(expected)) < 5 or len(_norm_name(assessor_name)) < 5:
        return None
    if _names_equivalent(assessor_name, expected):
        return None
    return {
        "assessor_patient": assessor_name,
        "expected_patient": expected,
    }


def _parse_money(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if float(raw) >= 0 else None
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", str(raw).replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _fmt_inr(amount: float) -> str:
    if amount == int(amount):
        return f"Rs. {int(amount):,}"
    return f"Rs. {amount:,.2f}".rstrip("0").rstrip(".")


def parse_admission_yyyymmdd(raw: Any) -> str:
    """Return YYYYMMDD from common Indian date forms, else ''."""
    s = str(raw or "").strip()
    if not s:
        return ""
    # ISO
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", s)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](20\d{2})\b", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y}{mo:02d}{d:02d}"
    # YYYYMMDD already
    m = re.fullmatch(r"(20\d{6})", re.sub(r"\D", "", s)[:8] + re.sub(r"\D", "", s)[8:8])
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8 and digits[:4].startswith("20"):
        # try first 8 as YYYYmmdd
        cand = digits[:8]
        try:
            mo, d = int(cand[4:6]), int(cand[6:8])
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return cand
        except ValueError:
            pass
    return ""


def extract_admission_yyyymmdd_from_result(result: dict, claim_facts: Optional[dict] = None) -> str:
    claim = (result or {}).get("claim_details") or {}
    cf = claim_facts or {}
    for src in (
        claim.get("date_of_admission"),
        cf.get("date_of_admission"),
        ((result or {}).get("case_facts_ledger") or {}).get("merged", {}).get("admission_date")
        if isinstance((result or {}).get("case_facts_ledger"), dict)
        else "",
    ):
        ymd = parse_admission_yyyymmdd(src)
        if ymd:
            return ymd
    return ""


def _claim_candidates_with_weights(
    corpus: str,
    assessor: Optional[dict] = None,
) -> List[Tuple[str, int, str]]:
    """Return (normalized_claim, weight, source)."""
    assessor = assessor or {}
    out: List[Tuple[str, int, str]] = []

    def add(raw: str, weight: int, source: str) -> None:
        n = normalize_claim_incident(raw)
        if n:
            out.append((n, weight, source))

    # Assessor labeled — strong but NOT absolute
    if assessor.get("sub_claim_number"):
        add(str(assessor["sub_claim_number"]), 80, "assessor_subclaim")
        add(str(assessor["sub_claim_number"]).split(".", 1)[0], 70, "assessor_subclaim_root")
    if assessor.get("claim_number"):
        add(str(assessor["claim_number"]), 70, "assessor_claim")

    for m in re.finditer(
        r"(?:sub\s*claim\s*(?:number|no\.?))\s*[:.]?\s*"
        r"(\d{10,16}\.[A-Za-z0-9]{1,4})",
        corpus or "",
        re.I,
    ):
        add(m.group(1), 60, "labeled_subclaim")

    for m in re.finditer(
        r"(?:claim\s*(?:incident|number|no\.?))\s*[:.]?\s*"
        r"(\d{10,16}(?:\.[A-Za-z0-9]{1,4})?)",
        corpus or "",
        re.I,
    ):
        add(m.group(1), 40, "labeled_claim")

    for m in re.finditer(r"\b(20\d{11})(?:\.[A-Za-z0-9]{1,4})?\b", corpus or ""):
        add(m.group(0), 10, "bare_claim")

    return out


def resolve_claim_number(
    corpus: str,
    assessor: Optional[dict] = None,
    admission_yyyymmdd: str = "",
    current_claim: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Pick claim id. DOA date embedding beats Assessor OCR digit noise."""
    cands = _claim_candidates_with_weights(corpus, assessor)
    if current_claim:
        n = normalize_claim_incident(current_claim)
        if n:
            cands.append((n, 5, "current"))

    if not cands:
        return "", {"reason": "no_candidates"}

    doa = admission_yyyymmdd if re.fullmatch(r"20\d{6}", admission_yyyymmdd or "") else ""

    # Build unique roots with aggregate score
    scores: Dict[str, float] = defaultdict(float)
    sources: Dict[str, List[str]] = defaultdict(list)
    best_full: Dict[str, str] = {}  # root -> prefer version with .R1

    for full, weight, source in cands:
        root = full.split(".", 1)[0]
        if not re.fullmatch(r"\d{10,16}", root):
            continue
        scores[root] += weight
        sources[root].append(source)
        prev = best_full.get(root, "")
        if "." in full or not prev:
            best_full[root] = full if ("." in full or not prev) else prev
        if "." in full:
            best_full[root] = full

    # Date embedding: IFFCO-style claim = YYYYMMDD + serial
    if doa:
        # Boost exact date-prefix matches heavily
        for root in list(scores.keys()):
            if len(root) >= 13 and root[:8] == doa:
                scores[root] += 200

        # Reconstruct from each 13-digit candidate: force DOA into positions 0..7
        for root in list(scores.keys()):
            if len(root) != 13:
                continue
            repaired = doa + root[8:]
            if repaired == root:
                continue
            # Only accept reconstruction if original was a near twin (OCR noise)
            if _hamming(root, repaired) <= 4 and _claim_year_ok(repaired):
                scores[repaired] += scores[root] + 250  # date repair wins
                sources[repaired].extend(sources[root] + [f"doa_repair_from:{root}"])
                best_full[repaired] = repaired

    if not scores:
        return "", {"reason": "empty_scores"}

    # Prefer good-year roots
    def sort_key(root: str) -> Tuple:
        return (
            1 if _claim_year_ok(root) else 0,
            1 if (doa and len(root) >= 8 and root[:8] == doa) else 0,
            scores[root],
            1 if len(root) == 13 else 0,
        )

    winner = max(scores.keys(), key=sort_key)
    full = best_full.get(winner, winner)
    # Prefer root without suffix for Claim Incident No. field
    root = full.split(".", 1)[0]
    return root, {
        "winner": root,
        "score": scores[winner],
        "sources": sources[winner][:12],
        "doa": doa,
        "top": sorted(scores.items(), key=lambda x: -x[1])[:5],
    }


def _policy_candidates_with_weights(
    corpus: str,
    assessor: Optional[dict] = None,
) -> List[Tuple[str, int, str]]:
    assessor = assessor or {}
    out: List[Tuple[str, int, str]] = []

    def add(raw: str, weight: int, source: str) -> None:
        p = normalize_policy_number(raw)
        if p and re.match(r"^H\d{5,}$", p):
            out.append((p, weight, source))

    if assessor.get("policy_number"):
        add(str(assessor["policy_number"]), 70, "assessor_policy")

    for m in re.finditer(
        r"policy\s*(?:number|no\.?)\s*[:.]?\s*(H[A-Z0-9Il]{5,12})",
        corpus or "",
        re.I,
    ):
        add(m.group(1), 50, "labeled_policy")

    # Member / insured id often printed correctly: H1677679-1-1
    for m in re.finditer(r"\b(H[A-Z0-9Il]{6,10})\s*[-–]\s*\d{1,2}\s*[-–]\s*\d{1,2}\b", corpus or "", re.I):
        add(m.group(1), 90, "member_code")

    for m in re.finditer(r"\b(H[A-Z0-9Il]{6,12})\b", corpus or "", re.I):
        add(m.group(1), 8, "bare_policy")

    return out


def resolve_policy_number(
    corpus: str,
    assessor: Optional[dict] = None,
    current_policy: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Pick policy via weighted provenance + digit-column vote among OCR twins."""
    cands = _policy_candidates_with_weights(corpus, assessor)
    if current_policy:
        p = normalize_policy_number(current_policy)
        if p:
            cands.append((p, 5, "current"))

    if not cands:
        return "", {"reason": "no_candidates"}

    scores: Dict[str, float] = defaultdict(float)
    sources: Dict[str, List[str]] = defaultdict(list)
    labeled_for_vote: List[str] = []

    for pol, weight, source in cands:
        scores[pol] += weight
        sources[pol].append(source)
        if source in {"assessor_policy", "labeled_policy", "member_code"}:
            reps = 2 if source in {"assessor_policy", "member_code"} else 1
            labeled_for_vote.extend([pol] * reps)

    # OCR confusion repair: Assessor (and other) readings often flip 6↔8.
    # If a 1-digit twin is independently attested, transfer weight to the twin
    # so Assessor OCR cannot lock the wrong policy.
    _FLIP = {"6": "8", "8": "6", "0": "8", "8": "0", "1": "7", "7": "1", "5": "6", "6": "5"}

    def confusion_twins(pol: str) -> List[str]:
        if not re.fullmatch(r"H\d{7}", pol or ""):
            return []
        out = []
        digits = list(pol[1:])
        for i, ch in enumerate(digits):
            for alt in { _FLIP.get(ch, ""), "6" if ch == "8" else "", "8" if ch == "6" else ""}:
                if alt and alt != ch:
                    tw = "H" + "".join(digits[:i] + [alt] + digits[i + 1 :])
                    out.append(tw)
        return list(dict.fromkeys(out))

    attested = set(scores.keys())
    assessor_vals = {p for p, _w, s in cands if s == "assessor_policy"}
    for ap in assessor_vals:
        for twin in confusion_twins(ap):
            if twin in attested and twin != ap:
                scores[twin] += scores[ap] + 100
                sources[twin].append(f"ocr_confusion_twin_of:{ap}")
                scores[ap] = max(0, scores[ap] - 60)

    # Cluster H+7digit: digit-column majority across labeled occurrences
    if labeled_for_vote:
        cols: List[Counter] = [Counter() for _ in range(7)]
        usable = [p for p in labeled_for_vote if re.fullmatch(r"H\d{7}", p)]
        if usable:
            for pol in usable:
                for i, ch in enumerate(pol[1:]):
                    cols[i][ch] += 1
            voted = "H" + "".join(cols[i].most_common(1)[0][0] for i in range(7))
            if re.fullmatch(r"H\d{7}", voted):
                scores[voted] += 80
                sources[voted].append("digit_column_vote")

    def sort_key(pol: str) -> Tuple:
        return (
            1 if re.fullmatch(r"H\d{7}", pol) else 0,
            scores[pol],
            1 if any(s.startswith("member") or "confusion" in s for s in sources[pol]) else 0,
        )

    winner = max(scores.keys(), key=sort_key)

    # Member code always wins over a 1-digit twin
    for pol, _w, src in cands:
        if src == "member_code" and re.fullmatch(r"H\d{7}", pol) and re.fullmatch(r"H\d{7}", winner):
            if _hamming(pol[1:], winner[1:]) <= 1:
                winner = pol
                break

    return winner, {
        "winner": winner,
        "score": scores[winner],
        "sources": sources[winner][:12],
        "top": sorted(scores.items(), key=lambda x: -x[1])[:5],
    }


def resolve_bill_amount(
    corpus: str,
    assessor: Optional[dict] = None,
    current_bill: str = "",
) -> Tuple[str, Dict[str, Any]]:
    assessor = assessor or {}
    amounts: List[Tuple[float, int, str]] = []

    def add(raw: Any, weight: int, source: str) -> None:
        amt = _parse_money(raw)
        if amt is not None and amt >= 1000:
            amounts.append((amt, weight, source))

    if assessor.get("claimed_amount"):
        add(assessor["claimed_amount"], 100, "assessor_claimed")

    for pat, weight, source in (
        (
            r"(?:claimed\s*amount|total\s*claimed|amount\s*claimed)\s*[:.]?\s*"
            r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
            90,
            "labeled_claimed",
        ),
        (
            r"(?:claimed\s*amount|total\s*claimed|amount\s*claimed)\s*[:.]?\s*(?:rs\.?|inr|₹)?\s*"
            r"\n\s*([\d,]+(?:\.\d{1,2})?)",
            85,
            "labeled_claimed_nextline",
        ),
        (
            r"(?:total\s*(?:hospital\s*)?bill|grand\s*total|net\s*amount|sum\s*total)\s*[:.]?\s*"
            r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)",
            60,
            "labeled_bill_total",
        ),
    ):
        for m in re.finditer(pat, corpus or "", re.I):
            add(m.group(1), weight, source)

    for b in assessor.get("finance_bills") or []:
        if isinstance(b, dict):
            add(b.get("amount"), 40, "assessor_bill_row")

    cur = _parse_money(current_bill)
    if cur is not None:
        # Round placeholders get tiny weight
        w = 2 if cur in _PLACEHOLDER_BILLS else 25
        add(cur, w, "current")

    if not amounts:
        return "", {"reason": "no_amounts"}

    # Aggregate by amount value
    scores: Dict[float, float] = defaultdict(float)
    sources: Dict[float, List[str]] = defaultdict(list)
    for amt, weight, source in amounts:
        # Penalize classic placeholders unless they're the only signal
        if amt in _PLACEHOLDER_BILLS and source != "assessor_claimed":
            weight = min(weight, 5)
        scores[amt] += weight
        sources[amt].append(source)

    winner = max(scores.keys(), key=lambda a: (scores[a], a))
    # If winner is placeholder but a non-placeholder with score>=40 exists, take that
    if winner in _PLACEHOLDER_BILLS:
        alts = [a for a in scores if a not in _PLACEHOLDER_BILLS and scores[a] >= 40]
        if alts:
            winner = max(alts, key=lambda a: (scores[a], a))

    return _fmt_inr(winner), {
        "winner": winner,
        "score": scores[winner],
        "sources": sources[winner][:12],
        "top": sorted(((a, scores[a]) for a in scores), key=lambda x: -x[1])[:5],
    }


def build_identity_seal(
    *,
    corpus_text: str,
    assessor: Optional[dict] = None,
    admission_yyyymmdd: str = "",
    current_claim: str = "",
    current_policy: str = "",
    current_bill: str = "",
    expected_patient_name: str = "",
) -> IdentitySeal:
    assessor = dict(assessor or {})
    mismatch = detect_assessor_patient_mismatch(assessor, expected_patient_name)

    # Wrong-patient Assessor pack: never trust its claim/policy/amount (or DOA repair of it)
    assessor_for_ids: Dict[str, Any] = assessor
    if mismatch:
        assessor_for_ids = {}

    claim, claim_meta = resolve_claim_number(
        corpus_text, assessor_for_ids, admission_yyyymmdd, current_claim
    )
    policy, policy_meta = resolve_policy_number(corpus_text, assessor_for_ids, current_policy)
    bill, bill_meta = resolve_bill_amount(corpus_text, assessor_for_ids, current_bill)

    if mismatch:
        # Contaminated corpus still contains the other patient's IDs — withhold headers
        # rather than stamp another insured's claim onto this report.
        other_claim = normalize_claim_incident(
            str(assessor.get("sub_claim_number") or assessor.get("claim_number") or "")
        ).split(".", 1)[0]
        other_pol = normalize_policy_number(assessor.get("policy_number") or "")
        if claim and other_claim and (
            claim == other_claim
            or (len(claim) == len(other_claim) and _hamming(claim, other_claim) <= 4)
        ):
            claim = ""
            claim_meta = {**claim_meta, "withheld": "assessor_pack_mismatch"}
        if policy and other_pol and (
            policy == other_pol
            or (
                re.fullmatch(r"H\d{7}", policy)
                and re.fullmatch(r"H\d{7}", other_pol)
                and _hamming(policy[1:], other_pol[1:]) <= 1
            )
        ):
            policy = ""
            policy_meta = {**policy_meta, "withheld": "assessor_pack_mismatch"}
        # Withhold Assessor claimed amount; keep non-assessor bill if distinct
        other_amt = _parse_money(assessor.get("claimed_amount"))
        bill_amt = _parse_money(bill)
        if other_amt and bill_amt and abs(other_amt - bill_amt) < 1.0:
            bill = ""
            bill_meta = {**bill_meta, "withheld": "assessor_pack_mismatch"}
        claimed = ""
    else:
        claimed = str(assessor.get("claimed_amount") or "").strip()
        if not claimed and bill_meta.get("winner"):
            claimed = (
                str(int(bill_meta["winner"]))
                if float(bill_meta["winner"]) == int(bill_meta["winner"])
                else str(bill_meta["winner"])
            )

    return IdentitySeal(
        claim_incident_number=claim,
        policy_number=policy,
        total_hospital_bill=bill,
        claimed_amount=claimed,
        pack_mismatch=bool(mismatch),
        assessor_patient=(mismatch or {}).get("assessor_patient", ""),
        expected_patient=(mismatch or {}).get("expected_patient", ""),
        provenance={
            "claim": claim_meta,
            "policy": policy_meta,
            "bill": bill_meta,
            "pack_mismatch": mismatch,
        },
    )


def apply_identity_seal(
    result: dict,
    seal: IdentitySeal,
    *,
    force_zero_recommended_if_rejected: bool = True,
) -> dict:
    """Overwrite insurance + finance headers. Call LAST before returning audit result."""
    if not result or result.get("error"):
        return result

    ins = result.setdefault("insurance_details", {})
    claim = result.setdefault("claim_details", {})
    fin = result.setdefault("financial_review", {})

    if seal.pack_mismatch:
        # Never leave another patient's sealed IDs on this report
        ins["claim_incident_number"] = ""
        ins["policy_number"] = ""
        result["pack_integrity"] = {
            "ok": False,
            "assessor_patient": seal.assessor_patient,
            "expected_patient": seal.expected_patient,
            "message": (
                f"Uploaded Assessor / claim pack appears to belong to "
                f"'{seal.assessor_patient}', not '{seal.expected_patient}'. "
                f"Claim/policy withheld. Re-upload the correct document set."
            ),
        }
        result["claim_recommended"] = "No"
        result["claim_not_recommended"] = "Yes"
        result["compliance_verdict"] = "Non-Compliant"
        result["inference"] = result["pack_integrity"]["message"]
        result["auditor_conclusion"] = result["pack_integrity"]["message"]
    else:
        if seal.claim_incident_number:
            ins["claim_incident_number"] = seal.claim_incident_number.split(".", 1)[0]
        if seal.policy_number:
            ins["policy_number"] = seal.policy_number

    if seal.total_hospital_bill:
        fin["total_hospital_bill"] = seal.total_hospital_bill
        claim["total_hospital_bill"] = seal.total_hospital_bill
        savings = result.get("claim_savings")
        if isinstance(savings, dict):
            savings["total_claim_amount"] = seal.total_hospital_bill

    # Rejected claims should not show a positive recommended approval
    if force_zero_recommended_if_rejected:
        rec = str(result.get("claim_recommended") or "").strip().lower()
        not_rec = str(result.get("claim_not_recommended") or "").strip().lower()
        if rec in {"no", "n"} or not_rec in {"yes", "y"} or seal.pack_mismatch:
            fin["recommended_approval_amount"] = "Rs. 0"
            fin["net_claimable_amount"] = fin.get("net_claimable_amount") or "Rs. 0"
            if isinstance(result.get("claim_savings"), dict):
                result["claim_savings"]["admissible_amount"] = "Rs. 0"
                result["claim_savings"]["recommended_approval_amount"] = "Rs. 0"

    result["identity_seal"] = {
        "claim_incident_number": seal.claim_incident_number,
        "policy_number": seal.policy_number,
        "total_hospital_bill": seal.total_hospital_bill,
        "pack_mismatch": seal.pack_mismatch,
        "provenance": seal.provenance,
    }
    return result
