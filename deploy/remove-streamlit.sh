#!/usr/bin/env bash
# Stop and disable legacy Streamlit UI on EC2 (Next.js replaces it).
set -euo pipefail

echo "==> Looking for Streamlit processes / units…"

for unit in streamlit glowix-streamlit glowix-ui medical-auditor-ui; do
  if systemctl list-unit-files "${unit}.service" 2>/dev/null | grep -q "${unit}"; then
    echo "Stopping ${unit}.service"
    sudo systemctl stop "${unit}" || true
    sudo systemctl disable "${unit}" || true
  fi
done

# Kill any leftover streamlit process
if pgrep -af streamlit >/dev/null 2>&1; then
  echo "Killing leftover streamlit processes:"
  pgrep -af streamlit || true
  sudo pkill -f "streamlit run" || true
fi

echo ""
echo "Ensure nginx / points to Next.js (127.0.0.1:3000), not 8501:"
echo "  grep -A3 'location /' /etc/nginx/sites-available/glowix"
echo ""
echo "Done. Keep glowix + glowix-web running:"
echo "  sudo systemctl status glowix glowix-web --no-pager"
