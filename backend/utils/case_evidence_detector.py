"""Detect documented clinical evidence from case text for audit enrichment.

Corrects common LLM/OCR failure modes: creatinine decimal errors, false 'missing
antibiotic/cardiac workup' claims when ECG/Echo/treatment sheets are present, and
CT reports mis-labelled as MRI.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.utils.claim_details_extractor import _classify_document, _source_label

_CREATININE_RE = re.compile(
    r"creatinine\s*(?:\(serum\))?\s*[:.]?\s*(\d+(?:\.\d+)?)\s*mg/dl",
    re.I,
)
_SODIUM_RE = re.compile(
    r"sodium\s*(?:\(serum\))?\s*[:.]?\s*(\d+(?:\.\d+)?)\s*m\s*mol/l",
    re.I,
)
_CRP_RE = re.compile(
    r"c-?reactive\s*protein\s*(?:\(crp\))?\s*[:.]?\s*(\d+(?:\.\d+)?)\s*mg/dl",
    re.I,
)

_ANTIBIOTIC_MARKERS = re.compile(
    r"\b(?:inj\.?\s*)?(?:doxycycline|azithromycin|ceftriaxone|meropenem|piperacillin|"
    r"levofloxacin|ciprofloxacin|metronidazole|vancomycin|colistin|amikacin|"
    r"cefoperazone|sulbactam|piptaz|tazocin|linezolid|clindamycin|"
    r"perixid|u-azom|azom|piperacillin|culture\s+shows\s+growth)\b",
    re.I,
)
_CARDIAC_MARKERS = re.compile(
    r"\b(?:echocardiography|echocardiogram|ecg\s+report|electrocardiogram|"
    r"holter\s+monitoring|troponin|trop-?[ti]|unstable\s+angina|\bacs\b|"
    r"coronary\s+angiograph|\bcag\b|left\s+ventricular\s+hypertrophy|"
    r"bundle\s+branch\s+block|sinus\s+tachycardia)\b",
    re.I,
)
_CT_REPORT_RE = re.compile(
    r"\b(?:ct\s+scan|hrct|hrtc)\b.*?\b(?:thorax|chest|lung)|"
    r"hrct\s+(?:scan\s+of\s+)?thorax|ct\s+scan\s+of\s+thorax",
    re.I | re.S,
)
_MRI_REPORT_RE = re.compile(
    r"\bmri\s+(?:brain|spine|report|of\s+(?:brain|spine|knee|shoulder))|"
    r"magnetic\s+resonance\s+imaging",
    re.I,
)
_ICU_MARKERS = re.compile(r"\bicu\b|i\.c\.u\.|intensive\s+care", re.I)
_COPD_DIAGNOSIS_RE = re.compile(
    r"\b(?:copd|chronic\s+obstructive\s+pulmonary|emphysema)\b",
    re.I,
)

_FILENAME_ECG = re.compile(r"\becg\b|electrocardiogram", re.I)
_FILENAME_ECHO = re.compile(r"\becho\b|echocardiograph", re.I)
_FILENAME_CT = re.compile(r"\bct\s*scan\b|\bhrct\b", re.I)
_PREAUTH_MARKERS = re.compile(
    r"pre[\s-]?auth(?:orization)?|request\s+for\s+cashless|cashless\s+hospitalization",
    re.I,
)
_EMERGENCY_MARKERS = re.compile(
    r"\b(?:emergency|casualty|trauma|walk[\s-]?in)\b|unstable\s+angina|\bacs\b|"
    r"trop-?[ti]\s*\+?|troponin|chest\s+discomfort|"
    r"compression\s+fracture|vertebral\s+fracture|fall\s+(?:from|at|down)|"
    r"unable\s+to\s+walk|cannot\s+walk",
    re.I,
)
_ACUTE_TRAUMA_MARKERS = re.compile(
    r"compression\s+fracture|vertebral\s+fracture|acute\s+(?:mild\s+)?compression|"
    r"fracture\s+(?:of\s+)?L[1-5]|L[1-5]\s+[^\n]{0,40}fracture|"
    r"fall\s+(?:from|at|down|history)|history\s+of\s+fall|"
    r"trauma(?:tic)?\s+(?:fracture|injury)|acute\s+trauma",
    re.I,
)
_MEDICAL_MANAGEMENT_MARKERS = re.compile(
    r"medical\s+management|conservative\s+management|non[\s-]?operative|"
    r"no\s+surgery|iv\s+(?:fluids?|analgesic|steroid)|inj\.?\s*(?:mp|methylpred)",
    re.I,
)
_CONSERVATIVE_TRIAL_GAP_RE = re.compile(
    r"failed\s+conservative|conservative\s+(?:care|management|treatment|therapy)\s+"
    r"(?:not|fail|prior|before)|"
    r"medication\s+trials?|trial\s+of\s+(?:medical|conservative)|"
    r"elective\s+surgery\s+without|adequate\s+trial\s+of\s+medical",
    re.I,
)
_ADMISSION_CHALLENGE_RE = re.compile(
    r"emergency\s+admission|admission\s+(?:not\s+)?justif|opd\s+(?:manage|suffic)|"
    r"could\s+have\s+been\s+managed\s+(?:as\s+)?opd|inpatient\s+not\s+(?:required|needed)",
    re.I,
)
_SYMPTOM_CHEST_RE = re.compile(r"chest\s+discomfort\s*x\s*(\d+)\s*d", re.I)
_SYMPTOM_FEVER_RE = re.compile(r"fever\s*(\d+)\s*[-–]\s*(\d+)\s*days", re.I)
_COPD_SPIROMETRY_RE = re.compile(r"\b(?:copd|spirometry|fev1/?fvc|post[\s-]?bronchodilator)\b", re.I)
_NO_CARDIAC_WORKUP_RE = re.compile(
    r"no\s+cardiac|lack\s+of\s+cardiac|absence\s+of\s+(?:cardiac|acs\s+management)|"
    r"without\s+cardiac|not\s+documented.*(?:ecg|echo|troponin)|"
    r"no\s+(?:ecg|echo|troponin)|lacks?\s+comprehensive\s+documentation\s+for\s+the\s+acs",
    re.I,
)
_ACS_PROCESS_GAP_RE = re.compile(r"\btimi\b|risk\s+score|comprehensive\s+acs\s+management", re.I)
_CULTURE_RE = re.compile(r"culture\s+shows\s+growth\s+of\s+([^\n.;]+)", re.I)

_LAB_FINDING_LABELS = {
    "creatinine": "Creatinine (Serum)",
    "crp": "C-Reactive Protein (CRP)",
    "sodium": "Sodium (Serum)",
}


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _plausible_creatinine(val: float) -> bool:
    return 0.3 <= val <= 15.0


def extract_lab_values(case_text: str) -> Dict[str, str]:
    """Pull key labs from OCR text with physiologic sanity filtering."""
    text = case_text or ""
    labs: Dict[str, str] = {}

    creatinines = [
        float(m.group(1))
        for m in _CREATININE_RE.finditer(text)
        if _plausible_creatinine(float(m.group(1)))
    ]
    if creatinines:
        labs["creatinine"] = f"{creatinines[0]} mg/dl"

    for pat, key, fmt in (
        (_SODIUM_RE, "sodium", "{} mmol/L"),
        (_CRP_RE, "crp", "{} mg/dl"),
    ):
        m = pat.search(text)
        if m:
            labs[key] = fmt.format(m.group(1))

    return labs


def extract_symptom_durations(case_text: str) -> List[str]:
    """Pull symptom-duration phrases from treatment sheets and consult notes."""
    text = case_text or ""
    durations: List[str] = []
    m = _SYMPTOM_CHEST_RE.search(text)
    if m:
        durations.append(f"{m.group(1)} days (chest discomfort)")
    m = _SYMPTOM_FEVER_RE.search(text)
    if m:
        durations.append(f"{m.group(1)}-{m.group(2)} days (fever)")
    return list(dict.fromkeys(durations))


def _split_source_blocks(case_text: str) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    if not case_text:
        return blocks
    parts = re.split(r"=== Source document:\s*(.+?)\s*===", case_text, flags=re.I)
    for i in range(1, len(parts), 2):
        fname = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if fname and body.strip():
            blocks.append((fname, body.strip()))
    return blocks


def _chart_source_label(case_text: str) -> str:
    """Return a source label from the uploaded chart block (filename + content type)."""
    for fname, body in _split_source_blocks(case_text):
        doc_type = _classify_document(fname, body)
        if doc_type in ("indoor_case", "clinical", "pre_auth"):
            return _source_label(fname, doc_type)
    for fname, body in _split_source_blocks(case_text):
        if re.search(r"treatment\s+sheet|ward\s*/\s*bed|consultation\s+note", body, re.I):
            return f"{fname} — clinical chart"
    return "Clinical chart in uploaded case file"


def _dedupe_strings(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        key = _norm(str(item))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(item))
    return out


def _dedupe_checklist(checklist: List[dict]) -> List[dict]:
    seen: set = set()
    out: List[dict] = []
    for item in checklist:
        if not isinstance(item, dict):
            continue
        key = _norm(item.get("area", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


_NON_CLINICAL_DOC_RE = re.compile(
    r"wording|guideline|policy\s+wording|terms\s+and\s+conditions|"
    r"schedule\s+of\s+benefits|family\s+health\s+protector|"
    r"degenerative\s+disorders|clinical\s+practice\s+guideline|"
    r"product\s+brochure|policy\s+document",
    re.I,
)


def _is_non_clinical_document(fname: str, body: str) -> bool:
    name = (fname or "").lower()
    if _NON_CLINICAL_DOC_RE.search(name):
        return True
    head = (body or "")[:2500].lower()
    if re.search(
        r"policy\s+wording|schedule\s+of\s+benefits|general\s+terms|"
        r"exclusions\s+apply|this\s+policy\s+wording|definitions?\s+and\s+exclusions",
        head,
    ):
        return True
    if re.search(
        r"clinical\s+practice\s+guideline|evidence[\s-]?based\s+recommendation|"
        r"grade\s+of\s+recommendation|guideline\s+committee",
        head,
    ) and not re.search(r"patient\s+name|date\s+of\s+admission|discharge\s+summary", head):
        return True
    doc_type = _classify_document(fname, body)
    if doc_type == "policy":
        return True
    return False


def is_non_clinical_document(fname: str, body: str) -> bool:
    """Public alias — policy wordings / uploaded guidelines are not clinical case files."""
    return _is_non_clinical_document(fname, body)


def clinical_case_text(case_text: str) -> str:
    """Case text with policy wordings and clinical guidelines removed."""
    blocks = _split_source_blocks(case_text)
    if not blocks:
        return case_text or ""
    kept = [
        f"=== Source document: {fname} ===\n{body}"
        for fname, body in blocks
        if not _is_non_clinical_document(fname, body)
    ]
    return "\n\n".join(kept) if kept else (case_text or "")


def _has_clinical_mri_report(case_text: str) -> bool:
    for fname, body in _split_source_blocks(case_text):
        if _is_non_clinical_document(fname, body):
            continue
        doc_type = _classify_document(fname, body)
        if doc_type not in ("radiology", "clinical", "indoor_case", "pre_auth", "discharge"):
            continue
        if _MRI_REPORT_RE.search(body) and re.search(
            r"mri\s+(?:brain|spine|report|of\s+)|magnetic\s+resonance\s+imaging\s+report",
            body,
            re.I,
        ):
            return True
    return False


def detect_case_evidence(case_text: str) -> Dict[str, Any]:
    """Summarise what the uploaded case file actually documents."""
    text = clinical_case_text(case_text)
    low = text.lower()

    has_antibiotics = bool(_ANTIBIOTIC_MARKERS.search(text))
    has_cardiac = bool(_CARDIAC_MARKERS.search(text))
    has_ct = bool(_CT_REPORT_RE.search(text)) or bool(_FILENAME_CT.search(text))
    has_mri = _has_clinical_mri_report(case_text)
    has_icu = bool(_ICU_MARKERS.search(text))
    has_copd_dx = bool(_COPD_DIAGNOSIS_RE.search(text))

    filenames_hint = ""
    for marker in ("=== Source document:", "— vision transcription ("):
        if marker in text:
            filenames_hint = text
            break

    if _FILENAME_ECG.search(filenames_hint) or "document type: ecg" in low:
        has_cardiac = True
    if _FILENAME_ECHO.search(filenames_hint) or "echocardiography report" in low:
        has_cardiac = True

    culture_match = _CULTURE_RE.search(text)

    return {
        "labs": extract_lab_values(text),
        "has_antibiotics": has_antibiotics,
        "has_cardiac_workup": has_cardiac,
        "has_ct_report": has_ct,
        "has_mri_report": has_mri,
        "has_icu_care": has_icu,
        "has_copd_diagnosis": has_copd_dx,
        "symptom_durations": extract_symptom_durations(text),
        "has_preauth_form": bool(_PREAUTH_MARKERS.search(text)),
        "culture_organism": culture_match.group(1).strip() if culture_match else "",
    }


def _fix_creatinine_in_text_blob(blob: str, correct: str) -> str:
    if not blob:
        return blob
    return re.sub(
        r"creatinine[^;.\n]{0,40}\b19(?:\s*mg/dl)?",
        f"Creatinine (Serum) {correct}",
        blob,
        flags=re.I,
    )


def _downgrade_deviation(dev: dict, reason: str, severity: str = "Low") -> None:
    dev["case_evidence"] = reason
    dev["severity"] = severity


def _is_copd_spirometry_issue(text: str) -> bool:
    return bool(_COPD_SPIROMETRY_RE.search(_norm(text)))


def _seed_or_update_clinical_findings(
    findings: List[dict],
    evidence: Dict[str, Any],
    case_text: str,
) -> None:
    chart_source = _chart_source_label(case_text)
    labs = evidence.get("labs") or {}

    for key, label in _LAB_FINDING_LABELS.items():
        val = labs.get(key)
        if not val:
            continue
        updated = False
        for item in findings:
            if not isinstance(item, dict):
                continue
            if key in _norm(item.get("parameter", "")):
                item["value"] = val
                if not item.get("source"):
                    item["source"] = chart_source
                updated = True
                break
        if not updated:
            findings.append({
                "parameter": label,
                "value": val,
                "normal_range": "",
                "comment": "From uploaded investigation / lab report",
                "source": chart_source,
            })

    symptom_durations = evidence.get("symptom_durations") or []
    if symptom_durations:
        merged = "; ".join(symptom_durations)
        updated = False
        for item in findings:
            if not isinstance(item, dict):
                continue
            if "symptom duration" in _norm(item.get("parameter", "")):
                existing = str(item.get("value") or "")
                parts = [p.strip() for p in re.split(r"[;]", existing) if p.strip()]
                for part in symptom_durations:
                    if part not in parts:
                        parts.append(part)
                item["value"] = "; ".join(parts) if parts else merged
                item["source"] = item.get("source") or chart_source
                item["comment"] = item.get("comment") or "From treatment sheet / clinical chart"
                updated = True
                break
        if not updated:
            findings.append({
                "parameter": "Symptom duration at presentation",
                "value": merged,
                "normal_range": "",
                "comment": "From treatment sheet / clinical chart",
                "source": chart_source,
            })

    organism = evidence.get("culture_organism")
    if organism and not any("culture" in _norm(f.get("parameter", "")) for f in findings if isinstance(f, dict)):
        findings.append({
            "parameter": "Culture sensitivity",
            "value": organism,
            "normal_range": "",
            "comment": "Organism isolated in uploaded lab report",
            "source": chart_source,
        })


def _reconcile_compliance_verdict(result: dict, evidence: Dict[str, Any]) -> None:
    deviations = result.get("guideline_deviations") or []
    if not isinstance(deviations, list):
        return

    highs = [
        d for d in deviations
        if isinstance(d, dict) and _norm(d.get("severity", "")) == "high"
    ]
    if not highs:
        verdict = _norm(result.get("compliance_verdict", ""))
        if verdict == "non-compliant":
            result["compliance_verdict"] = "Partially Compliant"
        return

    if not evidence.get("has_copd_diagnosis"):
        copd_highs = [
            d for d in highs
            if _is_copd_spirometry_issue(str(d.get("issue", "")))
        ]
        if len(copd_highs) == len(highs):
            result["compliance_verdict"] = "Partially Compliant"


def _remove_challenge(challenges: List[str], needle: str) -> None:
    n = _norm(needle)
    to_remove = [c for c in challenges if n in _norm(c)]
    for c in to_remove:
        if c in challenges:
            challenges.remove(c)


def _mri_clinically_relevant(result: dict, case_text: str, evidence: Dict[str, Any]) -> bool:
    """MRI checklist only for neuro / spine / explicit MRI indications — not cardiology."""
    if evidence.get("has_mri_report"):
        return True
    claim = result.get("claim_details") or {}
    blob = " ".join([
        str(claim.get("diagnosis") or ""),
        str(claim.get("procedure_or_surgery") or ""),
        (case_text or "")[:6000],
    ]).lower()

    neuro = bool(re.search(
        r"trigeminal|neuralgia|stroke|cva|seizure|epilepsy|brain\s+tumor|"
        r"meningitis|neuropathy|parkinson|migraine|neurovascular|mvd\b|"
        r"intracranial|spinal\s+cord|compression\s+fracture|vertebral|"
        r"lumbar|spine|l[1-5]\b|sciatica",
        blob,
        re.I,
    ))
    if neuro:
        return True

    # Cardiology / ACS / hypoglycemia / general medical — MRI not required
    non_mri = bool(re.search(
        r"\b(?:cad|acs|cabg|pci|stent|angina|myocardial|troponin|ecg|echo|"
        r"hypoglyc|diabetes|chest\s+pain|unstable\s+angina|stemi|nstemi|"
        r"coronary|cardiac|cardiology)\b",
        blob,
        re.I,
    ))
    if non_mri:
        return False

    # Default: only require MRI checklist if guideline/deviations already mention it
    for dev in result.get("guideline_deviations") or []:
        if isinstance(dev, dict) and "mri" in _norm(dev.get("issue", "")):
            return True
    return False


def _cardiac_clinically_relevant(result: dict, case_text: str, evidence: Dict[str, Any]) -> bool:
    if evidence.get("has_cardiac_workup"):
        return True
    claim = result.get("claim_details") or {}
    blob = " ".join([
        str(claim.get("diagnosis") or ""),
        str(claim.get("procedure_or_surgery") or ""),
        (case_text or "")[:4000],
    ]).lower()
    return bool(re.search(
        r"\b(?:cad|acs|cabg|pci|stent|angina|myocardial|troponin|chest\s+pain|"
        r"unstable\s+angina|stemi|nstemi|coronary|cardiac|cardiology|heart\s+failure)\b",
        blob,
        re.I,
    ))


def _is_acute_trauma_medical_management(result: dict, case_text: str) -> bool:
    claim = result.get("claim_details") or {}
    blob = " ".join([
        str(claim.get("diagnosis") or ""),
        str(claim.get("procedure_or_surgery") or ""),
        clinical_case_text(case_text)[:8000],
    ])
    if not _ACUTE_TRAUMA_MARKERS.search(blob):
        return False
    # Elective surgery pathways should not use this guard
    if re.search(r"\b(?:surgery|operative|fixation|instrumentation|discectomy|fusion)\b", blob, re.I):
        if not _MEDICAL_MANAGEMENT_MARKERS.search(blob):
            return False
    return True


def apply_case_evidence_corrections(result: dict, case_text: str) -> dict:
    """Fix labs, checklist, deviations, and challenges using deterministic evidence."""
    if not result or result.get("error"):
        return result

    evidence = detect_case_evidence(case_text)
    labs = evidence.get("labs") or {}
    acute_trauma_mm = _is_acute_trauma_medical_management(result, case_text)
    cardiac_relevant = _cardiac_clinically_relevant(result, case_text, evidence)

    # --- Clinical findings: correct creatinine OCR hallucination ---
    findings = result.setdefault("clinical_findings", [])
    if isinstance(findings, list) and labs.get("creatinine"):
        correct_cr = labs["creatinine"]
        for item in findings:
            if not isinstance(item, dict):
                continue
            param = _norm(item.get("parameter", ""))
            if "creatinine" not in param:
                continue
            val = str(item.get("value") or "")
            bad = re.search(r"\b19(?:\.\d+)?\s*mg", val, re.I)
            if bad or (re.search(r"\d+", val) and float(re.search(r"(\d+(?:\.\d+)?)", val).group(1)) > 10):
                item["value"] = correct_cr
                item["comment"] = (
                    str(item.get("comment") or "")
                    + " (corrected from OCR — physiologic range applied)"
                ).strip()

    # --- Checklist corrections ---
    checklist = result.setdefault("clinical_checklist", [])
    if not isinstance(checklist, list):
        checklist = []
        result["clinical_checklist"] = checklist

    def _set_checklist(labels: List[str], available: str, remarks: str) -> None:
        label_norms = {_norm(l) for l in labels}
        matched = False
        for item in checklist:
            if not isinstance(item, dict):
                continue
            area_norm = _norm(item.get("area", ""))
            if area_norm in label_norms or any(ln in area_norm for ln in label_norms if len(ln) > 6):
                item["available"] = available
                item["remarks"] = remarks
                matched = True
        if not matched:
            checklist.append({"area": labels[0], "available": available, "remarks": remarks})

    if evidence.get("has_antibiotics"):
        _set_checklist(
            ["Antibiotic Therapy", "Antibiotics"],
            "YES",
            "Antibiotic therapy and/or culture sensitivity documented in case file",
        )

    if evidence.get("has_cardiac_workup") and cardiac_relevant:
        _set_checklist(
            ["Cardiac Assessment", "Cardiac Workup"],
            "YES",
            "ECG and/or echocardiography and/or troponin workup documented",
        )

    if evidence.get("has_ct_report"):
        for item in checklist:
            if not isinstance(item, dict):
                continue
            if _norm(item.get("area", "")) == "mri report" and not evidence.get("has_mri_report"):
                item["area"] = "CT Scan Report"
                item["available"] = "YES"
                item["remarks"] = "HRCT/CT thorax report present — not MRI"
        _set_checklist(
            ["CT Scan Report", "CT Scan"],
            "YES",
            "CT/HRCT thorax report present in case file",
        )

    # MRI checklist only when clinically relevant (neurology / explicit MRI indication).
    # Cardiology / ACS / hypoglycemia cases must NOT show "MRI Report: NO".
    mri_relevant = _mri_clinically_relevant(result, case_text, evidence)
    if evidence.get("has_mri_report"):
        _set_checklist(
            ["MRI Report"],
            "YES",
            "MRI report present in case file",
        )
    elif mri_relevant:
        _set_checklist(
            ["MRI Report"],
            "NO",
            "No MRI report in uploaded clinical records",
        )
        imaging = result.get("imaging_findings") or []
        if isinstance(imaging, list):
            result["imaging_findings"] = [
                item for item in imaging
                if isinstance(item, dict) and "mri" not in _norm(item.get("type", ""))
            ]
        for dev in result.get("guideline_deviations") or []:
            if not isinstance(dev, dict):
                continue
            if "mri" in _norm(dev.get("issue", "")) and not evidence.get("has_ct_report"):
                _downgrade_deviation(
                    dev,
                    "No MRI report attached in submitted records; remove MRI-specific references.",
                )
    else:
        # Remove irrelevant MRI checklist rows and hallucinated MRI imaging findings.
        result["clinical_checklist"] = [
            item for item in checklist
            if not (isinstance(item, dict) and "mri" in _norm(item.get("area", "")))
        ]
        checklist = result["clinical_checklist"]
        imaging = result.get("imaging_findings") or []
        if isinstance(imaging, list):
            result["imaging_findings"] = [
                item for item in imaging
                if isinstance(item, dict) and "mri" not in _norm(item.get("type", ""))
            ]
        for dev in result.get("guideline_deviations") or []:
            if not isinstance(dev, dict):
                continue
            if "mri" in _norm(dev.get("issue", "")):
                _downgrade_deviation(
                    dev,
                    "MRI is not clinically indicated for this presentation; "
                    "do not treat missing MRI as a documentation gap.",
                )

    result["clinical_checklist"] = _dedupe_checklist(checklist)

    # Specialty gate: drop CT / Cardiac / Medication Trials when not clinically indicated
    pruned: List[dict] = []
    for item in result["clinical_checklist"]:
        if not isinstance(item, dict):
            continue
        area = _norm(item.get("area", ""))
        if "cardiac" in area and not cardiac_relevant:
            continue
        if ("ct scan" in area or area == "ct") and not evidence.get("has_ct_report"):
            continue
        if "medication trial" in area:
            dx_blob = " ".join([
                str((result.get("claim_details") or {}).get("diagnosis") or ""),
                clinical_case_text(case_text)[:3000],
            ]).lower()
            if not re.search(r"neuralgia|trigeminal|mvd\b", dx_blob):
                continue
        pruned.append(item)
    result["clinical_checklist"] = pruned
    checklist = result["clinical_checklist"]

    if isinstance(findings, list):
        _seed_or_update_clinical_findings(findings, evidence, case_text)

    # --- Guideline deviations: retract false 'missing' claims ---
    deviations = result.get("guideline_deviations") or []
    if isinstance(deviations, list):
        for dev in deviations:
            if not isinstance(dev, dict):
                continue
            issue = _norm(dev.get("issue", ""))
            issue_raw = str(dev.get("issue", ""))
            evidence_text = _norm(dev.get("case_evidence", ""))

            if evidence.get("has_antibiotics") and (
                "antibiotic" in issue or "no antibiotic" in evidence_text
                or "absence of antibiotic" in evidence_text
            ):
                _downgrade_deviation(
                    dev,
                    "Antibiotic therapy documented on treatment sheets and/or culture report.",
                )

            if evidence.get("has_cardiac_workup"):
                if _NO_CARDIAC_WORKUP_RE.search(issue_raw) or _NO_CARDIAC_WORKUP_RE.search(
                    str(dev.get("case_evidence") or "")
                ):
                    _downgrade_deviation(
                        dev,
                        "Cardiac workup documented: ECG and/or echocardiography and/or troponin "
                        "in uploaded case file.",
                    )
                elif _ACS_PROCESS_GAP_RE.search(issue_raw):
                    _downgrade_deviation(
                        dev,
                        "ECG/echocardiography/troponin documented; formal TIMI risk score or "
                        "written ACS management pathway may still be requested from hospital.",
                        severity="Medium",
                    )

            if _is_copd_spirometry_issue(issue_raw) and not evidence.get("has_copd_diagnosis"):
                _downgrade_deviation(
                    dev,
                    "COPD/spirometry not documented in case file — primary presentation appears "
                    "cardiac and/or infective respiratory; COPD guideline may not apply.",
                )

            if acute_trauma_mm and (
                _CONSERVATIVE_TRIAL_GAP_RE.search(issue_raw)
                or _CONSERVATIVE_TRIAL_GAP_RE.search(str(dev.get("case_evidence") or ""))
            ):
                _downgrade_deviation(
                    dev,
                    "Acute traumatic fracture managed medically — failed outpatient conservative "
                    "trial is not a prerequisite for emergency inpatient medical management.",
                    severity="Low",
                )

            if labs.get("creatinine"):
                dev["case_evidence"] = _fix_creatinine_in_text_blob(
                    str(dev.get("case_evidence") or ""), labs["creatinine"]
                )

    # --- Challenge points ---
    challenges = result.setdefault("challenge_points", [])
    if isinstance(challenges, list):
        if evidence.get("has_antibiotics"):
            _remove_challenge(challenges, "antibiotic")
        if evidence.get("has_cardiac_workup"):
            _remove_challenge(challenges, "cardiac evaluation")
            _remove_challenge(challenges, "angiography")
        if not evidence.get("has_copd_diagnosis"):
            _remove_challenge(challenges, "spirometry")
            _remove_challenge(challenges, "copd")
        if acute_trauma_mm:
            to_drop = [
                c for c in challenges
                if _ADMISSION_CHALLENGE_RE.search(str(c))
                or _CONSERVATIVE_TRIAL_GAP_RE.search(str(c))
            ]
            for c in to_drop:
                if c in challenges:
                    challenges.remove(c)
        result["challenge_points"] = _dedupe_strings(challenges)

    # --- Observations referencing false gaps ---
    observations = result.get("observations") or []
    if isinstance(observations, list):
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            analysis = str(obs.get("analysis") or "")
            q = _norm(obs.get("question", ""))

            if labs.get("creatinine"):
                analysis = _fix_creatinine_in_text_blob(analysis, labs["creatinine"])

            if evidence.get("has_antibiotics") and "antibiotic" in q:
                obs["answer"] = "Partially Supported"
                analysis += (
                    " Note: antibiotic prescriptions and/or culture sensitivity are "
                    "documented in the uploaded treatment sheets and lab reports."
                )

            if evidence.get("has_cardiac_workup") and (
                "cardiac" in q or "angiograph" in q or "acs" in q or "coronary" in q
            ):
                if _ACS_PROCESS_GAP_RE.search(analysis) or _ACS_PROCESS_GAP_RE.search(
                    str(obs.get("question") or "")
                ):
                    obs["answer"] = "Partially Supported"
                    analysis += (
                        " Note: ECG and/or echocardiography and troponin are documented; "
                        "only formal TIMI risk scoring or written ACS pathway may be missing."
                    )
                elif _NO_CARDIAC_WORKUP_RE.search(analysis):
                    obs["answer"] = "Partially Supported"
                    analysis += (
                        " Note: ECG and/or echocardiography and troponin workup are present "
                        "in the uploaded case file."
                    )

            if acute_trauma_mm and (
                _ADMISSION_CHALLENGE_RE.search(str(obs.get("question") or ""))
                or _ADMISSION_CHALLENGE_RE.search(analysis)
                or _CONSERVATIVE_TRIAL_GAP_RE.search(analysis)
                or _CONSERVATIVE_TRIAL_GAP_RE.search(str(obs.get("question") or ""))
            ):
                ans = _norm(obs.get("answer", ""))
                if ans in ("not supported", "insufficient evidence", "partially supported"):
                    obs["answer"] = "Supported"
                analysis += (
                    " Note: Acute traumatic fracture with pain/immobility supports inpatient "
                    "medical management; do not require a failed OPD conservative trial first."
                )

            obs["analysis"] = analysis.strip()

    # --- Documentation gaps ---
    gaps = result.setdefault("documentation_gaps", [])
    if isinstance(gaps, list):
        filtered = []
        for gap in gaps:
            g = _norm(str(gap))
            if evidence.get("has_antibiotics") and "no documentation of antibiotic" in g:
                continue
            if evidence.get("has_antibiotics") and "absence of spirometry" in g:
                continue
            if evidence.get("has_cardiac_workup") and "missing cardiac" in g:
                continue
            if evidence.get("has_cardiac_workup") and "no cardiac" in g:
                continue
            if not evidence.get("has_copd_diagnosis") and _is_copd_spirometry_issue(g):
                continue
            if acute_trauma_mm and (
                _CONSERVATIVE_TRIAL_GAP_RE.search(str(gap))
                or _ADMISSION_CHALLENGE_RE.search(str(gap))
            ):
                continue
            if labs.get("creatinine"):
                gap = _fix_creatinine_in_text_blob(str(gap), labs["creatinine"])
            filtered.append(gap)
        result["documentation_gaps"] = _dedupe_strings(filtered)

    # --- Pre-auth cross-check when no pre-auth form in packet ---
    if not evidence.get("has_preauth_form"):
        for obs in observations if isinstance(observations, list) else []:
            if not isinstance(obs, dict):
                continue
            analysis = str(obs.get("analysis") or "")
            if re.search(r"cross[\s-]?checked\s+with\s+pre[\s-]?auth\s*:\s*yes", analysis, re.I):
                obs["analysis"] = re.sub(
                    r"cross[\s-]?checked\s+with\s+pre[\s-]?auth\s*:\s*yes",
                    "Pre-authorization form not present in uploaded case file",
                    analysis,
                    flags=re.I,
                )

    # --- ICU / room category ---
    if evidence.get("has_icu_care"):
        tba = result.setdefault("treatment_billing_audit", {})
        if not str(tba.get("room_category_admitted") or "").strip():
            tba["room_category_admitted"] = "ICU"

    # --- Nature of admission hint ---
    claim = result.setdefault("claim_details", {})
    nature = _norm(claim.get("nature_of_admission", ""))
    if _EMERGENCY_MARKERS.search(clinical_case_text(case_text)) or acute_trauma_mm:
        claim["nature_of_admission"] = "Emergency"
    elif nature in ("", "unknown"):
        if re.search(r"unstable\s+angina|\bacs\b|chest\s+discomfort", case_text, re.I):
            claim["nature_of_admission"] = "Emergency"

    if acute_trauma_mm:
        proc = str(claim.get("procedure_or_surgery") or "").strip()
        if not proc or proc.lower() in ("not specified", "unknown", "-", "—"):
            claim["procedure_or_surgery"] = "Medical management"

    result["_case_evidence"] = evidence
    _reconcile_compliance_verdict(result, evidence)
    return result


def format_case_evidence_block(case_text: str) -> str:
    """Compact evidence summary for the audit LLM prompt."""
    evidence = detect_case_evidence(case_text)
    lines = ["=== CASE EVIDENCE (deterministic detection — prefer over guesswork) ==="]
    labs = evidence.get("labs") or {}
    if labs:
        lines.append("Labs: " + "; ".join(f"{k}={v}" for k, v in labs.items()))
    flags = []
    if evidence.get("has_antibiotics"):
        flags.append("antibiotics/culture documented")
    if evidence.get("has_cardiac_workup"):
        flags.append("cardiac workup (ECG/Echo/troponin) documented")
    if evidence.get("has_ct_report"):
        flags.append("CT/HRCT report present")
    if evidence.get("has_mri_report"):
        flags.append("MRI report present")
    if evidence.get("has_icu_care"):
        flags.append("ICU care documented")
    durations = evidence.get("symptom_durations") or []
    if durations:
        flags.append("symptom duration: " + "; ".join(durations))
    if not evidence.get("has_copd_diagnosis") and flags:
        lines.append(
            "Note: COPD/spirometry not clearly documented — do not treat as COPD case unless stated."
        )
    if flags:
        lines.append("Documented: " + "; ".join(flags))
    if len(lines) <= 1:
        return ""
    lines.append(
        "Use this block when filling clinical_checklist and before claiming antibiotics, "
        "cardiac workup, or imaging are missing."
    )
    return "\n".join(lines)
