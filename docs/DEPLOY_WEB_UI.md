# Deploying the Next.js Web UI on AWS EC2

This guide replaces the Streamlit UI (`frontend/app.py` on port 8501) with the new React/Next.js app in `web/` on port **3000**, while keeping FastAPI on port **8000**.

## Architecture on EC2

```
Browser
   │
   ▼
nginx :80
   ├── /api/*  →  FastAPI (glowix) :8000
   └── /*      →  Next.js (glowix-web) :3000
```

## Prerequisites on EC2

1. **Node.js 18+** (20 LTS recommended)

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v && npm -v
```

2. **Existing stack** already running:
   - `glowix` systemd service (uvicorn/FastAPI on 8000)
   - PostgreSQL / RDS configured in `.env`
   - nginx serving `/api/`

## One-time setup

SSH into EC2:

```bash
cd ~/medical_auditor_v5
git pull origin cursor/multi-user-admin-dashboard

chmod +x deploy/setup-web.sh
./deploy/setup-web.sh
```

## Nginx changes (required)

```bash
sudo cp deploy/nginx-full.conf /etc/nginx/sites-available/glowix
sudo nano /etc/nginx/sites-available/glowix   # set server_name
sudo ln -sf /etc/nginx/sites-available/glowix /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Remove the old Streamlit proxy on `/` if it pointed to port 8501.

## Environment variables

| Where | Variable | Value |
|-------|----------|-------|
| `web/.env.local` | `NEXT_PUBLIC_API_URL` | `/api` |
| `glowix-web.service` | `PORT` | `3000` |

No new AWS services required (same EC2).

## Security group

| Port | Purpose |
|------|---------|
| 22 | SSH |
| 80 / 443 | nginx only |

Do not open 3000 or 8000 publicly.

## Updates

```bash
git pull && cd web && npm ci && npm run build
sudo systemctl restart glowix-web glowix
```

## Local dev

```bash
# Terminal 1
uvicorn backend.main:app --reload --port 8000

# Terminal 2
cd web && cp .env.example .env.local
# NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```
