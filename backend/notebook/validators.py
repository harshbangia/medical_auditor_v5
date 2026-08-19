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


def _claim_year_ok(root: str) -> bool:
    try:
        return 2024 <= int(root[:4]) <= 2027
    except ValueError:
        return False


def repair_claim_ocr_candidates(candidates: Iterable[str]) -> str:
    """Pick best claim number; repair common OCR digit swaps when a near-match exists.

    Bency bug: 2020877000347 / 2020877700347 vs true 2026071700347 (year 2020 vs 2026).
    Prefer recent claim years (2024–2027). Never let raw OCR frequency of a bad-year
    id beat a near-duplicate good-year id.
    """
    norms: List[str] = []
    for c in candidates:
        n = normalize_claim_incident(c)
        if n:
            norms.append(n)
    if not norms:
        return ""

    def score(val: str) -> Tuple[int, int, int]:
        root = val.split(".", 1)[0]
        year_ok = 1 if _claim_year_ok(root) else 0
        return (year_ok, len(root), -abs(len(root) - 13))

    unique = list(set(norms))
    good = [n for n in unique if _claim_year_ok(n.split(".", 1)[0])]

    # Drop bad-year ids that are OCR twins of a good-year claim
    if good:
        kept: List[str] = []
        for n in unique:
            root = n.split(".", 1)[0]
            if _claim_year_ok(root):
                kept.append(n)
                continue
            twin = any(
                len(root) == len(g.split(".", 1)[0])
                and _hamming(root, g.split(".", 1)[0]) <= 4
                for g in good
            )
            if not twin:
                kept.append(n)
        unique = kept or list(good)

    allowed_roots = {u.split(".", 1)[0] for u in unique}
    filtered = [n for n in norms if n.split(".", 1)[0] in allowed_roots]
    if not filtered:
        filtered = norms

    roots = [n.split(".", 1)[0] for n in filtered]
    common = Counter(roots).most_common(1)
    if common and common[0][1] >= 2:
        root = common[0][0]
        if not _claim_year_ok(root):
            for g in good:
                gr = g.split(".", 1)[0]
                if len(gr) == len(root) and _hamming(gr, root) <= 4:
                    root = gr
                    break
        for n in filtered:
            if n.split(".", 1)[0] == root and "." in n:
                return n
        return root

    ranked = sorted(unique, key=score, reverse=True)
    return ranked[0]



def repair_policy_ocr_candidates(candidates: Iterable[str], preferred: str = "") -> str:
    """Pick best H####### policy; prefer Assessor / labeled value, then near-duplicate consensus."""
    norms: List[str] = []
    for c in candidates:
        p = normalize_policy_number(c)
        if p and re.match(r"^H\d{5,}$", p):
            norms.append(p)
    if not norms:
        return ""
    pref = normalize_policy_number(preferred) if preferred else ""
    if pref and pref in set(norms):
        return pref
    # Prefer canonical H + 7 digits
    scored = sorted(
        set(norms),
        key=lambda p: (1 if re.fullmatch(r"H\d{7}", p) else 0, Counter(norms)[p], len(p)),
        reverse=True,
    )
    top = scored[0]
    # If preferred is within 1 digit of top, keep preferred (Assessor OCR slightly off vs corpus)
    if pref and re.fullmatch(r"H\d{7}", pref) and re.fullmatch(r"H\d{7}", top):
        if _hamming(pref[1:], top[1:]) <= 1:
            return pref
    # Among H7 twins within hamming ≤1, prefer higher frequency then lexicographically stable Assessor-ish
    for alt in scored[1:]:
        if re.fullmatch(r"H\d{7}", top) and re.fullmatch(r"H\d{7}", alt):
            if _hamming(top[1:], alt[1:]) <= 1 and Counter(norms)[alt] > Counter(norms)[top]:
                top = alt
    return top


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
    admission_date: str = "",
) -> Dict[str, str]:
    """Return validated claim_incident_number, policy_number, claimed_amount via identity seal."""
    from backend.notebook.identity_seal import (
        build_identity_seal,
        parse_admission_yyyymmdd,
    )

    seal = build_identity_seal(
        corpus_text=corpus_text or "",
        assessor=assessor or {},
        admission_yyyymmdd=parse_admission_yyyymmdd(admission_date),
        current_claim=current_claim,
        current_policy=current_policy,
    )
    return {
        "claim_incident_number": seal.claim_incident_number,
        "policy_number": seal.policy_number,
        "claimed_amount": seal.claimed_amount or (
            str((assessor or {}).get("claimed_amount") or "").strip()
        ),
    }
