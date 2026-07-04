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

## Hotfix round (after first live run — Naveen Kumar report)

The first live run surfaced two problems: a 12-minute runtime and a few new
regressions. Fixed:

1. **Performance — vision OCR was fully sequential.** Every scan-like page was a
   serial gpt-4o high-detail call. Now the page renders happen sequentially
   (fast) and the transcription calls run in a thread pool
   (`VISION_OCR_WORKERS`, default 5). Also: `MAX_VISION_OCR_PAGES` lowered
   25 → 12 (per document) and `VISION_OCR_DPI` 300 → 250. Expected wall-clock
   drops from ~12 min to a few minutes on a 5-PDF handwritten claim.
2. **Date guard was too aggressive.** It deleted the *real* admission date
   (`18/10/2023`, corroborated by both the pre-auth and the discharge) because
   it was 2 years from the query-letter's proposed date — leaving empty
   admission fields and a summary that contradicted the claim block.
   `enforce_date_plausibility` is now **corroboration-aware**: it never deletes a
   date the deterministic extractor chose as a document-of-record value, and
   only scrubs *uncorroborated* out-of-window dates from LLM free text (so the
   rogue `18/01/2023` follow-up is still removed). Impossible dates (future /
   pre-2015) are always dropped. Conflicting-but-plausible dates are surfaced in
   the discrepancy table, not silently deleted.
3. **"Emergency" on a planned pre-auth.** The LLM set Emergency (it saw ICU);
   nothing overrode it. New `correct_nature_of_admission` forces
   "Planned / Elective" when planned markers (pre-auth / proposed hospitalisation)
   are present and no explicit emergency-admission marker exists.
4. **Duplicated hospital name** ("SHRI HARI … Shri Hari …") is de-duplicated in
   `canonicalize_entities` when the tail merely repeats the head.
5. **Specialty tables widened** (hemorrhoids / piles / fistula / hernia →
   gastroenterology) so mismatched auto-selected guidelines are flagged.

Note on the guideline still showing `6_orthopedics_goi.pdf` for a hemorrhoids
case: that guideline was almost certainly **manually selected** in the UI
(`report_confidence` was Medium with no manual-review flag, which only happens
for a user-chosen guideline). If it was auto-selected, the new specialty
cross-check now drops confidence below 0.6 and raises the manual-review flag.
Either way, if the S3 catalogue has no proctology/GI guideline, the audit should
be run against the closest correct protocol or the case flagged for review —
the engine can't audit hemorrhoids meaningfully against an orthopedics guideline.

## Suggested next steps (from the redesign doc, not yet built)
Phases 1–5: per-page `PageRecord` ingestion with structured/ensemble
transcription, the `facts/` layer with a `Fact` provenance model, semantic
guideline-catalogue embeddings, the closed-fact audit reasoner + Pydantic
schema, and the golden-set evaluation harness.
