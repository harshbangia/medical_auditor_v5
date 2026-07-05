"""Gate audits when selected guidelines are clearly unrelated to the case.

Blocks before the LLM audit when the guideline disease/topic and the case
diagnosis point to different conditions (e.g. enteric-fever guideline on an
alcohol case). Still allows the audit when the guideline topic appears in the
uploaded documents (comorbidity / same disease documented elsewhere).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


# Disease-level topics (finer than broad specialty buckets).
# Order: longer phrases first within each tuple where relevant.
_DISEASE_TOPICS: Dict[str, Tuple[str, ...]] = {
    "enteric_fever": (
        "enteric fever", "enteric", "typhoid", "paratyphoid", "salmonella",
    ),
    "alcohol_related": (
        "alcohol dependence", "alcohol withdrawal", "alcohol use disorder",
        "alcoholism", "alcoholic", "delirium tremens", "alcohol",
    ),
    "hypoglycemia": ("hypoglycemia", "hypoglycaemia", "hypoglyc"),
    "coronary_disease": (
        "triple vessel", "unstable angina", "ischemic heart", "ischaemic heart",
        "coronary", "myocardial", "cabg", "angina", "stemi", "nstemi", "cad",
    ),
    "trigeminal_neuralgia": ("trigeminal neuralgia", "trigeminal", "neuralgia"),
    "stroke_neuro": ("stroke", "cva", "seizure", "epilepsy", "meningitis", "mvd"),
    "copd_respiratory": ("copd", "asthma", "pneumonia", "ards", "spirometry"),
    "pancreatitis_gi": (
        "acute pancreatitis", "necrotizing pancreatitis", "pancreatitis",
        "cholecyst", "appendic", "gi bleed",
    ),
    "hepatitis_liver": ("cirrhosis", "hepatitis", "liver failure", "hepatic"),
    "renal": ("dialysis", "renal failure", "kidney failure", "ckd", "aki"),
    "oncology": ("carcinoma", "malignan", "chemotherapy", "metastas", "cancer"),
    "orthopedic": (
        "fracture", "arthroscopy", "bankart", "joint replacement", "ligament",
    ),
    "diabetes_endocrine": (
        "diabetic ketoacidosis", "ketoacidosis", "diabetes", "diabetic", "thyroid",
    ),
    "malaria_dengue": ("malaria", "dengue", "chikungunya"),
    "tuberculosis": ("tuberculosis", " tb ", " mdr tb"),
}

_TOPIC_LABELS: Dict[str, str] = {
    "enteric_fever": "enteric fever / typhoid",
    "alcohol_related": "alcohol-related disorder",
    "hypoglycemia": "hypoglycemia",
    "coronary_disease": "coronary / cardiac disease",
    "trigeminal_neuralgia": "trigeminal neuralgia",
    "stroke_neuro": "neurology / stroke",
    "copd_respiratory": "respiratory disease (COPD / asthma / pneumonia)",
    "pancreatitis_gi": "pancreatitis / GI surgery",
    "hepatitis_liver": "liver / hepatitis / cirrhosis",
    "renal": "renal disease",
    "oncology": "oncology / malignancy",
    "orthopedic": "orthopaedic / fracture",
    "diabetes_endocrine": "diabetes / endocrine",
    "malaria_dengue": "malaria / dengue",
    "tuberculosis": "tuberculosis",
}

# Specialty → keywords used for guideline filenames and legacy case matching.
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
        "appendic", "cirrhosis", "hepatitis", "alcohol", "alcoholic",
        "hemorrhoid", "haemorrhoid", "piles", "anal fissure", "fistula",
        "proctolog", "hernia",
    ),
    "nephrology": (
        "nephro", "dialysis", "ckd", "aki", "renal failure", "kidney failure",
    ),
    "oncology": (
        "cancer", "carcinoma", "oncolog", "chemotherapy", "malignan", "metastas",
    ),
    "infectious_disease": (
        "sepsis", "malaria", "dengue", "typhoid", "tuberculosis", "covid",
        "enteric",
    ),
    "addiction_psychiatry": (
        "alcohol", "alcoholism", "alcohol dependence", "substance", "addiction",
    ),
}

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
        "enteric fever", "enteric",
    ),
    "addiction_psychiatry": (
        "alcohol", "alcoholism", "alcohol dependence", "alcohol withdrawal",
        "substance abuse", "addiction",
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
    for label, keywords in table.items():
        for kw in keywords:
            if kw in blob:
                found.add(label)
                break
    return found


def detect_disease_topics(text: str) -> Set[str]:
    return _detect(text, _DISEASE_TOPICS)


def detect_specialties(text: str) -> Set[str]:
    return _detect(text, _SPECIALTY_KEYWORDS)


def disease_topics_from_guidelines(guideline_names: List[str]) -> Set[str]:
    found: Set[str] = set()
    for name in guideline_names or []:
        found |= detect_disease_topics(name)
    return found


def specialties_from_guidelines(guideline_names: List[str]) -> Set[str]:
    found: Set[str] = set()
    for name in guideline_names or []:
        found |= detect_specialties(name)
        found |= detect_disease_topics(name)
        low = _norm(name)
        if "hypoglyc" in low or "diabetes" in low:
            found.add("endocrinology")
        if "coronary" in low or "cardiac" in low or "heart" in low or "acs" in low:
            found.add("cardiology")
        if "neuralgia" in low or "trigeminal" in low or "stroke" in low or "neuro" in low:
            found.add("neurology")
        if "copd" in low or "asthma" in low or "pneumonia" in low or "respiratory" in low:
            found.add("pulmonology")
        if "enteric" in low or "typhoid" in low:
            found.add("infectious_disease")
        if "alcohol" in low:
            found.add("addiction_psychiatry")
    return found


def _topic_labels(topics: Set[str]) -> str:
    if not topics:
        return "unclassified"
    return ", ".join(_TOPIC_LABELS.get(t, t.replace("_", " ")) for t in sorted(topics))


def _primary_case_blob(
    case_profile: Optional[dict],
    claim_diagnosis: str = "",
) -> str:
    profile = case_profile or {}
    procedures = profile.get("procedures") or []
    if isinstance(procedures, list):
        proc_text = " ".join(str(p) for p in procedures)
    else:
        proc_text = str(procedures)
    return " ".join([
        str(profile.get("diagnosis") or ""),
        proc_text,
        claim_diagnosis or "",
    ])


def _primary_case_specialties(
    case_profile: Optional[dict],
    claim_diagnosis: str = "",
) -> Set[str]:
    return _detect(_primary_case_blob(case_profile, claim_diagnosis), _PRIMARY_DIAGNOSIS_KEYWORDS)


def _disease_topic_keywords(topics: Set[str]) -> Tuple[str, ...]:
    kws: List[str] = []
    for topic in topics:
        kws.extend(_DISEASE_TOPICS.get(topic, ()))
    return tuple(kws)


def _disease_topic_present(guide_topics: Set[str], blob: str) -> bool:
    """True when any keyword for the selected guideline disease appears in case text."""
    norm = _norm(blob)
    if not norm or not guide_topics:
        return False
    for kw in _disease_topic_keywords(guide_topics):
        if kw in norm:
            return True
    return False


def _guideline_topic_present_in_case(
    guide_specs: Set[str],
    case_profile: Optional[dict],
    case_text: str,
    claim_diagnosis: str,
) -> bool:
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


def _mismatch_message(
    guideline_names: List[str],
    case_labels: str,
    guide_labels: str,
) -> str:
    guidelines = "; ".join(guideline_names)
    return (
        f"Selected guideline(s) do not match the clinical case. "
        f"Case appears related to: {case_labels}. "
        f"Selected guideline(s) ({guidelines}) appear related to: {guide_labels}. "
        f"The audit has been stopped. Please select the correct clinical guideline and run again."
    )


def check_guideline_alignment(
    guideline_names: List[str],
    case_profile: Optional[dict],
    case_text: str = "",
    claim_diagnosis: str = "",
) -> Dict[str, Any]:
    """Return alignment result. Blocks on clear disease / specialty mismatch."""
    guide_disease = disease_topics_from_guidelines(guideline_names)
    guide_specs = specialties_from_guidelines(guideline_names)

    primary_blob = _primary_case_blob(case_profile, claim_diagnosis)
    case_disease = detect_disease_topics(primary_blob)
    case_primary = _primary_case_specialties(case_profile, claim_diagnosis)

    context_blob = " ".join([primary_blob, (case_text or "")[:12000]])

    # --- Disease-topic gate (catches enteric fever vs alcohol, etc.) ---
    if guide_disease:
        if _disease_topic_present(guide_disease, context_blob):
            return {
                "aligned": True,
                "case_topics": sorted(case_disease | case_primary),
                "guideline_topics": sorted(guide_disease),
                "message": "",
                "reason": "guideline_disease_in_case",
            }

        if case_disease & guide_disease:
            return {
                "aligned": True,
                "case_topics": sorted(case_disease),
                "guideline_topics": sorted(guide_disease),
                "overlap": sorted(case_disease & guide_disease),
                "message": "",
                "reason": "disease_topic_overlap",
            }

        if case_disease:
            message = _mismatch_message(
                guideline_names,
                _topic_labels(case_disease),
                _topic_labels(guide_disease),
            )
            return {
                "aligned": False,
                "case_topics": sorted(case_disease),
                "guideline_topics": sorted(guide_disease),
                "message": message,
                "guidelines": guideline_names,
                "reason": "disease_topic_mismatch",
            }

    # --- Specialty gate (legacy; hypoglycemia vs cardiology, etc.) ---
    if not guide_specs:
        return {
            "aligned": True,
            "case_specialties": sorted(case_primary),
            "guideline_specialties": [],
            "message": "",
            "reason": "guideline_unclassified",
        }

    if _guideline_topic_present_in_case(
        guide_specs, case_profile, case_text, claim_diagnosis
    ):
        return {
            "aligned": True,
            "case_specialties": sorted(case_primary | guide_specs),
            "guideline_specialties": sorted(guide_specs),
            "message": "",
            "reason": "guideline_topic_found_in_case",
        }

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

    message = _mismatch_message(
        guideline_names,
        _topic_labels(case_primary),
        _topic_labels(guide_specs),
    )
    return {
        "aligned": False,
        "case_specialties": sorted(case_primary),
        "guideline_specialties": sorted(guide_specs),
        "overlap": [],
        "message": message,
        "guidelines": guideline_names,
        "reason": "specialty_mismatch",
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
