#!/usr/bin/env bash
# Fix 502 Bad Gateway on login — backend (glowix / uvicorn :8000) is not reachable.
# Run on EC2:
#   cd ~/medical_auditor_v5 && bash deploy/fix-502-on-ec2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "========== 1. glowix service status =========="
if systemctl is-active --quiet glowix 2>/dev/null; then
  echo "glowix: active"
else
  echo "glowix: NOT active (this causes nginx 502 on /api/*)"
fi
sudo systemctl status glowix --no-pager -l | head -25 || true

echo ""
echo "========== 2. Is anything listening on :8000? =========="
if command -v ss >/dev/null 2>&1; then
  ss -tlnp | grep ':8000' || echo "Nothing listening on port 8000"
else
  sudo lsof -i :8000 || echo "Nothing listening on port 8000"
fi

echo ""
echo "========== 3. Recent glowix crash logs =========="
sudo journalctl -u glowix -n 80 --no-pager || true

echo ""
echo "========== 4. Test Python import (catches deploy/import errors) =========="
if [[ -x "$ROOT/venv/bin/python" ]]; then
  if "$ROOT/venv/bin/python" -c "from backend.main import app; print('import ok')"; then
    echo "Backend imports successfully."
  else
    echo "IMPORT FAILED — fix the traceback above, then re-run this script."
    exit 1
  fi
else
  echo "No venv at $ROOT/venv — run: bash deploy/setup-venv.sh"
  exit 1
fi

echo ""
echo "========== 5. Ensure deps are installed =========="
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"
pip install -q -r "$ROOT/requirements.txt"

echo ""
echo "========== 6. Restart glowix =========="
sudo systemctl daemon-reload
sudo systemctl restart glowix
sleep 3

echo ""
echo "========== 7. Verify backend locally =========="
if curl -sf http://127.0.0.1:8000/ >/dev/null; then
  echo "OK: http://127.0.0.1:8000/ responds"
else
  echo "FAIL: backend still not responding on :8000"
  sudo journalctl -u glowix -n 40 --no-pager
  exit 1
fi

echo ""
curl -s http://127.0.0.1:8000/health/db || true
echo ""

echo ""
echo "========== 8. Verify via nginx (/api/) =========="
if curl -sf http://127.0.0.1/api/health/db >/dev/null; then
  echo "OK: nginx -> backend proxy works"
else
  echo "WARN: /api/health/db still failing — check nginx proxy_pass (see deploy/nginx-api.conf)"
  sudo nginx -T 2>/dev/null | grep -A8 'location /api' || true
fi

echo ""
echo "Done. Try logging in again from Streamlit."
