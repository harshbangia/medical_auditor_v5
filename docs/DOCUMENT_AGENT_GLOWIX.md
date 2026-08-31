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
- **PDF only** via `generate_glowix_expert_opinion_pdf` (letterhead)
- No HTML download
- Patient / Claim / Insurance cards come from the same JSON as the PDF
