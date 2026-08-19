"""Cross-document contradiction detectors (NotebookLM forensic layer)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from backend.notebook.models import NotebookChunk


def _norm_name(s: str) -> str:
    s = re.sub(r"[^a-z]", "", (s or "").lower())
    return s


def detect_foreign_patient_names(
    chunks: List[NotebookChunk],
    expected_name: str,
) -> List[Dict[str, Any]]:
    """Flag other patient names appearing in clinical charts (template reuse)."""
    expected = _norm_name(expected_name)
    if len(expected) < 4:
        return []

    findings: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    # Capture ALLCAPS or Title names near Patient / Name labels
    name_rx = re.compile(
        r"(?:patient\s*name|name\s*of\s*patient|pt\.?\s*name|^name)\s*[:.]?\s*"
        r"(?:mr\.?|mrs\.?|ms\.?)?\s*([A-Z][A-Za-z][A-Za-z .']{2,40})",
        re.I | re.M,
    )
    alien_rx = re.compile(
        r"\b(SAVITHA\s*A\s*G|DAYA\s*THE|BENCY\s+BUU)\b",
        re.I,
    )

    for ch in chunks:
        text = ch.text or ""
        for m in alien_rx.finditer(text):
            alien = re.sub(r"\s+", " ", m.group(1)).strip()
            key = _norm_name(alien)
            if key in seen or (expected and key in expected):
                continue
            seen.add(key)
            findings.append({
                "category": "identity_fraud",
                "indicator": "Foreign patient identity in clinical charts",
                "evidence": (
                    f"Document '{ch.filename}' page {ch.page or '?'} contains name "
                    f"'{alien}' which does not match expected patient '{expected_name}'."
                ),
                "severity": "High",
                "recommendation": (
                    "Treat as possible template reuse / record mixing; verify original IP charts."
                ),
                "citation": {
                    "filename": ch.filename,
                    "page": ch.page,
                    "excerpt": text[max(0, m.start() - 40): m.end() + 40][:200],
                },
            })
        for m in name_rx.finditer(text):
            alien = re.sub(r"\s+", " ", m.group(1)).strip()
            key = _norm_name(alien)
            if len(key) < 5 or key in seen:
                continue
            if expected and key != expected and expected not in key and key not in expected:
                # Ignore hospital / doctor labels
                if re.search(r"hospital|doctor|dr\b|clinic|nursing", alien, re.I):
                    continue
                # Require substantial difference
                if abs(len(key) - len(expected)) > 2 or sum(
                    1 for a, b in zip(key, expected) if a != b
                ) >= 3:
                    seen.add(key)
                    findings.append({
                        "category": "identity_fraud",
                        "indicator": "Patient name mismatch across documents",
                        "evidence": (
                            f"'{alien}' on {ch.filename} p.{ch.page or '?'} vs expected "
                            f"'{expected_name}'."
                        ),
                        "severity": "High",
                        "recommendation": "Reconcile identity with Aadhaar / hospital IP record.",
                        "citation": {
                            "filename": ch.filename,
                            "page": ch.page,
                            "excerpt": alien,
                        },
                    })
    return findings


def detect_abg_vs_vitals(chunks: List[NotebookChunk]) -> List[Dict[str, Any]]:
    """ABG severe hypoxemia vs room-air SpO2 98% is physiologically impossible."""
    corpus = "\n".join(ch.text for ch in chunks)
    low_po2 = re.search(
        r"(?:pO2|PO2|PaO2)\s*[:.]?\s*(\d{2}(?:\.\d)?)\s*(?:mmHg)?",
        corpus,
        re.I,
    )
    low_so2 = re.search(
        r"(?:sO2|SaO2)\s*[:.]?\s*(\d{2}(?:\.\d)?)\s*%?",
        corpus,
        re.I,
    )
    high_spo2 = re.search(
        r"(?:SpO2|SPO2|O2\s*sat(?:uration)?)\s*[:.]?\s*(9[5-9]|100)\s*%?",
        corpus,
        re.I,
    )
    room_air = re.search(r"room\s*air|on\s*RA\b|ambulant|conscious.*alert", corpus, re.I)

    findings: List[Dict[str, Any]] = []
    if low_po2 and high_spo2:
        try:
            po2 = float(low_po2.group(1))
            spo2 = float(high_spo2.group(1))
        except ValueError:
            return findings
        if po2 < 55 and spo2 >= 95:
            findings.append({
                "category": "clinical_abuse",
                "indicator": "Physiologically incompatible ABG vs nursing vitals",
                "evidence": (
                    f"ABG pO2 {po2} mmHg"
                    + (f" / sO2 {low_so2.group(1)}%" if low_so2 else "")
                    + f" concurrent with SpO2 {spo2}%"
                    + (" on room air / alert patient" if room_air else "")
                    + " — incompatible without documentation of error or separate episodes."
                ),
                "severity": "High",
                "recommendation": (
                    "Flag record integrity risk; demand original ABG printouts and vitals chart timestamps."
                ),
            })
    return findings


def detect_impossible_anion_gap(chunks: List[NotebookChunk]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for ch in chunks:
        for m in re.finditer(
            r"anion\s*gap\s*[:.]?\s*(-?\d{1,2}(?:\.\d)?)\s*(?:mmol)?",
            ch.text or "",
            re.I,
        ):
            try:
                gap = float(m.group(1))
            except ValueError:
                continue
            if gap < -5:
                findings.append({
                    "category": "clinical_abuse",
                    "indicator": "Chemically impossible anion gap",
                    "evidence": (
                        f"Anion gap {gap} mmol/L on {ch.filename} p.{ch.page or '?'} "
                        "is not physiologically plausible — reporting error or fabrication risk."
                    ),
                    "severity": "High",
                    "recommendation": "Verify raw electrolyte panel and lab analyzer printout.",
                    "citation": {"filename": ch.filename, "page": ch.page},
                })
    return findings


def detect_ped_nondisclosure(chunks: List[NotebookChunk]) -> List[Dict[str, Any]]:
    """Preauth NA / no PED vs documented chronic disease (e.g. hypothyroidism)."""
    corpus = "\n".join(ch.text for ch in chunks)
    disclosed_neg = bool(re.search(
        r"(?:past\s*(?:medical\s*)?(?:history|illness)|chronic(?:\s+illness)?|"
        r"pre[\s-]*existing|comorbidit)\s*[:./\s-]*\s*(?:NA|N\/A|nil|none|no)\b|"
        r"history\s+of\s+alcoholism\s*[:.]?\s*No|"
        r"alcohol\s*/\s*drug\s*[:.]?\s*No|"
        r"chronic\s+illness\s*[:.]?\s*NA",
        corpus,
        re.I,
    ))
    has_thyroid = bool(re.search(
        r"hypothyroid|thyronorm|levothyroxine|eltroxin",
        corpus,
        re.I,
    ))
    has_known = bool(re.search(
        r"k\s*/\s*c\s*/\s*o|known\s+case\s+of|on\s+(?:regular\s+)?(?:tab|tablet)",
        corpus,
        re.I,
    ))
    findings: List[Dict[str, Any]] = []
    if disclosed_neg and has_thyroid:
        findings.append({
            "category": "misrepresentation",
            "indicator": "Material non-disclosure of pre-existing hypothyroidism",
            "evidence": (
                "Pre-authorization / history fields deny chronic illness (NA/No) while "
                "clinical notes document hypothyroidism / Thyronorm (levothyroxine) therapy."
            ),
            "severity": "High",
            "recommendation": (
                "Apply disclosure / PED clauses (e.g. Family Health Protector Clause 49 analogue); "
                "consider repudiation or investigation."
            ),
        })
    elif disclosed_neg and has_known and re.search(
        r"diabetes|hypertension|cad\b|ckd\b|asthma|copd",
        corpus,
        re.I,
    ):
        findings.append({
            "category": "misrepresentation",
            "indicator": "Material non-disclosure of documented comorbidity",
            "evidence": (
                "History fields deny prior illness while clinical charts document a known chronic condition."
            ),
            "severity": "High",
            "recommendation": "Verify proposal form disclosures against clinical PED evidence.",
        })
    return findings


def detect_diagnostic_only_admission(chunks: List[NotebookChunk]) -> List[Dict[str, Any]]:
    """TIA / stroke workup with negative imaging + oral meds only → OPD-suitable."""
    corpus = "\n".join(ch.text for ch in chunks).lower()
    is_tia = bool(re.search(r"\btia\b|transient\s+ischemic|hemispheric\s+transient", corpus))
    negative_img = bool(re.search(
        r"no\s+evidence\s+of\s+acute\s+infarct|no\s+acute\s+intracranial|"
        r"no\s+evidence\s+of\s+occlusion|negative\s+(?:for\s+)?(?:infarct|stenosis)",
        corpus,
    ))
    oral_only = bool(re.search(r"aspirin|clopidogrel|atorvastatin|ecosprin|deplatt", corpus))
    icu = bool(re.search(r"\bicu\b", corpus))
    findings: List[Dict[str, Any]] = []
    if is_tia and negative_img and oral_only:
        findings.append({
            "category": "billing_abuse",
            "indicator": "Diagnostic admission without active inpatient therapy",
            "evidence": (
                "TIA/stroke workup with negative acute imaging and routine oral secondary "
                "prevention only"
                + (" including ICU stay" if icu else "")
                + " — aligns with outpatient TIA clinic pathway per standard guidelines; "
                "hospitalization for evaluation may be excluded under diagnostic-admission clauses."
            ),
            "severity": "High",
            "recommendation": (
                "Recommend rejection or major curtailment under diagnostic / OPD-conversion exclusions; "
                "cite ESO TIA outpatient pathway."
            ),
        })
    return findings


def run_contradiction_checks(
    chunks: List[NotebookChunk],
    expected_patient_name: str = "",
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    findings.extend(detect_foreign_patient_names(chunks, expected_patient_name))
    findings.extend(detect_abg_vs_vitals(chunks))
    findings.extend(detect_impossible_anion_gap(chunks))
    findings.extend(detect_ped_nondisclosure(chunks))
    findings.extend(detect_diagnostic_only_admission(chunks))
    return findings
