# Medical Auditor — v6 Architecture Redesign Proposal

**Author:** Prepared for Harsh · **Date:** 4 July 2026
**Scope:** Full redesign proposal for a larger rebuild, prioritising (1) handwriting/OCR quality, (2) wrong/implausible dates, (3) fabricated financials, (4) guideline mis-selection.
**Reference case:** `Mr._Naveen_Audit_Report_20260704_150544.pdf` (used throughout as the concrete failure example).

---

## 1. Executive summary

The current system (v5) is a linear pipeline that extracts text from claim PDFs, retrieves guideline chunks via FAISS, and asks a single LLM call to produce a large JSON audit. It is *functional* but *not trustworthy* — and for a medico-legal document, trust is the product.

The Naveen report is a compact catalogue of every structural weakness:

| Section of report | What it says | What's actually wrong | Root cause |
|---|---|---|---|
| Guideline referenced | `6_orthopedics_goi.pdf` | Orthopedics guideline used for a "hematological disorder / liver" case | Guideline selection runs on failed-OCR text + bare filenames; alignment gate can't classify hematology so it passes |
| Diagnosis | "Hematological disorder and liver function issues" | Vague, likely not the real diagnosis; contradicts the ortho guideline | Three independent diagnosis judgments (profiler, selector, audit LLM) with no single source of truth |
| Financial review | Rs 100,000 / 80,000 / 20,000; room 40k; meds 30k; tests 30k | **All invented** — no bill was extracted | Prompt orders the model to fill financial tables even with zero source data |
| Follow-up interval | `18/01/2023` | 2023 date inside a 2025–26 claim | Plausibility filter only guards regex dates, never LLM-produced dates |
| Consultation / admission | Both `18-Oct-2025`, source "-" | Collapsed onto the query letter's *proposed* date | No document-of-record precedence enforced on the final object |
| Nature of admission | "Emergency" | It's a planned pre-auth case | Keyword misfire in `_infer_nature_of_admission` |
| Insurance company | "IFFCO TOKIO … **LiwITED**" | OCR error surfaced verbatim | No canonicalisation of known entities |
| Hospital | "Shri Hari Multispeciatity Hospital **Near CIVIL Hospital**" | Address crumb + typo | Name-cleaning heuristics missed this pattern |

**The through-line:** the system *never abstains*. When data is missing or unreadable, it fills the gap with a confident-sounding invention instead of saying "insufficient evidence." Every fix below serves one principle:

> **Ground everything, fabricate nothing, and attach a confidence + provenance to every field. When grounding is absent, abstain loudly rather than guess.**

---

## 2. Why v5 fails (root-cause analysis)

### 2.1 One monolithic LLM call does too much
`run_audit` builds a ~6,000-token prompt that asks a single `gpt-4o` call to simultaneously: classify nature of admission, extract patient/insurance/claim facts, judge clinical necessity, produce financial line items, detect fraud, and write a narrative. Anything the model can't ground, it *interpolates* — which is exactly where invented financials and phantom "cardiac assessment YES" come from. The prompt even instructs it to "Populate claim_savings_line_items with billed vs admissible amounts" unconditionally.

### 2.2 No single source of truth for the diagnosis
- `case_profiler.extract_case_profile` → `gpt-4o-mini` guesses a diagnosis.
- `guideline_selector.select_guideline` → a *separate* `gpt-4o-mini` call guesses the best filename from raw case text + filenames.
- `audit_engine.run_audit` → `gpt-4o` writes yet another diagnosis into `claim_details.diagnosis`.

These three never reconcile. In the Naveen case the selector landed on orthopedics while the audit LLM wrote "hematological disorder." Both can't be right, and nothing catches the contradiction.

### 2.3 The alignment gate is blind to unlisted specialties
`guideline_alignment` only blocks on a *clear* specialty mismatch derived from hard-coded keyword tables. Hematology/hepatology aren't in `_PRIMARY_DIAGNOSIS_KEYWORDS`, so `_primary_case_specialties()` returns an empty set and `check_guideline_alignment` returns `aligned=True, reason="case_primary_unclassified"`. The one safety net that should have caught orthopedics-for-hematology was structurally incapable of it.

