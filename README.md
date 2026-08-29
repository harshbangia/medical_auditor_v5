# Glowix Medical Auditor

AI-powered medical insurance claim audit platform.

## Stack

- **Backend:** FastAPI (`backend/`) on port 8000
- **Frontend:** Next.js (`web/`) on port 3000
- **Database:** PostgreSQL / RDS
- **Storage:** S3 guidelines + report assets

## Local development

```bash
# Backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Configure .env (LLM_PROVIDER, GEMINI_API_KEY or OPENAI_API_KEY, DATABASE_URL, etc.)
# See .env.example and docs/LLM_PROVIDER_MIGRATION.md
uvicorn backend.main:app --reload --port 8000

# Frontend (separate terminal)
cd web
cp .env.example .env.local
# NEXT_PUBLIC_API_URL=http://localhost:8000
npm install && npm run dev
```

Open http://localhost:3000

## Production (EC2)

See `docs/DEPLOY_WEB_UI.md`.

Services:
- `glowix` — FastAPI
- `glowix-web` — Next.js
- nginx — `/` → 3000, `/api/` → 8000
