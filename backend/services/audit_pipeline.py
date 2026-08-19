import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional
from uuid import uuid4

from backend.ai.audit_engine import analyze_case_images, run_audit
from backend.ai.case_profiler import extract_case_profile, normalize_str_list, normalize_case_profile
from backend.ai.guideline_selector import select_guideline
from backend.rag.guideline_retriever import retrieve_from_guidelines, retrieve_guideline_sections
from backend.rag.rag_manager import get_or_create_index
from backend.services.audit_jobs import AuditJob
from backend.services.s3_utils import download_guideline
from backend.ai.audit_result_enricher import enrich_audit_result
from backend.ai.clinical_synthesizer import build_clinical_synthesis_section
from backend.utils.insurance_extractor import enrich_insurance_facts, merge_insurance_into_result
from backend.utils.claim_details_extractor import enrich_claim_facts, merge_claim_details_into_result
from backend.utils.guideline_alignment import assert_guideline_alignment, GuidelineMismatchError
from backend.ai.document_extractor import map_case_documents, ledger_to_case_profile
from backend.utils.case_facts_ledger import (
    apply_ledger_to_claim_facts,
    build_case_facts_ledger,
    format_ledger_for_audit,
)
from backend.agents.orchestrator import apply_agent_postprocess
from backend.agents.planner import build_audit_plan
from backend.utils.pdf_reader import extract_text_and_images
from backend.utils.case_evidence_detector import clinical_case_text, is_non_clinical_document

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
        "proposed_hospitalization_date", "proposed_hospitalization_date_source",
        "date_of_discharge", "date_of_discharge_source",
        "nature_of_admission", "procedure_or_surgery", "diagnosis",
        "all_document_dates",
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
        "amount_saved", "savings_percentage",
    ):
        result["financial_review"].setdefault(_k, "")
    result.setdefault("fraud_abuse", {})
    result.setdefault("fraud_abuse_findings", [])
    result.setdefault("claim_savings", {})
    result.setdefault("guideline_alignment", {})
    result.setdefault("timeline", [])
    result.setdefault("observations", [])
    result.setdefault("inference", "")
    result.setdefault("auditor_conclusion", "No conclusion generated")
    result.setdefault("report_summary", [])
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
        return "", [], [], [], []

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

    doc_blocks: List[tuple] = []

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
                case_texts.append(f"=== Source document: {name} ===\n{text}")
                doc_blocks.append((name, text))
            images.extend(imgs or [])
            source_summaries.append(_summarize_source(name, text))
        except Exception as exc:
            raise RuntimeError(f"Failed to read {name}: {exc}") from exc
        pct_done = 10 + int(55 * (idx + 1) / total)
        progress("extracting", pct_done, f"Finished {idx + 1}/{total}: {name}")

    return "\n\n".join(case_texts), images, source_summaries, temp_pdf_paths, doc_blocks