### 2.4 OCR/handwriting is a lossy, all-or-nothing cascade
`pdf_reader.extract_text_and_images` decides OCR strategy by *total* character count (`MIN_NATIVE_TEXT=1000`). Vision transcription only fires on pages with `< VISION_OCR_MIN_NATIVE_CHARS (120)` chars, and `_page_image_for_transcription` skips embedded images under `400×400 px` or `8 KB`. A scanned discharge page with a printed letterhead (a few hundred native chars) plus a body of handwriting is treated as "already has text" and the handwriting is never transcribed. That is almost certainly why Naveen's real diagnosis never made it into the case text — leaving the LLM to invent one.

### 2.5 Dates: two parallel truths that never reconcile
There are two date systems: the deterministic regex extractor (`claim_details_extractor`, document-type aware, with a `reference_year` plausibility filter) and the LLM's free-text dates inside `timeline` / `clinical_findings`. Only the former is validated. The `18/01/2023` follow-up date came straight from the LLM's `clinical_findings` and bypassed every guard. Worse, `merge_claim_details_into_result` overwrites LLM dates with regex dates for the *primary* fields only, so the two systems produce a report that contradicts itself (admission 18-Oct-2025 in the table, follow-up 18/01/2023 in the timeline).

### 2.6 No confidence, no evaluation, no schema contract
`_parse_audit_json` does regex surgery on the model's output and hopes it's valid JSON. There is no schema validation (Pydantic), no per-field confidence, no golden-set regression tests for extraction accuracy. You cannot currently answer "did last week's change make date extraction better or worse?" — which is why reliability drifts.

---

## 3. Target v6 architecture

Shift from *"one big call that writes a report"* to *"a grounded extraction graph that assembles a report from verified facts."*

```
                         ┌──────────────────────────────────────────────┐
                         │                INGESTION LAYER                │
   claim PDFs  ─────────▶│  Per-page router → native | tesseract | LLM   │
                         │  vision OCR. Emits PageRecord[] w/ provenance │
                         └───────────────────┬──────────────────────────┘
                                             │ PageRecord[]  (text, doc_type, page, source, method, confidence)
                                             ▼
                         ┌──────────────────────────────────────────────┐
                         │            FACT EXTRACTION LAYER              │
                         │  Deterministic extractors + LLM extractors,   │
                         │  each returning Fact{value, source, page,     │
                         │  method, confidence}. NO judgments here.      │
                         └───────────────────┬──────────────────────────┘
                                             │ CaseFacts (dates, parties, diagnosis candidates, bill lines)
                       ┌─────────────────────┼─────────────────────┐
                       ▼                     ▼                     ▼
             ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
             │ DIAGNOSIS RESOLVE│  │  DATE RESOLVER    │  │ FINANCIAL RESOLVE │
             │ single source of │  │ precedence + year │  │ only from real    │
             │ truth + specialty│  │ plausibility on   │  │ bill lines; else  │
             │ classification   │  │ ALL dates         │  │ abstain           │
             └────────┬─────────┘  └─────────┬────────┘  └─────────┬────────┘
                      │                       │                     │
                      ▼                       │                     │
             ┌──────────────────┐             │                     │
             │ GUIDELINE MATCHER │◀── specialty + diagnosis         │
             │ semantic, top-k,  │                                  │
             │ confidence-scored │                                  │
             └────────┬─────────┘                                  │
                      ▼ RAG chunks                                  │
             ┌──────────────────────────────────────────────────────┴───┐
             │                    AUDIT REASONER                          │
             │  Reasons ONLY over verified CaseFacts + retrieved chunks.  │
             │  Cites factID for every claim; cannot introduce new facts. │
             └───────────────────────────┬──────────────────────────────┘
                                         ▼
             ┌──────────────────────────────────────────────────────────┐
             │  VALIDATION & ASSEMBLY (Pydantic schema + guards)         │
             │  Reject/blank any field lacking a backing fact.           │
             │  Attach confidence + provenance. Render report.           │
             └──────────────────────────────────────────────────────────┘
```

