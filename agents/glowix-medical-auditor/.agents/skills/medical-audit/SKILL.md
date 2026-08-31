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

## Step 3 — Clinical & billing review
Assess medical necessity, investigations, antibiotics, ICU need, room category, missing documents, overbilling.

## Step 4 — Deep Q&A (§6)
Write 4–6 questions. Each answer must be multi-paragraph when evidence exists, covering as relevant:

- Timeline of events (injury/onset → consults → admission → procedure)
- Fresh vs old / acuity using guideline duration thresholds when provided
- PED / waiting periods / accident exemptions citing **only** clauses present in uploaded policy PDF
- Imaging / lab pathognomonic findings (what is on the report — do not invent)
- Antibiotic stewardship / cultures if antibiotics billed
- Identity consistency (OCR variants vs true mismatch)
- Documentation gaps and exact queries to raise

Each analysis must name source documents (filenames).

Answer labels: Supported | Partially Supported | Not Supported | Insufficient Evidence

## Step 5 — Verdict
- Recommend or not recommend with clear rationale
- List queries for hospital/insurer
- Compliance verdict vs uploaded guideline(s)

## Quality bar (match NotebookLM POC)
- Correct IDs and demographics on first pass
- No OCR junk as “high FWA”
- Deep, clause-aware, timeline-aware observations
- Honest NA / Insufficient Evidence when pages don’t support a claim
