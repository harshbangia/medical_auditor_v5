---
name: medical-audit
description: Produce NotebookLM-depth Glowix Expert Opinion Q&A from uploaded claim PDFs and guidelines.
---

# Medical Audit Skill

## Step 1 — Document inventory
List every uploaded file and its role (Assessor, Aadhaar, Final Bill, Discharge, Labs, Imaging, Policy, Guideline, Other).

## Step 2 — Seal identity & finance
From Assessor (preferred) + Aadhaar + Final Bill + Discharge, lock:

| Field | Source preference |
|-------|-------------------|
| Patient name | Assessor insured name ↔ Aadhaar |
| Age / Gender | Assessor age or DOB→age at DOA; Aadhaar DOB/gender |
| Policy No. | Assessor |
| Claim Incident No. | Assessor claim / sub-claim root |
| Hospital | Assessor / bill / discharge letterhead |
| DOA / DOD | Assessor / discharge / bill |
| Diagnosis | Discharge / Assessor / clinical notes (not radiology-only impressions as sole diagnosis) |
| Total Hospital Bill | Final bill grand total |
| Claimed Amount | Assessor claimed amount (note if differs from bill) |

If Assessor insured ≠ clinical patient (clearly different person), stop and warn: wrong document pack.

## Step 3 — Clinical & forensic billing review
Assess medical necessity, investigations, antibiotics, ICU need, room category, missing documents, overbilling.

**Forensic bill pass (mandatory — NotebookLM depth):**
- Compare every major final-bill line to OT notes, anesthesia chart, WHO checklist, progress notes, pharmacy.
- Flag: clinician role misclassification (e.g. physiotherapist billed as super-specialist), unrendered equipment (laparoscopy tower when only open laparotomy done), duplicate/bundled surgeon fees, IRDAI List-I non-payables.
- Flag: investigations billed (HPE / GeneXpert / AFB / culture) without corresponding reports in the pack.
- Flag: Discharge Summary that omits the definitive OT pathology (e.g. Abdominal Cocoon).
- Flag: missing indoor progress notes for date ranges within the stated LOS.
- For each finding, record title, rupee amount (if any), evidence quote/filename, and audit action (disallow / query / withhold).

## Step 4 — Deep Q&A (§6 / observations)
Write **6–8** questions. Each `analysis` must be multi-paragraph (≥180 words when evidence exists), covering as relevant:

- Timeline of events (onset → consults → admission → procedure → postop course)
- Fresh vs old / acuity using guideline duration thresholds when provided
- PED / waiting periods / accident exemptions citing **only** clauses present in uploaded policy PDF
- Imaging / lab pathognomonic findings (what is on the report — do not invent)
- Antibiotic stewardship / cultures if antibiotics billed
- Identity consistency (OCR variants vs true mismatch)
- **At least 2** forensic billing / documentation-gap questions with rupee amounts and named sources
- When hernia + obstruction coexist: clearly separate incidental concomitant repair vs hernia-induced obstruction (CT transition zone, reducibility, OT findings)

Each analysis must name source documents (filenames).

Answer labels: Supported | Partially Supported | Not Supported | Insufficient Evidence

## Step 5 — Verdict
- Recommend or not recommend with clear rationale
- List **numbered** deductions (amount + reason) and hospital queries
- Compliance verdict vs uploaded guideline(s)
- `financial_review.non_payable_amount` must reflect summed `billing_disallowances` when evidence supports them

## Quality bar (match NotebookLM POC)
- Correct IDs and demographics on first pass
- No OCR junk as “high FWA”
- Deep, clause-aware, timeline-aware, **line-item forensic** observations
- Honest NA / Insufficient Evidence when pages don’t support a claim
- Adjudication remarks a claims officer can act on without re-reading the whole file
