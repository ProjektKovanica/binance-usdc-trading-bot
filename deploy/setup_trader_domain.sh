#!/bin/bash
set -e
DOMAIN="trader.kovanica.online"
CONF_SRC="$(dirname "$0")/nginx-trader.kovanica.online.conf"
BOT="${BOT_DIR:-/root/trading-bot}"

echo "=== Phase A: nginx for $DOMAIN ==="
if [ ! -f "$CONF_SRC" ]; then
  echo "Missing $CONF_SRC"
  exit 1
fi

sudo cp "$CONF_SRC" /etc/nginx/sites-available/trader.kovanica.online
sudo ln -sf /etc/nginx/sites-available/trader.kovanica.online /etc/nginx/sites-enabled/trader.kovanica.online
sudo nginx -t
sudo systemctl reload nginx

echo "DNS: point $DOMAIN A record to this server IP"
echo "SSL: sudo certbot --nginx -d $DOMAIN"
echo "=== Phase B deps ==="
cd "$BOT"
source .venv/bin/activate
pip install -q "python-jose[cryptography]" "passlib[bcrypt]" "bcrypt>=4.0.0,<4.1" email-validator
pm2 restart dashboard-api --update-env
echo "Done. Test: curl -s https://$DOMAIN/api/health  (or http:// until SSL)"
