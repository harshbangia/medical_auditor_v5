#!/usr/bin/env bash
# Run on EC2 as ubuntu (needs sudo for nginx):
#   cd ~/medical_auditor_v5 && bash deploy/fix-504-on-ec2.sh
set -euo pipefail

echo "========== 1. Recent glowix backend logs (last 100 lines) =========="
sudo journalctl -u glowix -n 100 --no-pager || journalctl -u glowix -n 100 --no-pager

echo ""
echo "========== 2. Active audit lines (if any running now) =========="
sudo journalctl -u glowix --since "30 min ago" --no-pager | grep -E '\[audit:|OCR|RAG|run_audit|audit success|audit failed' || true

echo ""
echo "========== 3. Current nginx proxy timeouts =========="
sudo nginx -T 2>/dev/null | grep -iE 'proxy_read_timeout|proxy_connect_timeout|proxy_send_timeout|client_max_body_size|location /api|proxy_pass' || echo "(no matches — check /etc/nginx/sites-enabled/)"

echo ""
echo "========== 4. Patch nginx /api/ block for 30-minute audits =========="
NGINX_SITE=""
for f in /etc/nginx/sites-enabled/*; do
  if sudo grep -q 'location /api' "$f" 2>/dev/null; then
    NGINX_SITE="$f"
    break
  fi
done

if [[ -z "$NGINX_SITE" ]]; then
  echo "WARNING: Could not find location /api in sites-enabled. Edit nginx manually."
  echo "Use deploy/nginx-api.conf as reference."
else
  echo "Found API config in: $NGINX_SITE"
  if sudo grep -q 'proxy_read_timeout' "$NGINX_SITE"; then
    echo "Updating existing proxy_read_timeout values..."
    sudo sed -i 's/proxy_read_timeout[^;]*/proxy_read_timeout 1800s/g' "$NGINX_SITE"
    sudo sed -i 's/proxy_connect_timeout[^;]*/proxy_connect_timeout 1800s/g' "$NGINX_SITE"
    sudo sed -i 's/proxy_send_timeout[^;]*/proxy_send_timeout 1800s/g' "$NGINX_SITE"
  else
    echo "Inserting timeout directives after 'location /api'..."
    sudo sed -i '/location \/api/a\        proxy_connect_timeout 1800s;\n        proxy_send_timeout 1800s;\n        proxy_read_timeout 1800s;\n        client_max_body_size 100M;' "$NGINX_SITE"
  fi
  sudo nginx -t
  sudo systemctl reload nginx
  echo "nginx reloaded."
fi

echo ""
echo "========== 5. glowix service status =========="
systemctl is-active glowix && systemctl status glowix --no-pager -l | head -20

echo ""
echo "========== 6. Verify backend code has smart OCR (optional) =========="
if grep -q 'Skipping full-document OCR' ~/medical_auditor_v5/backend/utils/pdf_reader.py 2>/dev/null; then
  echo "OK: smart OCR optimization is present in pdf_reader.py"
else
  echo "MISSING: deploy latest code (git pull) and restart glowix:"
  echo "  cd ~/medical_auditor_v5 && git pull && sudo systemctl restart glowix"
fi

echo ""
echo "Done. Re-run an audit, then watch live logs:"
echo "  sudo journalctl -u glowix -f"
