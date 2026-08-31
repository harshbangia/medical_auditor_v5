# Connect Glowix “Run audit” to the AI Studio document agent

## What you already proved
Google AI Studio Playground + PDF attach → HTML report (`ai_studio_code (1).html`) is correct.

## What we built in Glowix
Same flow inside the app:

```
User clicks Run audit
  → POST /audit (unchanged)
  → AUDIT_PIPELINE=document_agent
  → upload PDFs to Gemini Files API
  → generateContent(system=AGENTS.md, PDFs + starter prompt)
  → HTML report returned as result.report_html
  → UI shows iframe + Download HTML
```

Legacy OCR pipeline remains available via `AUDIT_PIPELINE=legacy`.

## EC2 setup

```bash
cd ~/medical_auditor_v5
git pull
source venv/bin/activate
pip install -r requirements.txt

nano .env
```

Add / set:

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
AUDIT_PIPELINE=document_agent
DOCUMENT_AGENT_MODEL=gemini-3.1-pro-preview
# or gemini-3.6-flash / gemini-3.7-flash if available on your key
```

```bash
# rebuild web if UI changed
cd web && npm run build && cd ..
sudo systemctl restart glowix glowix-web
```

## User flow
1. New audit → upload case PDFs (+ select guidelines; they are attached as PDFs too)
2. Run audit
3. Progress: uploading → Gemini document agent → done
4. Report page shows HTML preview
5. Download HTML report (open in browser → Print → PDF if needed)

## Notes
- This is **not** Antigravity Managed Agent Sources (GCS). It is the **Playground multimodal** path that already worked for you.
- Timeouts: `GEMINI_HTTP_TIMEOUT_MS=600000` recommended for large packs.
- To roll back: `AUDIT_PIPELINE=legacy` and restart glowix.
