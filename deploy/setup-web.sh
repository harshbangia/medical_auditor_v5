#!/usr/bin/env bash
# Build and start the Next.js web UI on EC2
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/web"

echo "==> Installing Node.js deps (web/)"
cd "$WEB"
npm ci

echo "==> Building Next.js (standalone)"
cp -n .env.example .env.local 2>/dev/null || true
npm run build

echo "==> Installing systemd unit (glowix-web)"
sudo cp "$ROOT/deploy/glowix-web.service" /etc/systemd/system/glowix-web.service
sudo sed -i "s|/home/ubuntu/medical_auditor_v5|$ROOT|g" /etc/systemd/system/glowix-web.service
sudo systemctl daemon-reload
sudo systemctl enable glowix-web
sudo systemctl restart glowix-web

echo "==> glowix-web status"
systemctl is-active glowix-web && systemctl status glowix-web --no-pager -l | head -15

echo ""
echo "Done. Point nginx / to port 3000 (see deploy/nginx-full.conf)."
