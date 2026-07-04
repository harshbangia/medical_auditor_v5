"""Gate audits when selected clinical guidelines do not match the case diagnosis."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


# Specialty buckets: keywords that appear in diagnosis / case text / guideline names.
_SPECIALTY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cardiology": (
        "cardio", "cardiac", "coronary", "angina", "acs", "mi ", "myocardial",
        "cabg", "pci", "stent", "heart", "ecg", "echo", "troponin", "cad",
        "unstable angina", "stemi", "nstemi", "arrhythmia", "hypertension",
        "chest pain", "triple vessel", "lad ", "lcx", "rca",
    ),
    "neurology": (
        "neuro", "trigeminal", "neuralgia", "stroke", "cva", "seizure",
        "epilepsy", "brain", "mri brain", "mvd", "parkinson", "migraine",
        "neuropathy", "meningitis",
    ),
    "endocrinology": (
        "hypoglycemia", "hypoglycaemia", "diabetes", "diabetic", "insulin",
        "thyroid", "endocrine", "glucose", "hba1c", "ketoacidosis",
    ),
    "pulmonology": (
        "copd", "asthma", "pneumonia", "respiratory", "pulmonary", "lung",
        "hrct", "spirometry", "bronchitis", "dyspnea", "breathlessness",
    ),
    "orthopedics": (
        "ortho", "fracture", "joint", "knee", "hip", "shoulder", "spine",
        "arthroscopy", "bankart", "ligament", "orthopaedic", "orthopedic",
    ),
    "gastroenterology": (
        "gastro", "liver", "hepatic", "pancrea", "ulcer", "gi bleed",
        "cholecyst", "appendic", "abdomen", "abdominal",
    ),
    "nephrology": (
        "renal", "kidney", "nephro", "dialysis", "ckd", "aki", "creatinine",
    ),
    "oncology": (
        "cancer", "carcinoma", "tumor", "tumour", "oncolog", "chemotherapy",
        "malignan", "metastas",
    ),
    "infectious_disease": (
        "sepsis", "infection", "fever", "malaria", "dengue", "typhoid",
        "tuberculosis", "tb ", "covid", "culture",
    ),
    "general": (
        "general medicine", "internal medicine", "icu", "emergency",
        "observation", "medical management",
    ),
}


class GuidelineMismatchError(RuntimeError):
    """Raised when selected guidelines do not align with the clinical case."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def detect_specialties(text: str) -> Set[str]:
    blob = _norm(text)
    found: Set[str] = set()
    for specialty, keywords in _SPECIALTY_KEYWORDS.items():
        if specialty == "general":
            continue
        for kw in keywords:
            if kw in blob:
                found.add(specialty)
                break
    return found


def specialties_from_profile(case_profile: Optional[dict], case_text: str = "") -> Set[str]:
    profile = case_profile or {}
    parts = [
        str(profile.get("diagnosis") or ""),
        " ".join(profile.get("procedures") or []) if isinstance(profile.get("procedures"), list) else str(profile.get("procedures") or ""),
        " ".join(profile.get("imaging") or []) if isinstance(profile.get("imaging"), list) else "",
        case_text[:8000],
    ]
    return detect_specialties(" ".join(parts))


def specialties_from_guidelines(guideline_names: List[str]) -> Set[str]:
    found: Set[str] = set()
    for name in guideline_names or []:
        found |= detect_specialties(name)
        # Filename heuristics
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


def check_guideline_alignment(
    guideline_names: List[str],
    case_profile: Optional[dict],
    case_text: str = "",
    claim_diagnosis: str = "",
) -> Dict[str, Any]:
    """Return alignment result. aligned=False means audit must not proceed."""
    case_specs = specialties_from_profile(case_profile, case_text)
    if claim_diagnosis:
        case_specs |= detect_specialties(claim_diagnosis)

    guide_specs = specialties_from_guidelines(guideline_names)

    # If we cannot classify either side, allow (avoid false blocks on rare cases).
    if not case_specs or not guide_specs:
        return {
            "aligned": True,
            "case_specialties": sorted(case_specs),
            "guideline_specialties": sorted(guide_specs),
            "message": "",
        }

    overlap = case_specs & guide_specs
    if overlap:
        return {
            "aligned": True,
            "case_specialties": sorted(case_specs),
            "guideline_specialties": sorted(guide_specs),
            "overlap": sorted(overlap),
            "message": "",
        }

    case_label = ", ".join(sorted(case_specs)) or "unknown"
    guide_label = ", ".join(sorted(guide_specs)) or "unknown"
    guidelines = "; ".join(guideline_names)
    message = (
        f"Selected guideline(s) do not match the clinical case. "
        f"Case appears related to: {case_label}. "
        f"Selected guideline(s) ({guidelines}) appear related to: {guide_label}. "
        f"Please select an appropriate clinical guideline and run the audit again."
    )
    return {
        "aligned": False,
        "case_specialties": sorted(case_specs),
        "guideline_specialties": sorted(guide_specs),
        "overlap": [],
        "message": message,
        "guidelines": guideline_names,
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
