# LLM provider migration (OpenAI → Gemini)

## Summary

Glowix Auditor now uses a **provider abstraction**:

- `LLM_PROVIDER=gemini` (default) → Google Gemini via `google-genai`
- `LLM_PROVIDER=openai` → previous OpenAI Responses/Chat/Embeddings path (rollback)

All model calls go through `backend.llm_client.get_llm_provider()`.

NotebookLM is **not** integrated. Document grounding remains:

- Case Notebook (`backend/notebook/`)
- FAISS guideline RAG (embeddings now provider-aware; indexes are versioned)

## Environment

See `.env.example`. Required for Gemini:

```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
AUDIT_MODEL=gemini-2.5-pro
VISION_MODEL=gemini-2.5-flash
VISION_OCR_MODEL=gemini-2.5-pro
EXTRACT_MODEL=gemini-2.5-flash
EMBEDDING_MODEL=gemini-embedding-001
```

If leftover `gpt-4o*` values remain in env while `LLM_PROVIDER=gemini`, the code **ignores** them and uses Gemini defaults.

## Deploy notes

1. `pip install -r requirements.txt` (adds `google-genai`)
2. Set `GEMINI_API_KEY` on EC2 (do not commit secrets)
3. `sudo systemctl restart glowix`
4. First audits after cutover will **rebuild FAISS guideline indexes** (new embedding space)

## Comparison

```python
from backend.llm.compare import compare_complete
print(compare_complete(prompt='Return JSON {"ok": true}', json_mode=True))
```

Requires both `OPENAI_API_KEY` and `GEMINI_API_KEY`.

## Risks

- Handwriting OCR quality may differ from GPT-4o — validate on real cases
- JSON shape drift — existing parsers still repair fenced/malformed JSON
- Rate limits / pricing differ from OpenAI