Two invariants make the whole thing trustworthy:

1. **Facts carry provenance.** Every extracted value is a `Fact` object — `{value, source_file, page, method, confidence, evidence_snippet}`. The report renderer refuses to display a value without a backing fact.
2. **Reasoners cannot mint facts.** The audit LLM receives a *closed* set of verified facts and guideline chunks. Its job is to judge and cite, not to supply missing numbers or dates. A post-validation step strips any figure/date the model emits that has no matching `Fact`.

---

## 4. Priority 1 — Handwriting & OCR quality

### 4.1 Problems
- Strategy chosen by *document-total* text, not per page.
- Vision OCR skipped on pages that have *some* printed text but a handwritten body.
- Embedded-image size gate (`400×400`, `8 KB`) drops legitimate scans.
- No retry, no ensemble, no per-page confidence, no quality signal back to the user.

### 4.2 Redesign: a per-page ingestion router
Make the *page* the unit of decision, and record how each page was read.

```python
# backend/ingestion/page_router.py  (new)
from dataclasses import dataclass, field

@dataclass
class PageRecord:
    source_file: str
    page: int
    text: str = ""
    method: str = ""          # "native" | "tesseract" | "vision" | "vision+native"
    doc_type: str = ""        # classified downstream
    is_handwritten: bool = False
    ocr_confidence: float = 0.0   # 0..1
    image_b64: str = ""       # kept for clinical-image analysis
    notes: list = field(default_factory=list)

def route_page(page, pdf_path, page_num, source) -> PageRecord:
    native = (page.get_text() or "").strip()
    native_words = len(native.split())
    printed_ratio = _printed_vs_scanned_ratio(page)   # text layer coverage vs page area

    # A page is "scan-like" if it has large images and little machine text
    scan_like = printed_ratio < 0.15 or native_words < 40

    if not scan_like and native_words >= 60:
        return PageRecord(source, page_num, native, method="native",
                          ocr_confidence=0.95)

    # Always render at high DPI for vision — do NOT gate on embedded image size
    img = _render_page(pdf_path, page_num, dpi=VISION_OCR_DPI)   # 300 dpi default in v6
    vision_text, conf, handwritten = transcribe_with_vision(img, page_num, source)

    # Keep native letterhead text AND the vision body — never discard either
    merged = _merge_native_and_vision(native, vision_text)
    return PageRecord(source, page_num, merged,
                      method="vision+native" if native else "vision",
                      is_handwritten=handwritten, ocr_confidence=conf,
                      image_b64=_b64(img))
```

Key changes vs v5:
- **Per-page routing** replaces the document-total threshold. A single handwritten page in an otherwise-typed file is now always transcribed.
- **Render, don't rely on embedded images.** Drop the `400×400 / 8 KB` gate entirely; render every scan-like page at 300 DPI. (v5 used 220; bump for handwriting.)
- **Never discard native text.** Merge printed letterhead + vision body so hospital/specialty context is preserved.
- **Confidence + handwriting flag** are captured per page and surfaced in the report's "Document Sources" section, so an auditor sees *"3 pages read by handwriting AI at medium confidence — verify manually."*

### 4.3 Handwriting accuracy: ensemble + self-consistency
Doctors' handwriting is the hardest input. Two cheap, high-yield upgrades:

1. **Two-pass self-consistency for low-confidence pages.** Transcribe twice (temperature 0 and 0.4); if the diagnosis/drug/date tokens disagree, ask a third "reconciler" call to choose using letterhead + prescription context, and mark disagreements in `UNCERTAIN`. Your existing `_VISION_OCR_PROMPT` disambiguation rules are excellent — keep them; just apply them across the ensemble.
2. **A structured transcription contract.** Have vision return JSON (`document_type`, `header`, `body`, `dates[]`, `drugs[]`, `amounts[]`, `uncertain[]`) instead of free text. Structured fields feed the deterministic extractors directly and make the "did we actually read a diagnosis?" check trivial — if `body` is empty or `uncertain` dominates, the page is flagged low-confidence rather than silently trusted.

