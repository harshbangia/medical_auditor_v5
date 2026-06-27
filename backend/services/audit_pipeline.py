import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional
from uuid import uuid4

from backend.ai.audit_engine import analyze_case_images, run_audit
from backend.ai.case_profiler import extract_case_profile, normalize_str_list
from backend.ai.guideline_selector import select_guideline
from backend.rag.guideline_retriever import retrieve_guideline_sections
from backend.rag.rag_manager import get_or_create_index
from backend.services.audit_jobs import AuditJob
from backend.services.s3_utils import download_guideline
from backend.ai.audit_result_enricher import enrich_audit_result
from backend.ai.clinical_synthesizer import build_clinical_synthesis_section
from backend.utils.insurance_extractor import enrich_insurance_facts, merge_insurance_into_result
from backend.utils.claim_details_extractor import enrich_claim_facts, merge_claim_details_into_result
from backend.utils.pdf_reader import extract_text_and_images

ProgressFn = Callable[[str, int, str], None]


def _noop_progress(phase: str, progress: int, message: str):
    pass


def _normalize_timeline(result: dict):
    claim = result.get("claim_details") or {}
    timeline = result.get("timeline") or []
    if not isinstance(timeline, list):
        timeline = []

    def _norm(s: str) -> str:
        return " ".join(str(s or "").strip().lower().split())

    def _is_unknown_date(s: str) -> bool:
        val = _norm(s)
        return (not val) or (val in {"-", "na", "n/a", "not available", "unknown"}) or ("*" in val)

    normalized = []
    seen_signatures = set()
    existing_event_keys = set()

    for item in timeline:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "").strip()
        event = str(item.get("event") or "").strip()
        if not event and not date:
            continue
        if _is_unknown_date(date):
            date = ""
        signature = (_norm(event), _norm(date))
        if signature in seen_signatures:
            continue
        normalized.append({"date": date, "event": event})
        seen_signatures.add(signature)
        if event:
            existing_event_keys.add(_norm(event))

    required = [
        ("consultation_date", "Consultation date"),
        ("date_of_admission", "Date of admission"),
        ("date_of_discharge", "Date of discharge"),
        ("procedure_or_surgery", "Procedure / surgery done"),
        ("nature_of_admission", "Nature of admission"),
    ]

    for field, label in required:
        value = str(claim.get(field) or "").strip()
        if not value or _is_unknown_date(value):
            continue
        norm_label = _norm(label)
        norm_value = _norm(value)
        if norm_label in existing_event_keys:
            continue
        duplicate_found = False
        for item in normalized:
            e = _norm(item.get("event", ""))
            d = _norm(item.get("date", ""))
            if not e and not d:
                continue
            if (norm_label in e or e in norm_label) and (
                norm_value in e or e in norm_value or norm_value == d
            ):
                duplicate_found = True
                break
        if duplicate_found:
            continue
        normalized.append({"date": value, "event": label})
        seen_signatures.add((norm_label, norm_value))

    result["timeline"] = normalized


def _ensure_result_shape(result: dict) -> dict:
    result.setdefault("patient_details", {})
    result.setdefault("insurance_details", {})
    for _k in ("insurance_company", "policy_number", "policy_period", "claim_incident_number"):
        result["insurance_details"].setdefault(_k, "")
    result.setdefault("claim_details", {})
    for _k in (
        "hospital", "consultation_date", "consultation_date_source",
        "date_of_admission", "date_of_admission_source",
        "date_of_discharge", "date_of_discharge_source",
        "nature_of_admission", "procedure_or_surgery", "diagnosis",
    ):
        result["claim_details"].setdefault(_k, "")
    result.setdefault("date_discrepancies", [])
    result.setdefault("clinical_findings", [])
    result.setdefault("documentation_gaps", [])
    result.setdefault("clinical_checklist", [])
    result.setdefault("guideline_deviations", [])
    result.setdefault("challenge_points", [])
    result.setdefault("compliance_verdict", "")
    result.setdefault("imaging_findings", [])
    result.setdefault("auditor_observation_summary", "")
    result.setdefault("treatment_billing_audit", {})
    for _k in (
        "room_category_admitted", "room_category_eligible", "procedures_performed",
        "cross_checked_with_preauth", "excluded_items_billed", "charges_appropriate",
    ):
        result["treatment_billing_audit"].setdefault(_k, "")
    result.setdefault("financial_review", {})
    for _k in (
        "total_hospital_bill", "non_payable_amount", "net_claimable_amount",
        "recommended_approval_amount", "patient_liability",
    ):
        result["financial_review"].setdefault(_k, "")
    result.setdefault("timeline", [])
    result.setdefault("observations", [])
    result.setdefault("inference", "")
    result.setdefault("auditor_conclusion", "No conclusion generated")
    result.setdefault("remarks", "")
    result.setdefault("qa_section", [])
    result.setdefault("document_sources", [])
    inf = (result.get("inference") or "").strip()
    ac = (result.get("auditor_conclusion") or "").strip()
    if inf and not ac:
        result["auditor_conclusion"] = inf
    elif ac and not inf:
        result["inference"] = ac
    _normalize_timeline(result)
    return result


def _summarize_source(filename: str, text: str) -> dict:
    total = len(text or "")
    vision_chars = 0
    typed_chars = total
    if "vision transcription" in (text or ""):
        for block in (text or "").split("=== Page "):
            if "— vision transcription" in block:
                vision_chars += len(block)
        typed_chars = max(0, total - vision_chars)
    return {
        "filename": filename,
        "total_chars": total,
        "typed_chars": typed_chars,
        "handwritten_or_scanned_chars": vision_chars,
        "contains_handwriting_or_scan": vision_chars > 0,
    }


