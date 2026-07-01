#!/usr/bin/env bash
# One-shot DB bootstrap on EC2: stop backend, init tables, create admin, restart.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ADMIN_EMAIL="${1:-}"
ADMIN_PASSWORD="${2:-}"

if [[ -z "$ADMIN_EMAIL" || -z "$ADMIN_PASSWORD" ]]; then
  echo "Usage: bash deploy/bootstrap-db.sh <admin-email> <admin-password>"
  exit 1
fi

echo "Stopping glowix (avoids DB lock during init)..."
sudo systemctl stop glowix || true

source "$ROOT/venv/bin/activate"
python "$ROOT/backend/db/init_db.py"
python "$ROOT/scripts/create_user.py" "$ADMIN_EMAIL" "$ADMIN_PASSWORD" admin

echo "Starting glowix..."
sudo systemctl start glowix
sleep 2
curl -s http://127.0.0.1:8000/health/db || true
echo ""
echo "Done. Log in to Streamlit as $ADMIN_EMAIL"
