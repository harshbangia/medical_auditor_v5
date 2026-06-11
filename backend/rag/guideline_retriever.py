"""Targeted multi-query guideline retrieval for faster, relevant RAG."""

from typing import Dict, List

from backend.ai.case_profiler import normalize_str_list
from backend.rag.vector_store import search_multi


def _queries_from_profile(profile: dict, case_excerpt: str) -> List[str]:
    diagnosis = profile.get("diagnosis") or ""
    age = profile.get("age") or ""
    procedures = normalize_str_list(profile.get("procedures"))
    imaging = normalize_str_list(profile.get("imaging_mentioned"))
    admission = profile.get("admission_type") or ""

    proc_str = ", ".join(procedures[:5]) if procedures else ""
    img_str = ", ".join(imaging[:5]) if imaging else ""

    queries = [
        f"{diagnosis} diagnosis criteria indication management treatment protocol age {age}",
        f"{proc_str} procedure surgery indication contraindication documentation requirements",
        f"{diagnosis} admission criteria {admission} inpatient outpatient justification",
        f"investigation imaging {img_str} required before treatment {diagnosis}",
        f"medical necessity documentation audit pre-authorization {diagnosis} {proc_str}",
        f"complications deviation contraindication when not to treat {diagnosis}",
    ]

    # Add excerpt anchor so retrieval stays tied to actual case wording
    anchor = " ".join(case_excerpt[:1200].split())
    if anchor:
        queries.append(anchor)

    seen = set()
    unique = []
    for q in queries:
        q = " ".join(q.split()).strip()
        if len(q) < 12 or q in seen:
            continue
        seen.add(q)
        unique.append(q)
    return unique[:8]


def retrieve_guideline_sections(index, chunks, profile: dict, case_text: str, top_k: int = 14) -> str:
    """
    Run several focused searches and merge unique guideline chunks.
    Much faster and more relevant than one giant embedding query.
    """
    queries = _queries_from_profile(profile, case_text[:4000])
    merged = search_multi(index, chunks, queries, top_k_each=4, max_total=top_k)
    if not merged.strip():
        merged = search_multi(index, chunks, [case_text[:3000]], top_k_each=6, max_total=top_k)
    return merged[:12000]
