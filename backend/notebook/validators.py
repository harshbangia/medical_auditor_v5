"""Claim / policy ID validators with OCR repair (Bency / Case180 class errors)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from backend.utils.demographics_normalizer import normalize_policy_number


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def normalize_claim_incident(raw: str) -> str:
    """Normalize claim incident numbers; keep optional .R1 suffix."""
    val = re.sub(r"\s+", "", str(raw or "").strip().rstrip("."))
    m = re.fullmatch(r"(\d{10,16})(\.[A-Za-z0-9]{1,4})?", val, re.I)
    if not m:
        return ""
    return m.group(1) + (m.group(2) or "").upper()


def _hamming(a: str, b: str) -> int:
    if len(a) != len(b):
        return 99
    return sum(1 for x, y in zip(a, b) if x != y)


def repair_claim_ocr_candidates(candidates: Iterable[str]) -> str:
    """Pick best claim number; repair common OCR digit swaps when a near-match exists.

    Bency bug: 2020877700347 vs true 2026071700347 (year 2020 vs 2026, digit noise).
    Prefer numbers whose first 4 digits look like a recent claim year (2024–2027).
    """
    norms: List[str] = []
    for c in candidates:
        n = normalize_claim_incident(c)
        if n:
            norms.append(n)
    if not norms:
        return ""

    # Prefer assessor-style 13-digit roots with year 2025/2026/2027
    def score(val: str) -> Tuple[int, int]:
        root = val.split(".", 1)[0]
        year = int(root[:4]) if root[:4].isdigit() else 0
        year_ok = 1 if 2024 <= year <= 2027 else 0
        # Prefer longer roots (13 typical)
        return (year_ok, len(root), -abs(len(root) - 13))

    ranked = sorted(set(norms), key=score, reverse=True)

    # If top has bad year but another is within hamming≤3 with good year, prefer good year
    top = ranked[0]
    top_root = top.split(".", 1)[0]
    if not (2024 <= int(top_root[:4] or 0) <= 2027):
        for alt in ranked[1:]:
            alt_root = alt.split(".", 1)[0]
            if len(alt_root) == len(top_root) and _hamming(alt_root, top_root) <= 4:
                if 2024 <= int(alt_root[:4] or 0) <= 2027:
                    return alt
    # Majority vote on roots when multiple near-duplicates
    roots = [n.split(".", 1)[0] for n in norms]
    common = Counter(roots).most_common(1)
    if common and common[0][1] >= 2:
        root = common[0][0]
        # Prefer version with suffix if any
        for n in norms:
            if n.startswith(root):
                if "." in n:
                    return n
        return root
    return ranked[0]


def collect_claim_candidates(text: str) -> List[str]:
    found: List[str] = []
    for m in re.finditer(
        r"(?:claim\s*(?:incident|number|no\.?)|sub\s*claim)\s*[:.]?\s*"
        r"(\d{10,16}(?:\.[A-Za-z0-9]{1,4})?)",
        text or "",
        re.I,
    ):
        found.append(m.group(1))
    # Bare 13-digit sequences that look like IFFCO claims (start 20xx)
    for m in re.finditer(r"\b(20\d{11})(?:\.[A-Za-z0-9]{1,4})?\b", text or ""):
        found.append(m.group(0))
    return found


def collect_policy_candidates(text: str) -> List[str]:
    found: List[str] = []
    for m in re.finditer(r"\b(H[A-Z0-9Il]{6,12})\b", text or "", re.I):
        fixed = normalize_policy_number(m.group(1))
        if fixed:
            found.append(fixed)
    return found


def validate_ids_from_corpus(
    corpus_text: str,
    assessor: Optional[Dict] = None,
    current_claim: str = "",
    current_policy: str = "",
) -> Dict[str, str]:
    """Return validated claim_incident_number and policy_number."""
    assessor = assessor or {}
    claim_cands = collect_claim_candidates(corpus_text)
    if assessor.get("claim_number"):
        claim_cands.insert(0, str(assessor["claim_number"]))
    if assessor.get("sub_claim_number"):
        # Prefer full sub-claim when present; also add root
        sub = str(assessor["sub_claim_number"])
        claim_cands.insert(0, sub)
        claim_cands.insert(0, sub.split(".", 1)[0])
    if current_claim:
        claim_cands.append(current_claim)

    policy_cands = collect_policy_candidates(corpus_text)
    if assessor.get("policy_number"):
        policy_cands.insert(0, normalize_policy_number(assessor["policy_number"]))
    if current_policy:
        policy_cands.append(normalize_policy_number(current_policy))

    best_claim = repair_claim_ocr_candidates(claim_cands)
    # Prefer assessor claim when years match recent window
    if assessor.get("claim_number"):
        a = normalize_claim_incident(str(assessor["claim_number"]))
        if a and (not best_claim or a.split(".")[0][:4] in {"2024", "2025", "2026", "2027"}):
            # If current/best looks like OCR corruption of assessor (hamming≤4), take assessor
            if not best_claim:
                best_claim = a
            else:
                br, ar = best_claim.split(".", 1)[0], a.split(".", 1)[0]
                if len(br) == len(ar) and _hamming(br, ar) <= 4:
                    best_claim = a
                elif ar[:4] in {"2025", "2026", "2027"} and br[:4] not in {"2025", "2026", "2027"}:
                    best_claim = a

    best_policy = ""
    if policy_cands:
        # Prefer H1####### length 8
        scored = sorted(
            {normalize_policy_number(p) for p in policy_cands if normalize_policy_number(p)},
            key=lambda p: (1 if re.fullmatch(r"H\d{7}", p) else 0, len(p)),
            reverse=True,
        )
        best_policy = scored[0] if scored else ""

    # Assessor claimed amount hint
    claimed = str(assessor.get("claimed_amount") or "").strip()

    return {
        "claim_incident_number": best_claim,
        "policy_number": best_policy,
        "claimed_amount": claimed,
    }