def _process_files_sequential(file_items: List[tuple], progress: ProgressFn) -> tuple:
    """Process PDFs one at a time to avoid OOM on small EC2 instances."""
    if not file_items:
        return "", [], [], []

    unique = {}
    for name, data in file_items:
        if name not in unique:
            unique[name] = data
    file_items = list(unique.items())

    total = len(file_items)
    progress("extracting", 10, f"Processing {total} PDF(s)…")
    case_texts = []
    images = []
    source_summaries = []
    temp_pdf_paths = []

    for idx, (name, data) in enumerate(file_items):
        pct = 10 + int(55 * idx / max(total, 1))
        progress("extracting", pct, f"Processing PDF {idx + 1}/{total}: {name}")
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(data)
                tmp.flush()
                tmp_path = tmp.name
            temp_pdf_paths.append((tmp_path, name))
            text, imgs = extract_text_and_images(tmp_path, source_name=name)
            if text.strip():
                case_texts.append(text)
            images.extend(imgs or [])
            source_summaries.append(_summarize_source(name, text))
        except Exception as exc:
            raise RuntimeError(f"Failed to read {name}: {exc}") from exc
        pct_done = 10 + int(55 * (idx + 1) / total)
        progress("extracting", pct_done, f"Finished {idx + 1}/{total}: {name}")

    return "\n\n".join(case_texts), images, source_summaries, temp_pdf_paths


def run_full_audit(
    file_items: List[tuple],
    guideline: Optional[str],
    user_question: Optional[str],
    global_cache: dict,
    progress: ProgressFn = _noop_progress,
) -> dict:
    started = time.time()
    temp_pdf_paths = []
    guideline_path = None
    case_text, images, source_summaries, temp_pdf_paths = _process_files_sequential(
        file_items, progress
    )

    if len(case_text.strip()) < 50:
        raise RuntimeError("No meaningful text extracted from uploaded PDFs")

    progress("insurance", 58, "Extracting insurance details from letters…")
    insurance_facts = enrich_insurance_facts(case_text, temp_pdf_paths)
    claim_facts = enrich_claim_facts(case_text, temp_pdf_paths)
    clinical_synthesis = build_clinical_synthesis_section(case_text)

    progress("guideline", 65, "Loading clinical guideline…")
    if not guideline:
        guideline = select_guideline(case_text)
    guideline = guideline.strip().replace('"', '').replace("'", "")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        download_guideline(guideline, tmp.name)
        guideline_path = tmp.name

    try:
        progress("profile", 62, "Extracting case facts & loading guideline…")

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_profile = pool.submit(extract_case_profile, case_text[:16000])
            fut_index = pool.submit(get_or_create_index, guideline_path, cache_key=guideline)
            case_profile = fut_profile.result()
            index, chunks = fut_index.result()

        case_hint = (
            f"{case_profile.get('diagnosis', '')} | "
            f"{', '.join(normalize_str_list(case_profile.get('procedures')))}"
        )

        progress("rag", 72, "Retrieving relevant guideline criteria…")
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_rag = pool.submit(
                retrieve_guideline_sections, index, chunks, case_profile, case_text
            )
            fut_vision = pool.submit(analyze_case_images, images, case_hint)
            relevant_guideline = fut_rag.result()
            image_analysis = fut_vision.result()

        if not relevant_guideline.strip():
            raise RuntimeError("Guideline retrieval failed")

        progress("ai_audit", 82, "Running adversarial medical audit…")
        result = run_audit(
            case_text,
            relevant_guideline,
            user_question=user_question,
            images=images,
            case_profile=case_profile,
            guideline_name=guideline,
            image_analysis_text=image_analysis,
            insurance_facts=insurance_facts,
            clinical_synthesis=clinical_synthesis,
            claim_facts=claim_facts,
        )

        if not result or not isinstance(result, dict):
            raise RuntimeError("AI returned empty or invalid response")

        if result.get("error"):
            detail = result.get("parse_error") or result.get("detail") or result.get("error")
            raise RuntimeError(f"AI audit failed: {detail}")

        result = _ensure_result_shape(result)
        result = merge_insurance_into_result(result, insurance_facts)
        result = merge_claim_details_into_result(result, claim_facts)
        result = enrich_audit_result(result, case_text, insurance_facts, claim_facts)
        result["document_sources"] = source_summaries

        if all([
            not result.get("patient_details"),
            not result.get("clinical_findings"),
            not result.get("observations"),
            not (result.get("auditor_conclusion") or result.get("inference")),
        ]):
            raise RuntimeError("AI returned empty structured response")

        session_id = str(uuid4())
        global_cache[session_id] = {
            "case_text": case_text,
            "images": images,
            "guideline": guideline,
            "index": index,
            "chunks": chunks,
        }
        result["session_id"] = session_id
        progress("done", 100, f"Completed in {time.time() - started:.0f}s")
        return result
    finally:
        for pdf_path, _name in temp_pdf_paths:
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
        if guideline_path and os.path.exists(guideline_path):
            try:
                os.remove(guideline_path)
            except OSError:
                pass


def run_job_audit(job: AuditJob, file_items, guideline, user_question, global_cache) -> dict:
    def progress(phase, pct, message):
        from backend.services.audit_jobs import _update
        _update(job, phase=phase, progress=pct, message=message)

    return run_full_audit(file_items, guideline, user_question, global_cache, progress=progress)
