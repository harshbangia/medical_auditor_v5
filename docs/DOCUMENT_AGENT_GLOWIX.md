# Connect Glowix “Run audit” to the AI Studio document agent

## Flow
```
User clicks Run audit
  → upload PDFs to Gemini Files API (same as AI Studio)
  → Gemini returns Expert Opinion JSON
  → Glowix UI cards filled from JSON
  → Download Expert Opinion PDF = Glowix letterhead (existing generator)
```

## EC2
```bash
AUDIT_PIPELINE=document_agent
DOCUMENT_AGENT_MODEL=gemini-3.1-pro-preview
GEMINI_API_KEY=...
```

Rollback: `AUDIT_PIPELINE=legacy`

## Output
- **PDF only** via Medical Audit Report letterhead proforma (sections 1–9 narrative — not Q&A)
- No HTML download
- Patient / Claim / Insurance cards come from the same JSON as the PDF
- Observations render as narrative §6 topics; itemised `billing_disallowances` and `documentation_gaps` included
