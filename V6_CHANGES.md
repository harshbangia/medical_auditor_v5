# v6 Reliability Changes — Implementation Notes

These changes implement the four priorities from `ARCHITECTURE_REDESIGN_v6.md`
as **integrated patches to the existing v5 codebase** (no rewrite — the project
still runs the same way). Core principle: *ground everything, fabricate nothing,
abstain loudly when evidence is missing.*

## What changed, by file

### New: `backend/ai/report_guards.py`
The final assembly-stage validator. Runs last in the pipeline and can only
remove/downgrade unsupported content, never invent it.
- `strip_fabricated_financials` — if no real bill amount is traceable to the
  documents, blanks every financial field, sets `financial_review.status =
  "not_available"`, empties `claim_savings`, and scrubs stale money figures out
  of the inference paragraph and report-summary bullets.
- `enforce_date_plausibility` — drops out-of-window dates from **every** field
  (claim dates, timeline, `clinical_findings`, `all_document_dates`), including
  LLM-produced ones. This is what kills the `18/01/2023` leak.
- `canonicalize_entities` — fixes OCR'd insurer names (`LiwITED → LIMITED`) and
  strips address crumbs / typos from hospital names (`… Near CIVIL Hospital`).
- `compute_report_confidence` — sets `report_confidence` (High/Medium/Low) and
  `manual_review_required` + `manual_review_reasons`.

### `backend/ai/guideline_selector.py` (rewritten)
- `select_guideline_ranked(case_text, diagnosis_hint)` returns the best guideline
  **plus a confidence (0–1), ranked candidates, and the model's diagnosis**.
- Confidence is penalised when the top match barely leads #2 or when the
  guideline filename's specialty doesn't appear in the case. `< 0.6` → the
  pipeline flags the report for manual confirmation instead of silently
  auditing against the wrong protocol (the orthopedics-for-hematology bug).
- `select_guideline()` is kept as a thin backward-compatible wrapper.

### `backend/utils/pdf_reader.py` (handwriting/OCR)
- Vision transcription now also fires on **scan-like pages** (has an image +
  few machine-readable words), not only near-empty pages — so a scanned page
  with a printed letterhead over a handwritten body is transcribed.
- `_page_image_for_transcription` now **renders the full page at high DPI**
  (default raised 220 → 300) instead of pulling one embedded image, and the old
  `400×400 / 8 KB` size gate that dropped legitimate scans is removed.

### `backend/utils/claim_details_extractor.py`
- `_infer_nature_of_admission` is now **evidence-gated**: "Emergency" requires an
  explicit emergency-admission marker (casualty, emergency dept, RTA, etc.).
  Lone diagnosis hints (troponin, chest discomfort, ACS) no longer trigger it,
  and pre-auth/query documents default to "Planned / Elective".

### `backend/ai/audit_engine.py`
- The financial section of the audit prompt now forbids inventing amounts:
  if no itemised bill appears, the model must leave all financial fields empty
  and return an empty line-item array. (Belt-and-braces with `report_guards`.)

### `backend/services/audit_pipeline.py`
- Uses `select_guideline_ranked` and captures `guideline_selection`.
- Calls `apply_report_guards(...)` as the last step before returning.
- `_ensure_result_shape` seeds `report_confidence`, `manual_review_required`,
  `manual_review_reasons`, `guideline_selection`.

### `backend/utils/pdf_generator.py`
- Renders a **confidence + "⚠ MANUAL REVIEW REQUIRED"** banner near the top.
- Section 9 prints a clear "Financial review not available" note (with reason)
  instead of fabricated figures when no bill was provided.

## New environment variables (all optional, sensible defaults)

| Variable | Default | Purpose |
|---|---|---|
| `VISION_OCR_DPI` | `300` | Render DPI for handwriting transcription (was 220) |
| `VISION_SCANLIKE_MAX_WORDS` | `90` | Below this native word count, an image page is sent to vision OCR |
| `DATE_WINDOW_YEARS` | `1` | Max years a date may differ from the claim anchor before it's dropped |
| `GUIDELINE_SELECTOR_MODEL` | `gpt-4o-mini` | Model for ranked guideline selection |

## New fields in the audit result JSON
`report_confidence`, `manual_review_required`, `manual_review_reasons`,
`guideline_selection`, and `financial_review.status` / `financial_review.note`.

## Verification done
- All `backend/**/*.py` byte-compile cleanly.
- `report_guards` verified on the reproduced Mr. Naveen failure conditions:
  fabricated financials blanked, `18/01/2023` removed from every field, insurer
  and hospital names canonicalised, stale savings text scrubbed from the
  inference/summary, `manual_review_required = True`, and a valid `18-Oct-2025`
  date preserved.
- Full end-to-end run requires your environment (OpenAI key, S3, PyMuPDF), which
  isn't available in this workspace — run your existing `backend/tests` there.

## Suggested next steps (from the redesign doc, not yet built)
Phases 1–5: per-page `PageRecord` ingestion with structured/ensemble
transcription, the `facts/` layer with a `Fact` provenance model, semantic
guideline-catalogue embeddings, the closed-fact audit reasoner + Pydantic
schema, and the golden-set evaluation harness.
