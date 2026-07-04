"""Gate audits only when selected guidelines are clearly unrelated to the case.

Designed to be permissive: prefer running the audit over false blocks.
Only hard-stop when the guideline topic is absent from the case AND the
primary diagnosis clearly belongs to a different specialty.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


# Specialty → keywords used for guideline filenames and case matching.
_SPECIALTY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cardiology": (
        "cardio", "cardiac", "coronary", "angina", "acs", "myocardial",
        "cabg", "pci", "stent", "heart", "troponin", "unstable angina",
        "stemi", "nstemi", "arrhythmia", "triple vessel", "ihd",
        "ischemic heart", "ischaemic heart",
    ),
    "neurology": (
        "neuro", "trigeminal", "neuralgia", "stroke", "cva", "seizure",
        "epilepsy", "parkinson", "migraine", "meningitis", "mvd",
        "neurovascular", "intracranial",
    ),
    "endocrinology": (
        "hypoglycemia", "hypoglycaemia", "hypoglyc", "diabetes", "diabetic",
        "insulin", "thyroid", "endocrine", "hba1c", "ketoacidosis",
        "hyperglycemia", "hyperglycaemia",
    ),
    "pulmonology": (
        "copd", "asthma", "pneumonia", "pulmonary", "spirometry",
        "bronchitis", "respiratory failure", "ards",
    ),
    "orthopedics": (
        "ortho", "fracture", "arthroscopy", "bankart", "ligament",
        "orthopaedic", "orthopedic", "joint replacement",
    ),
    "gastroenterology": (
        "gastro", "hepatic", "pancrea", "gi bleed", "cholecyst",
        "appendic", "cirrhosis", "hepatitis",
    ),
    "nephrology": (
        "nephro", "dialysis", "ckd", "aki", "renal failure", "kidney failure",
    ),
    "oncology": (
        "cancer", "carcinoma", "oncolog", "chemotherapy", "malignan", "metastas",
    ),
    "infectious_disease": (
        "sepsis", "malaria", "dengue", "typhoid", "tuberculosis", "covid",
    ),
}

# Strong primary-diagnosis only keywords (avoid comorbidity noise).
_PRIMARY_DIAGNOSIS_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cardiology": (
        "coronary", "angina", "acs", "myocardial", "cabg", "pci", "stent",
        "unstable angina", "stemi", "nstemi", "triple vessel", "ihd",
        "ischemic heart", "ischaemic heart", "cad",
    ),
    "neurology": (
        "trigeminal", "neuralgia", "stroke", "cva", "seizure", "epilepsy",
        "parkinson", "meningitis", "neurovascular",
    ),
    "endocrinology": (
        "hypoglycemia", "hypoglycaemia", "hypoglyc", "diabetic ketoacidosis",
        "dka", "hyperglycemia", "hyperglycaemia", "thyroid storm",
    ),
    "pulmonology": (
        "copd", "asthma", "pneumonia", "ards", "respiratory failure",
    ),
    "orthopedics": (
        "fracture", "arthroscopy", "bankart", "joint replacement",
    ),
    "gastroenterology": (
        "gi bleed", "cholecyst", "appendic", "cirrhosis", "pancreatitis",
    ),
    "nephrology": (
        "dialysis", "ckd", "aki", "renal failure", "kidney failure",
    ),
    "oncology": (
        "cancer", "carcinoma", "chemotherapy", "malignan", "metastas",
    ),
    "infectious_disease": (
        "sepsis", "malaria", "dengue", "typhoid", "tuberculosis", "covid",
    ),
}


class GuidelineMismatchError(RuntimeError):
    """Raised when selected guidelines do not align with the clinical case."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _detect(text: str, table: Dict[str, Tuple[str, ...]]) -> Set[str]:
    blob = _norm(text)
    if not blob:
        return set()
    found: Set[str] = set()
    for specialty, keywords in table.items():
        for kw in keywords:
            if kw in blob:
                found.add(specialty)
                break
    return found


def detect_specialties(text: str) -> Set[str]:
    return _detect(text, _SPECIALTY_KEYWORDS)


def specialties_from_guidelines(guideline_names: List[str]) -> Set[str]:
    found: Set[str] = set()
    for name in guideline_names or []:
        found |= detect_specialties(name)
        low = _norm(name)
        if "hypoglyc" in low or "diabetes" in low:
            found.add("endocrinology")
        if "coronary" in low or "cardiac" in low or "heart" in low or "acs" in low:
            found.add("cardiology")
        if "neuralgia" in low or "trigeminal" in low or "stroke" in low or "neuro" in low:
            found.add("neurology")
        if "copd" in low or "asthma" in low or "pneumonia" in low or "respiratory" in low:
            found.add("pulmonology")
    return found