3. **Upgrade the vision model.** `VISION_OCR_MODEL` defaults to `gpt-4o`. For handwriting, a current top-tier multimodal model (e.g. GPT-4.1 / o-series vision, or a specialist medical-OCR service) is worth benchmarking on your golden set. Make it a config flag and measure — see §8.

### 4.4 Output signal
Add a hard rule: **if no page yields a confidently-read primary diagnosis, the pipeline does not fabricate one.** It emits `diagnosis: ""` with `documentation_gaps: ["Primary diagnosis could not be read from uploaded documents — manual review required"]` and lowers the overall report confidence. This alone would have prevented the "hematological disorder" invention.

---

## 5. Priority 2 — Dates: one resolver, plausibility on everything

### 5.1 Problems
- LLM dates (`timeline`, `clinical_findings`, `follow-up`) bypass all validation → `18/01/2023`.
- Primary dates collapse onto the query-letter proposed date.
- `nature_of_admission` misfires to "Emergency."

### 5.2 Redesign: a single `DateResolver` that owns every date
No date reaches the report without passing through one resolver.

```python
# backend/facts/date_resolver.py  (new)
CLAIM_WINDOW_YEARS = 2

class DateResolver:
    def __init__(self, reference_year: int | None):
        self.ref = reference_year

    def accept(self, raw: str, field: str, doc_type: str, source) -> Fact | None:
        parsed = parse_flexible_date(raw, self.ref)
        if not parsed:
            return None
        # Plausibility applies to EVERY date, whatever its origin (regex OR llm)
        if self.ref and abs(parsed.year - self.ref) > CLAIM_WINDOW_YEARS:
            return None            # kills 18/01/2023 in a 2026 claim
        if parsed.year < 2015:     # stale-scan guard when no anchor
            return None
        return Fact(value=fmt(parsed), field=field, source=source,
                    doc_type=doc_type, method="date_resolver",
                    confidence=_precedence_confidence(field, doc_type))
```

Then, critically, **run LLM-produced dates through the same gate:**

```python
# in validation/assembly, before rendering
for row in result.get("timeline", []) + result.get("clinical_findings", []):
    d = extract_date_token(row)
    if d and not date_resolver.accept(d, "llm_date", "llm", row_source):
        row["date"] = ""          # blank implausible LLM dates
        row.setdefault("notes", []).append("date removed: outside claim window")
```

### 5.3 Precedence as data, not scattered `if`s
Keep your existing document-of-record precedence idea (`pre_auth` > `clinical` > `query_letter` for admission) but express it as a single table the resolver consults, and **forbid the proposed-hospitalization date from ever populating `date_of_admission` or `consultation_date`** unless it is the *only* dated source — in which case it is labelled "(proposed, unconfirmed)" rather than silently promoted. This kills the "consultation = admission = 18-Oct" collapse.

### 5.4 Nature of admission: evidence-gated
Replace the keyword-OR in `_infer_nature_of_admission` with a small decision function that requires *positive* emergency evidence and defaults to Elective for pre-auth documents:

```python
def infer_nature(facts) -> tuple[str, float]:
    if facts.has_marker(EMERGENCY_WORDS) and facts.admission_within_24h_of_onset():
        return "Emergency", 0.8
    if facts.doc_type in ("pre_auth", "query_letter") or facts.has_marker(PLANNED_WORDS):
        return "Planned / Elective", 0.8
    return "Unknown", 0.3          # never guess Emergency
```

A pre-auth query letter → **Planned / Elective**, exactly as the guideline prompt already says it should be. The bug is that inference ran on the full OCR blob and hit a stray keyword; scoping it to classified facts fixes it.