def _normalize_guideline_list(
    guideline: Optional[str] = None,
    guidelines: Optional[List[str]] = None,
) -> List[str]:
    """Merge legacy single guideline with multi-select list; dedupe preserving order."""
    names: List[str] = []
    for source in (guidelines or []):
        if source and str(source).strip():
            names.append(str(source).strip())
    if guideline and str(guideline).strip():
        names.append(str(guideline).strip())
    seen = set()
    result: List[str] = []
    for name in names:
        clean = name.replace('"', "").replace("'", "")
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def run_full_audit(
    file_items: List[tuple],
    guideline: Optional[str],
    user_question: Optional[str],
    global_cache: dict,
    progress: ProgressFn = _noop_progress,
    guidelines: Optional[List[str]] = None,
) -> dict:
    started = time.time()
    temp_pdf_paths = []
    case_text, images, source_summaries, temp_pdf_paths, doc_blocks = _process_files_sequential(
        file_items, progress
    )

    if len(case_text.strip()) < 50:
        raise RuntimeError("No meaningful text extracted from uploaded PDFs")

    progress("claim", 57, "Extracting dates and claim fields…")
    claim_facts = enrich_claim_facts(case_text, temp_pdf_paths)

    progress("map", 59, "Mapping structured facts from each document…")
    clinical_doc_blocks = [
        (name, text) for name, text in doc_blocks
        if not is_non_clinical_document(name, text)
    ]
    per_doc_facts = map_case_documents(clinical_doc_blocks or doc_blocks, progress=progress)
    case_facts_ledger = build_case_facts_ledger(per_doc_facts, claim_facts)
    claim_facts = apply_ledger_to_claim_facts(claim_facts, case_facts_ledger)
    case_profile = ledger_to_case_profile(case_facts_ledger)
    audit_plan = build_audit_plan(
        document_count=len(source_summaries or []),
        specialty_hint=str(case_profile.get("diagnosis") or ""),
        has_handwriting=any(
            isinstance(s, dict) and s.get("contains_handwriting_or_scan")
            for s in (source_summaries or [])
        ),
    )
    progress(
        "plan",
        61,
        f"Audit plan ready ({len(audit_plan.steps)} stages)…",
    )
    if not (case_profile.get("diagnosis") or case_profile.get("chief_complaint")):
        progress("profile", 61, "Supplementing case profile from combined text…")
        supplemental = extract_case_profile(case_text[:16000])
        for key in ("diagnosis", "age", "gender", "chief_complaint", "admission_type"):
            if not case_profile.get(key) and supplemental.get(key):
                case_profile[key] = supplemental[key]
        for key in ("procedures", "key_labs", "imaging_mentioned", "documentation_weaknesses"):
            if not case_profile.get(key) and supplemental.get(key):
                case_profile[key] = supplemental[key]
    case_profile = normalize_case_profile(case_profile)

    progress("insurance", 63, "Extracting insurance details from letters…")
    insurance_facts = enrich_insurance_facts(case_text, temp_pdf_paths)

    progress("identity", 64, "Reading claim identity from preauth / cashless forms…")
    from backend.agents.claim_identity_agent import (
        apply_claim_identity_to_facts,
        extract_claim_identity,
    )
    identity = extract_claim_identity(temp_pdf_paths, case_text=case_text)
    insurance_facts, claim_facts = apply_claim_identity_to_facts(
        identity, insurance_facts, claim_facts
    )
    # Push identity demographics into ledger merge path
    if identity.get("age") or identity.get("patient_name") or identity.get("hospital"):
        merged = (case_facts_ledger.get("merged") or {})
        if identity.get("age"):
            merged["age"] = identity["age"]
        if identity.get("patient_name") and (
            not merged.get("patient_name")
            or len(identity["patient_name"]) >= len(str(merged.get("patient_name") or ""))
        ):
            merged["patient_name"] = identity["patient_name"]
        if identity.get("sex") and not merged.get("sex"):
            merged["sex"] = identity["sex"]
        if identity.get("hospital"):
            merged["hospital"] = identity["hospital"]
        if identity.get("policy_number"):
            merged["policy_number"] = identity["policy_number"]
        if identity.get("bill_amount"):
            merged["bill_amount"] = identity["bill_amount"]
        case_facts_ledger["merged"] = merged
        case_facts_ledger["claim_identity"] = identity

    progress("notebook", 65, "Building Case Notebook (corpus + Assessor FWA)…")
    from backend.notebook import build_case_notebook, apply_notebook_to_result
    admit_for_notebook = str(
        (claim_facts or {}).get("date_of_admission")
        or (case_facts_ledger.get("merged") or {}).get("admission_date")
        or ""
    )
    case_notebook = build_case_notebook(
        case_text=case_text,
        doc_blocks=doc_blocks,
        expected_patient_name=str(
            identity.get("patient_name")
            or (case_facts_ledger.get("merged") or {}).get("patient_name")
            or ""
        ),
        current_claim=str((insurance_facts or {}).get("claim_incident_number") or ""),
        current_policy=str((insurance_facts or {}).get("policy_number") or ""),
        admission_date=admit_for_notebook,
    )
    # Prefer notebook-validated IDs before guideline/audit LLM
    if case_notebook.validated_ids.get("claim_incident_number"):
        insurance_facts["claim_incident_number"] = case_notebook.validated_ids[
            "claim_incident_number"
        ].split(".", 1)[0]
    if case_notebook.validated_ids.get("policy_number"):
        insurance_facts["policy_number"] = case_notebook.validated_ids["policy_number"]

    # Drop policy wordings / uploaded guideline PDFs from clinical reasoning context
    clinical_text = clinical_case_text(case_text)
    clinical_synthesis = build_clinical_synthesis_section(clinical_text)

    progress("guideline", 65, "Loading clinical guideline(s)…")
    guideline_names = _normalize_guideline_list(guideline, guidelines)
    if not guideline_names:
        guideline_names = [select_guideline(clinical_text).strip().replace('"', '').replace("'", "")]

    guideline_paths: List[str] = []
    for gname in guideline_names:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            download_guideline(gname, tmp.name)
            guideline_paths.append(tmp.name)

    try:
        progress("profile", 62, "Loading clinical guideline(s)…")

        with ThreadPoolExecutor(max_workers=min(4, len(guideline_names) + 1)) as pool:
            index_futures = {
                pool.submit(get_or_create_index, path, cache_key=name): name
                for path, name in zip(guideline_paths, guideline_names)
            }
            guideline_stores = []
            for fut in index_futures:
                name = index_futures[fut]
                index, chunks = fut.result()
                guideline_stores.append((name, index, chunks))

        # Hard gate: do not run audit when guideline specialty ≠ case specialty
        progress("alignment", 68, "Checking guideline–case alignment…")
        claim_dx = (claim_facts or {}).get("diagnosis") or ""
        try:
            alignment = assert_guideline_alignment(
                guideline_names,
                case_profile,
                case_text=clinical_text,
                claim_diagnosis=claim_dx,
            )
        except GuidelineMismatchError as exc:
            progress("failed", 100, str(exc))
            raise RuntimeError(str(exc)) from exc

        case_hint = (
            f"{case_profile.get('diagnosis', '')} | "
            f"{', '.join(normalize_str_list(case_profile.get('procedures')))}"
        )

        progress("rag", 72, "Retrieving relevant guideline criteria…")
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_rag = pool.submit(
                retrieve_from_guidelines, guideline_stores, case_profile, clinical_text
            )
            fut_vision = pool.submit(analyze_case_images, images, case_hint)
            relevant_guideline = fut_rag.result()
            image_analysis = fut_vision.result()

        if not relevant_guideline.strip():
            raise RuntimeError("Guideline retrieval failed")

        guidelines_label = "; ".join(guideline_names)
        progress("ai_audit", 82, "Running adversarial medical audit…")
        result = run_audit(
            clinical_text,
            relevant_guideline,
            user_question=user_question,
            images=images,
            case_profile=case_profile,
            guideline_name=guidelines_label,
            guidelines_used=guideline_names,
            image_analysis_text=image_analysis,
            insurance_facts=insurance_facts,
            clinical_synthesis=clinical_synthesis,
            claim_facts=claim_facts,
            case_facts_ledger=case_facts_ledger,
        )

        if not result or not isinstance(result, dict):
            raise RuntimeError("AI returned empty or invalid response")

        if result.get("error"):
            detail = result.get("parse_error") or result.get("detail") or result.get("error")
            raise RuntimeError(f"AI audit failed: {detail}")

        result = _ensure_result_shape(result)
        result = merge_insurance_into_result(result, insurance_facts)
        result = merge_claim_details_into_result(result, claim_facts)
        result = enrich_audit_result(
            result, case_text, insurance_facts, claim_facts, source_summaries,
            case_facts_ledger=case_facts_ledger,
        )
        result = apply_notebook_to_result(result, case_notebook)
        progress("verify", 92, "Verifying evidence & assembling case record…")
        result = apply_agent_postprocess(
            result,
            case_text=case_text,
            source_summaries=source_summaries,
            case_facts_ledger=case_facts_ledger,
            claim_facts=claim_facts,
            guidelines=guideline_names,
        )
        result["document_sources"] = source_summaries
        result["case_facts_ledger"] = case_facts_ledger
        result["guideline_alignment"] = alignment
        # Re-apply notebook IDs/FWA after agent postprocess so they survive overwrites
        result = apply_notebook_to_result(result, case_notebook)

        # Absolute final seal — DOA-aware claim/policy/bill cannot be wrong-headed by LLM
        from backend.notebook.identity_seal import (
            apply_identity_seal,
            build_identity_seal,
            extract_admission_yyyymmdd_from_result,
        )
        final_seal = build_identity_seal(
            corpus_text=case_notebook.full_corpus or case_text or "",
            assessor=case_notebook.assessor,
            admission_yyyymmdd=extract_admission_yyyymmdd_from_result(result, claim_facts),
            current_claim=str(
                (result.get("insurance_details") or {}).get("claim_incident_number") or ""
            ),
            current_policy=str(
                (result.get("insurance_details") or {}).get("policy_number") or ""
            ),
            current_bill=str(
                (result.get("financial_review") or {}).get("total_hospital_bill")
                or (result.get("claim_details") or {}).get("total_hospital_bill")
                or ""
            ),
        )
        result = apply_identity_seal(result, final_seal, force_zero_recommended_if_rejected=True)

        if all([
            not result.get("patient_details"),
            not result.get("clinical_findings"),
            not result.get("observations"),
            not (result.get("auditor_conclusion") or result.get("inference")),
        ]):
            raise RuntimeError("AI returned empty structured response")

        session_id = str(uuid4())
        first_name, first_index, first_chunks = guideline_stores[0]
        session_payload = {
            "case_text": case_text,
            "images": images,
            "guidelines": guideline_names,
            "guideline": guidelines_label,
            "guideline_stores": guideline_stores,
            "index": first_index,
            "chunks": first_chunks,
            "notebook_corpus": case_notebook.corpus_text(max_chars=120_000),
            "notebook_meta": result.get("case_notebook") or {},
        }
        # Prefer durable cache (survives process restart); fall back to in-memory dict.
        try:
            from backend.services import qa_session_cache
            qa_session_cache.put(session_id, session_payload)
        except Exception:
            if isinstance(global_cache, dict):
                global_cache[session_id] = session_payload
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
        for gpath in guideline_paths:
            if gpath and os.path.exists(gpath):
                try:
                    os.remove(gpath)
                except OSError:
                    pass


def run_job_audit(job: AuditJob, file_items, guideline, user_question, global_cache, guidelines=None) -> dict:
    def progress(phase, pct, message):
        from backend.services.audit_jobs import _update
        _update(job, phase=phase, progress=pct, message=message)

    return run_full_audit(
        file_items, guideline, user_question, global_cache, progress=progress, guidelines=guidelines
    )
