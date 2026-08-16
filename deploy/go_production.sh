#!/bin/bash
set -e
BOT="${BOT_DIR:-/root/trading-bot}"
cd "$BOT/dashboard/frontend"

echo "=== npm install + vite build ==="
npm install --prefer-offline
# Prefer vite-only build
npx vite build || npm run build

if [ ! -f dist/index.html ]; then
  echo "ERROR: dist/index.html missing"
  exit 1
fi

echo "=== nginx production conf ==="
if [ -f "$BOT/deploy/nginx-trader-production.conf" ]; then
  sudo cp "$BOT/deploy/nginx-trader-production.conf" /etc/nginx/sites-available/trader.kovanica.online
  sudo ln -sf /etc/nginx/sites-available/trader.kovanica.online /etc/nginx/sites-enabled/trader.kovanica.online
  sudo nginx -t && sudo systemctl reload nginx
fi

echo "=== PM2: stop vite dev UI ==="
pm2 stop dashboard-ui 2>/dev/null || true
pm2 restart dashboard-api --update-env || true
pm2 save || true

echo "=== OK ==="
echo "Static: $BOT/dashboard/frontend/dist"
echo "Site: https://trader.kovanica.online"