### 5.5 Reconcile, don't duplicate
The report should have **one** date model. `timeline` is *rendered from* the resolved `Fact` set, not independently authored by the LLM. This structurally eliminates the "table says X, timeline says Y" contradiction.

---

## 6. Priority 3 — Financials: never fabricate

### 6.1 Problem
The prompt commands the LLM to populate `financial_review` and `claim_savings_line_items` unconditionally. With no bill in evidence it produced Rs 100,000 → 80,000 (a suspiciously round 20% "saving"). In a medico-legal report this is the single most damaging defect.

### 6.2 Redesign: financials are computed, not generated
Financial numbers must **only** come from a deterministic bill parser, never from the reasoning LLM.

```python
# backend/facts/bill_extractor.py
class BillExtraction:
    line_items: list[BillLine]   # {description, billed, admissible?, source, page}
    total_billed: Fact | None
    method: str                  # "extracted" | "none"

def build_financial_review(bill: BillExtraction, deductions) -> dict:
    if bill.method == "none" or not bill.total_billed:
        return {
            "status": "not_available",
            "message": "No itemised hospital bill was found in the uploaded documents. "
                       "Financial review requires the final bill/invoice.",
            # every numeric field stays empty — nothing invented
        }
    ...
```

Rules enforced at assembly:
- **No bill parsed ⇒ every financial field is blank/`not_available`.** The report prints "Financial review pending — bill not provided," not a number.
- **The reasoning LLM is not given a financial JSON schema to fill.** Remove `financial_review`, `claim_savings_line_items`, `total_hospital_bill` etc. from the audit prompt entirely. Those tables are assembled from `BillExtraction` afterwards.
- **Savings are arithmetic**, computed from line-item deductions with explicit reasons, each tied to a `BillLine` source — never a top-down "80% is admissible" guess.
- **Sanity guards:** reject round-number hallucination patterns and any admissible > billed.

This is a small change with outsized value: it removes the class of "invented money" defects completely.

---

## 7. Priority 4 — Guideline selection & RAG

### 7.1 Problems
- `select_guideline` sends bare filenames + raw (often failed-OCR) case text to `gpt-4o-mini` and trusts the returned string.
- The alignment gate can't classify specialties outside its hard-coded tables (hematology, hepatology, urology, gynaecology, ENT… all invisible).
- RAG indexes only guideline *text*; there's no notion of *which guideline* a case belongs to beyond one LLM guess.

### 7.2 Redesign: resolve diagnosis first, then match semantically
**Step 1 — single diagnosis source of truth.** One extraction call produces a structured diagnosis with an ICD-ish category and specialty, *plus a confidence*. This object is the only diagnosis used everywhere (profiler, selector, audit, report). No more three-way disagreement.

```python
@dataclass
class ResolvedDiagnosis:
    text: str
    specialty: str          # from a classifier, not a keyword table
    icd_category: str
    confidence: float
    evidence: str           # snippet the diagnosis came from
```

**Step 2 — semantic guideline matching over a guideline catalogue.** Precompute, once per guideline in S3, an embedding of a rich descriptor (title + specialty + first pages + a one-line LLM summary), cached to disk/S3 alongside the FAISS index. At audit time, embed the resolved diagnosis + procedures and rank guidelines by cosine similarity. Return **top-k with scores**, not a single filename.

```python
def rank_guidelines(diagnosis: ResolvedDiagnosis, catalogue) -> list[GuidelineMatch]:
    q = embed(f"{diagnosis.text} {diagnosis.specialty} {diagnosis.icd_category}")
    scored = [(cosine(q, g.embedding), g) for g in catalogue]
    return sorted(scored, reverse=True)[:5]
```

**Step 3 — confidence-gated selection.**
- Top match strong and clearly ahead of #2 → use it.
- Top match weak, or diagnosis confidence low, or #1≈#2 → **do not silently pick.** Return the ranked candidates to the UI and ask the auditor to confirm (you already have a human in the loop). This is the correct behaviour for the Naveen case: low OCR confidence should have triggered "please confirm guideline," not an orthopedics auto-pick.

