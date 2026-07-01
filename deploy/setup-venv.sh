#!/usr/bin/env bash
# Create project venv and install dependencies (Ubuntu 24.04+ safe).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi

if [[ ! -d venv ]]; then
  python3 -m venv venv
  echo "Created venv at $ROOT/venv"
fi

# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "DB init:"
echo "  source venv/bin/activate && python backend/db/init_db.py"
echo "  source venv/bin/activate && python scripts/create_user.py admin@example.com pass admin"
echo ""
echo "Done. Activate with: source $ROOT/venv/bin/activate"
echo "Backend:  $ROOT/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000"
echo "Streamlit: $ROOT/venv/bin/streamlit run frontend/app.py"
