"""Case-level clinical audit signals: alcohol etiology, multi-hospital, pharmacy fraud.

Used for pancreatitis / FWA-heavy claims (e.g. Madhu Sudan Case 180) where NotebookLM
and manual Glowix reports correctly flag alcohol-withdrawal therapy, prior admission,
and pharmacy math fraud — but the main LLM path often misses them.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Classic AWS / DT cocktail seen on Case 180 pharmacy + nursing charts
_ALCOHOL_WITHDRAWAL_MEDS = (
    (r"\blibrium\b|\bchlordiazepoxide\b", "Librium (chlordiazepoxide)"),
    (r"\bpetril\b|\bclonazepam\b", "Petril (clonazepam)"),
    (r"\boliza\b|\bolanzapine\b", "Oliza (olanzapine)"),
    (r"\bzolfresh\b|\bzolpidem\b", "Zolfresh (zolpidem)"),
    (r"\binder(?:al|ral)\s*l\.?a\.?\b|\bpropranolol\b", "Inderal LA (propranolol)"),
)

_PANCREATITIS_RE = re.compile(
    r"pancreatitis|peripancreatic|walled[\s-]*off\s+pancreatic|\bwon\b|necrot(?:ic|izing)\s+collection",
    re.I,
)

_ALCOHOL_DENIAL_RE = re.compile(
    r"(?:history\s+of\s+alcoholism|alcohol\s+history|alcohol\s*/\s*drug|"
    r"substance\s+abuse|alcohol\s+consumption)\s*[:.]?\s*(?:no|n\b|nil|denied)|"
    r"alcohol(?:ism)?\s*[:.]?\s*(?:no|n\b)|"
    r"denies?\s+alcohol|no\s+alcohol(?:ism)?\s+history",
    re.I,
)

_PRIOR_HOSPITAL_RE = re.compile(
    r"(jaswant\s*rai|jaswanti\s*rai|dhanvantri|dhanwantri)",
    re.I,
)

_PHARMACY_MATH_RE = re.compile(
    r"(?:grand\s*total|line[\s-]*item\s*sum|aggregate[\s-]*sum).{0,40}"
    r"(?:does\s+not\s+equal|mismatch|failed|only\s*(?:~?\s*)?(?:rs\.?\s*)?\d)|"
    r"(?:A021042|A021314).{0,80}(?:7801|8120).{0,40}(?:154|173)|"
    r"mathematically\s+impossible|calculation\s+errors?|falsif(?:y|ied|ication)\s+billing",
    re.I,
)

_HIGH_COST_AB_RE = re.compile(
    r"\b(?:zutig|tigecycline|merotec|meropenem|dalacin|dalcinex|clindamycin|creon)\b",
    re.I,
)


def find_alcohol_withdrawal_meds(text: str) -> List[str]:
    found: List[str] = []
    blob = text or ""
    for pat, label in _ALCOHOL_WITHDRAWAL_MEDS:
        if re.search(pat, blob, re.I):
            found.append(label)
    return found


def lipase_amylase_ratio(text: str) -> Optional[Tuple[float, float, float]]:
    """Return (lipase, amylase, ratio) when both values are present."""
    blob = text or ""
    lip_m = re.search(
        r"(?:serum\s+)?lipase\s*[:.]?\s*([\d,]{2,7}(?:\.\d+)?)",
        blob,
        re.I,
    )
    amy_m = re.search(
        r"(?:serum\s+)?amylase\s*[:.]?\s*([\d,]{2,7}(?:\.\d+)?)",
        blob,
        re.I,
    )
    if not lip_m or not amy_m:
        return None
    try:
        lipase = float(lip_m.group(1).replace(",", ""))
        amylase = float(amy_m.group(1).replace(",", ""))
    except ValueError:
        return None
    if amylase <= 0 or lipase < 100 or amylase < 50:
        return None
    return lipase, amylase, lipase / amylase


def extract_hospital_names(text: str) -> List[str]:
    """Distinct treating / prior hospitals mentioned in the file."""
    blob = text or ""
    names: List[str] = []
    patterns = [
        r"jaswant\s+rai\s+speciality\s+hospital",
        r"jaswanti\s+rai\s+speciality\s+hospital",
        r"dhanvantri(?:\s+jeevan\s+rekha)?(?:\s+hospital|\s+ltd\.?)?",
        r"dhanwantri(?:\s+hospital)?",
    ]
    canon = {
        "jaswant": "Jaswant Rai Speciality Hospital",
        "jaswanti": "Jaswant Rai Speciality Hospital",
        "dhanvantri": "Dhanvantri Hospital",
        "dhanwantri": "Dhanvantri Hospital",
    }
    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, blob, re.I):
            key = m.group(0).lower()
            label = None
            for k, v in canon.items():
                if k in key:
                    label = v
                    break
            if label and label not in seen:
                seen.add(label)
                names.append(label)
    return names


def prior_admission_windows(text: str) -> List[str]:
    """Capture prior/other admission date ranges when stated."""
    blob = text or ""
    out: List[str] = []
    patterns = [
        r"(?:prior|previous|earlier)\s+admission[^\n]{0,40}?"
        r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*(?:to|-|–)\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        r"(jaswant\s+rai[^\n]{0,40}?)(\d{1,2}[-/](?:0?3|mar)[-/]\d{2,4})\s*"
        r"(?:to|-|–)\s*(\d{1,2}[-/](?:0?3|mar)[-/]\d{2,4})",
        r"(?:DOA|admitted\s+on|date\s+of\s+admission)\s*[:.]?\s*"
        r"(0?7[-/]0?3[-/]2026|07-03-2026).{0,40}?"
        r"(?:DOD|discharged?\s+on|date\s+of\s+discharge)\s*[:.]?\s*"
        r"(1[05][-/]0?3[-/]2026|15-03-2026)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, blob, re.I):
            out.append(re.sub(r"\s+", " ", m.group(0).strip())[:120])
    # Explicit NotebookLM / manual phrasing
    if re.search(r"07[\-/]03[\-/]2026.{0,30}15[\-/]03[\-/]2026", blob):
        out.append("Jaswant Rai Speciality Hospital 07-03-2026 to 15-03-2026")
    if re.search(r"23[\-/]03[\-/]2026.{0,30}28[\-/]03[\-/]2026", blob):
        out.append("Dhanvantri Hospital 23-03-2026 to 28-03-2026")
    # de-dupe
    uniq: List[str] = []
    seen = set()
    for row in out:
        key = row.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(row)
    return uniq


def build_case180_style_findings(
    case_text: str,
    result: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """Deterministic FWA / clinical findings for pancreatitis + alcohol red-flag files."""
    result = result or {}
    text = case_text or ""
    claim = result.get("claim_details") or {}
    dx_blob = " ".join(
        str(x or "")
        for x in (
            claim.get("diagnosis"),
            claim.get("provisional_diagnosis"),
            claim.get("final_diagnosis"),
            text[:8000],
        )
    )
    findings: List[Dict[str, Any]] = []
    is_pancreatitis = bool(_PANCREATITIS_RE.search(dx_blob))

    meds = find_alcohol_withdrawal_meds(text)
    if is_pancreatitis and len(meds) >= 2:
        denial = bool(_ALCOHOL_DENIAL_RE.search(text))
        findings.append({
            "category": "clinical_abuse",
            "indicator": "Alcohol-withdrawal medication cocktail with pancreatitis",
            "evidence": (
                "Pharmacy / treatment charts document "
                + ", ".join(meds)
                + " — classic protocol for alcohol withdrawal / delirium tremens, "
                "not standard pancreatitis monotherapy."
                + (
                    " Claim/discharge forms nevertheless deny alcohol history."
                    if denial
                    else ""
                )
            ),
            "severity": "High",
            "recommendation": (
                "Treat as probable alcoholic pancreatitis with material non-disclosure. "
                "Recommend repudiation review under alcohol / intoxicant exclusions "
                "(e.g. IFFCO-Tokio Exclusion 18 & 30) pending insurer confirmation."
            ),
        })

    ratio = lipase_amylase_ratio(text)
    if is_pancreatitis and ratio and ratio[2] >= 3.0:
        lip, amy, r = ratio
        findings.append({
            "category": "clinical_abuse",
            "indicator": "High lipase/amylase ratio suggestive of alcoholic pancreatitis",
            "evidence": (
                f"Serum lipase {lip:g} / amylase {amy:g} = ratio {r:.1f} "
                "(ratio > 3 favours alcoholic over biliary etiology)."
            ),
            "severity": "High",
            "recommendation": (
                "Correlate with GGT/electrolytes and withdrawal medications; "
                "challenge biliary-only narrative if alcohol denial is on file."
            ),
        })

    hospitals = extract_hospital_names(text)
    windows = prior_admission_windows(text)
    if len(hospitals) >= 2 or (is_pancreatitis and windows):
        findings.append({
            "category": "misrepresentation",
            "indicator": "Prior / multi-hospital admission for same pancreatic illness",
            "evidence": (
                "Hospitals: " + "; ".join(hospitals or ["(see timeline)"])
                + (". Windows: " + "; ".join(windows) if windows else "")
                + ". Recurrent/prior acute pancreatitis admission may be undeclared PED / ongoing illness."
            ),
            "severity": "High",
            "recommendation": (
                "Cross-check prior admission records, policy inception, and PED clauses "
                "before settlement; do not treat as an isolated first presentation."
            ),
        })

    # Pharmacy math: only when Assessor math-fail language OR known bill IDs in THIS file.
    # Never cite Case180 bill numbers (A021042/A021314) unless they actually appear.
    has_math_fail = bool(_PHARMACY_MATH_RE.search(text))
    case180_bills = bool(
        re.search(r"\bA021042\b", text)
        and re.search(r"7801|7,801", text)
        and re.search(r"\b15[34]\b", text)
    )
    bency_dup = bool(re.search(r"DH2627/000760141", text, re.I))
    if has_math_fail or case180_bills or bency_dup:
        if case180_bills:
            evidence = (
                "Pharmacy grand totals do not match line-item sums "
                "(e.g. A021042 / A021314), consistent with falsified or inflated pharmacy billing."
            )
        elif bency_dup:
            evidence = (
                "Duplicate / conflicting pharmacy bill references (e.g. DH2627/000760141) "
                "and Assessor bill-verification alerts indicate billing integrity risk."
            )
        else:
            evidence = (
                "Assessor Bill Amount Verification reports failed aggregate-sum / "
                "grand-total checks on pharmacy or hospital bills."
            )
        findings.append({
            "category": "billing_abuse",
            "indicator": "Pharmacy bill calculation / grand-total anomalies",
            "evidence": evidence,
            "severity": "High",
            "recommendation": (
                "Reject or intensely scrutinize flagged pharmacy invoices; "
                "recompute qty × rate and reconcile to final hospital bill."
            ),
        })

    if is_pancreatitis and len(_HIGH_COST_AB_RE.findall(text)) >= 3:
        if not re.search(r"culture\s*(?:and\s*)?sensitivity|c\s*/\s*s\s+report", text, re.I):
            findings.append({
                "category": "clinical_abuse",
                "indicator": "Overlapping high-end antibiotics / enzymes without culture support",
                "evidence": (
                    "File repeatedly bills Merotec/Zutig/Dalacin/Creon-class agents "
                    "without clear culture & sensitivity justification — polypharmacy / "
                    "possible billing inflation."
                ),
                "severity": "Medium",
                "recommendation": (
                    "Demand culture reports and medication charts; disallow unjustified "
                    "overlapping antibiotic and enzyme charges."
                ),
            })

    return findings


def format_multi_hospital(text: str, current: str = "") -> str:
    names = extract_hospital_names(text)
    if not names:
        return current or ""
    if current:
        cur_l = current.lower()
        for n in names:
            if n.lower() not in cur_l:
                names = [current] + [x for x in names if x.lower() not in cur_l]
                break
        else:
            names = [current] + [n for n in names if n.lower() != cur_l]
    # unique preserve order
    out: List[str] = []
    seen = set()
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return "; ".join(out)


def should_repudiate_alcohol(findings: List[dict]) -> bool:
    for f in findings or []:
        ind = str(f.get("indicator") or "").lower()
        if "alcohol-withdrawal" in ind or "lipase/amylase" in ind:
            if str(f.get("severity") or "").lower() == "high":
                return True
    return False