**Step 4 — replace the blind alignment gate.** With semantic scores, "alignment" becomes "is the chosen guideline's similarity above threshold?" — which works for *every* specialty, including the ones the keyword tables never knew about. Keep a soft warning banner rather than a hard crash: *"Guideline match confidence: 41% — verify this is the right protocol."*

### 7.3 RAG retrieval improvements
Your multi-query retrieval + clinical re-rank in `vector_store.search_multi` is a good foundation. Targeted upgrades:
- **Better chunking:** current `chunk_text` splits on blank lines at 1,200 chars. Guidelines are highly structured (criteria, indications, contraindications). Add section-aware chunking (split on headings/numbered clauses) and store a `section_title` with each chunk so retrieved evidence can be cited as "Section 4.2 — Indications."
- **Cross-encoder re-rank:** the keyword-count `_score_chunk` is crude. Add an optional cross-encoder (or an LLM relevance score) over the top ~20 candidates before passing 12 to the audit. Bigger relevance lift than tuning `top_k`.
- **Cite retrieved sections:** each guideline deviation in the report should quote the specific retrieved chunk + section it was judged against, so the "guideline expectation" column is verifiable, not paraphrased from the model's memory.
- **Embedding model:** `text-embedding-3-small` is fine for cost; benchmark `-3-large` on retrieval quality for the clinical corpus and make it a flag.

---

## 8. Cross-cutting: the things that make it *stay* reliable

### 8.1 Schema contract (Pydantic)
Replace `_parse_audit_json`'s regex repair with a real schema. Define `AuditReport`, `Fact`, `ClaimDetails`, `FinancialReview` as Pydantic models with validators (dates in-window, `nature_of_admission ∈ enum`, admissible ≤ billed, hospital name not an address crumb). The LLM output is *parsed into* the schema; anything that fails validation is dropped or blanked, not shipped. Use the model's native JSON-schema / structured-output mode instead of asking for JSON in prose and cleaning it up.

### 8.2 Confidence & provenance everywhere
Every rendered field shows its source and, where it matters, a confidence chip. An auditor should be able to see at a glance which values are machine-certain (native text) vs machine-guessed (handwriting AI, low confidence). This is both a UX and a liability improvement.

### 8.3 Abstention as a first-class outcome
Add an overall `report_confidence` and an explicit `manual_review_required: bool`. Triggers: unread diagnosis, no bill, guideline match < threshold, ≥N low-confidence handwritten pages, or any date discrepancy. The Naveen report should have come out as **"Low confidence — manual review required,"** not a polished 5-page verdict.

### 8.4 Evaluation harness (the highest-ROI investment)
You currently can't measure regressions. Build a golden set:
- 15–30 real (de-identified) cases with hand-labelled ground truth: diagnosis, all dates, hospital, bill total, correct guideline.
- Metrics: date extraction accuracy, guideline-match top-1 accuracy, hallucinated-field rate (fields present with no backing fact), diagnosis F1.
- Run on every change; block merges that regress. Your `backend/tests` already has the right instinct (`test_fact_extractors.py`, `test_case_evidence_detector.py`) — extend it into an accuracy benchmark, not just unit tests.

### 8.5 Entity canonicalisation
A small normaliser dictionary + fuzzy match fixes "LiwITED" → "LIMITED", known insurer names, and strips address prefixes from hospital names deterministically. Cheap, and it removes an entire class of "looks unprofessional" defects.

---

## 9. Proposed module layout (v6)