def _primary_case_specialties(
    case_profile: Optional[dict],
    claim_diagnosis: str = "",
) -> Set[str]:
    """Use diagnosis / procedures only — not full OCR case text."""
    profile = case_profile or {}
    procedures = profile.get("procedures") or []
    if isinstance(procedures, list):
        proc_text = " ".join(str(p) for p in procedures)
    else:
        proc_text = str(procedures)
    primary_blob = " ".join([
        str(profile.get("diagnosis") or ""),
        proc_text,
        claim_diagnosis or "",
    ])
    return _detect(primary_blob, _PRIMARY_DIAGNOSIS_KEYWORDS)


def _guideline_topic_present_in_case(
    guide_specs: Set[str],
    case_profile: Optional[dict],
    case_text: str,
    claim_diagnosis: str,
) -> bool:
    """True if any keyword for the selected guideline specialty appears in the case."""
    profile = case_profile or {}
    procedures = profile.get("procedures") or []
    if isinstance(procedures, list):
        proc_text = " ".join(str(p) for p in procedures)
    else:
        proc_text = str(procedures)
    blob = _norm(" ".join([
        str(profile.get("diagnosis") or ""),
        proc_text,
        claim_diagnosis or "",
        (case_text or "")[:12000],
    ]))
    for specialty in guide_specs:
        for kw in _SPECIALTY_KEYWORDS.get(specialty, ()):
            if kw in blob:
                return True
    return False


def check_guideline_alignment(
    guideline_names: List[str],
    case_profile: Optional[dict],
    case_text: str = "",
    claim_diagnosis: str = "",
) -> Dict[str, Any]:
    """Return alignment result. Default is aligned=True (permissive)."""
    guide_specs = specialties_from_guidelines(guideline_names)

    # Unknown / generic guideline filename — never block.
    if not guide_specs:
        return {
            "aligned": True,
            "case_specialties": [],
            "guideline_specialties": [],
            "message": "",
            "reason": "guideline_unclassified",
        }

    # If guideline topic appears anywhere in the case, allow.
    if _guideline_topic_present_in_case(
        guide_specs, case_profile, case_text, claim_diagnosis
    ):
        return {
            "aligned": True,
            "case_specialties": sorted(
                _primary_case_specialties(case_profile, claim_diagnosis) | guide_specs
            ),
            "guideline_specialties": sorted(guide_specs),
            "overlap": sorted(guide_specs),
            "message": "",
            "reason": "guideline_topic_found_in_case",
        }

    # Guideline topic not found — only block if primary diagnosis is a *different* specialty.
    case_primary = _primary_case_specialties(case_profile, claim_diagnosis)
    if not case_primary:
        return {
            "aligned": True,
            "case_specialties": [],
            "guideline_specialties": sorted(guide_specs),
            "message": "",
            "reason": "case_primary_unclassified",
        }

    overlap = case_primary & guide_specs
    if overlap:
        return {
            "aligned": True,
            "case_specialties": sorted(case_primary),
            "guideline_specialties": sorted(guide_specs),
            "overlap": sorted(overlap),
            "message": "",
            "reason": "primary_overlap",
        }

    case_label = ", ".join(sorted(case_primary))
    guide_label = ", ".join(sorted(guide_specs))
    guidelines = "; ".join(guideline_names)
    message = (
        f"Selected guideline(s) do not match the clinical case. "
        f"Case primary diagnosis appears related to: {case_label}. "
        f"Selected guideline(s) ({guidelines}) appear related to: {guide_label}. "
        f"Please select an appropriate clinical guideline and run the audit again."
    )
    return {
        "aligned": False,
        "case_specialties": sorted(case_primary),
        "guideline_specialties": sorted(guide_specs),
        "overlap": [],
        "message": message,
        "guidelines": guideline_names,
        "reason": "clear_mismatch",
    }


def assert_guideline_alignment(
    guideline_names: List[str],
    case_profile: Optional[dict],
    case_text: str = "",
    claim_diagnosis: str = "",
) -> Dict[str, Any]:
    result = check_guideline_alignment(
        guideline_names, case_profile, case_text, claim_diagnosis
    )
    if not result.get("aligned"):
        raise GuidelineMismatchError(result["message"], details=result)
    return result