```
backend/
  ingestion/
    page_router.py        # per-page native|tesseract|vision routing → PageRecord[]
    vision_ocr.py         # structured, ensemble handwriting transcription
    doc_classifier.py     # PageRecord → doc_type (query_letter, pre_auth, bill, ...)
  facts/
    models.py             # Fact, CaseFacts, PageRecord (Pydantic)
    date_resolver.py      # single date authority + plausibility
    diagnosis_resolver.py # single diagnosis source of truth + specialty
    bill_extractor.py     # deterministic financials only
    party_extractor.py    # patient / insurer / hospital + canonicalisation
  guidelines/
    catalogue.py          # embed + cache guideline descriptors from S3
    matcher.py            # semantic top-k ranking + confidence gate
    rag.py                # section-aware chunking, retrieval, cross-encoder rerank
  reasoning/
    audit_reasoner.py     # judges over verified facts only; cites factIDs
    schema.py             # AuditReport Pydantic contract + validators
  assembly/
    validator.py          # strip unbacked fields, apply guards, attach confidence
    report_builder.py     # render from validated facts (timeline from Facts)
  eval/
    golden/               # labelled cases
    run_eval.py           # accuracy metrics + regression gate
```

Most of your existing logic maps cleanly: `claim_details_extractor` → `facts/date_resolver` + `party_extractor`; `guideline_selector` + `guideline_alignment` → `guidelines/matcher`; `pdf_reader` → `ingestion/*`; `audit_engine` splits into `reasoning/audit_reasoner` (judgment) and `assembly/*` (facts + rendering). This is a refactor of responsibilities, not a rewrite from zero.

---

## 10. Phased migration plan

You don't need a big-bang rewrite. Sequenced by risk-reduction-per-effort:

**Phase 0 — Stop the bleeding (days, low risk).** These are surgical patches to v5 that kill the worst defects immediately:
1. Guard financials: if no bill total extracted, blank all financial fields + set `not_available`. Remove financial tables from the audit prompt.
2. Run every LLM `timeline`/`clinical_findings` date through the existing `_is_plausible_for_claim` filter.
3. Fix `_infer_nature_of_admission` to require positive emergency evidence; default pre-auth → Elective.
4. Add entity canonicalisation (LIMITED / hospital address strip).
5. Add `manual_review_required` + overall confidence; trigger on missing diagnosis/bill.

**Phase 1 — Ingestion (1–2 weeks).** Per-page router + structured, ensemble vision OCR + per-page confidence surfaced in the report. Directly fixes the handwriting root cause.

**Phase 2 — Facts layer (1–2 weeks).** Introduce `Fact`/`CaseFacts`, the `DateResolver`, `diagnosis_resolver` as single source of truth. Refactor `claim_details_extractor` into it.

**Phase 3 — Guidelines (1 week).** Guideline catalogue embeddings + semantic matcher + confidence gate; retire the keyword alignment tables.

**Phase 4 — Reasoning + schema (1–2 weeks).** Split the monolith call: closed-fact audit reasoner + Pydantic schema + assembly validator that strips unbacked fields.

**Phase 5 — Eval harness (ongoing, start early).** Golden set + accuracy metrics wired into CI. Ideally begin in parallel with Phase 0 so every later phase is measured.

---

## 11. What "better" looks like on the Naveen case

After v6, the same inputs should produce:
- **Guideline:** either the correct specialty guideline (once handwriting is read) or *"Guideline match 38% — please confirm"* — never a silent orthopedics pick.
- **Diagnosis:** the actual read diagnosis, or `""` + "diagnosis unreadable, manual review" — never an invented "hematological disorder."
- **Dates:** admission and consultation only from documents of record; the `18/01/2023` follow-up dropped as out-of-window; timeline rendered from the same resolved dates as the table.
- **Nature:** "Planned / Elective" (pre-auth), not "Emergency."
- **Financials:** "Not available — itemised bill not provided," with zero invented rupee figures.
- **Overall:** `manual_review_required: true`, `report_confidence: low` — an honest report an auditor can trust precisely because it admits what it couldn't read.

---

## 12. Summary of the core principle

Every defect in the Naveen report traces to one habit: **filling gaps with confident inventions.** v6 replaces that habit with grounded facts, single sources of truth, plausibility gates on everything, deterministic financials, semantic (not keyword) guideline matching, and — most importantly — the willingness to abstain. Reliability isn't one fix; it's the discipline of never printing a value the system can't defend.
