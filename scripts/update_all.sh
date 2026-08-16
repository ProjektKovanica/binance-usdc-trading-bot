#!/bin/bash
# Jedna skripta: kopira updatee + restart PM2
set -e
BOT="${BOT_DIR:-/root/trading-bot}"
SRC="${1:-/root/bot-update}"

echo "=== USDC Trading Bot update ==="
echo "BOT=$BOT"
echo "SRC=$SRC"

if [ ! -d "$BOT" ]; then
  echo "ERROR: $BOT ne postoji"
  exit 1
fi

# Ako je SRC zip, raspakiraj
if [ -f "$SRC" ] && [[ "$SRC" == *.zip ]]; then
  TMP=$(mktemp -d)
  unzip -o "$SRC" -d "$TMP"
  if [ -d "$TMP/trading-bot" ]; then
    SRC="$TMP/trading-bot"
  else
    SRC="$TMP"
  fi
  echo "Unpacked to $SRC"
fi

if [ ! -d "$SRC" ]; then
  echo "ERROR: SRC folder/zip nije pronađen: $1"
  echo "Usage: bash update_all.sh /root/FULL-update.zip"
  echo "   or: bash update_all.sh /root/bot-update   # folder with trading-bot/ inside"
  exit 1
fi

# Ako SRC sadrži trading-bot podfolder
if [ -d "$SRC/trading-bot" ]; then
  SRC="$SRC/trading-bot"
fi

mkdir -p "$BOT/agent" "$BOT/config" "$BOT/scripts" "$BOT/data" "$BOT/core" \
         "$BOT/risk" "$BOT/dashboard/backend" "$BOT/dashboard/frontend/src"

copy_if() {
  local rel="$1"
  if [ -e "$SRC/$rel" ]; then
    cp -a "$SRC/$rel" "$BOT/$rel"
    echo "  OK $rel"
  fi
}

echo "--- Copy files ---"
# Agent
if [ -d "$SRC/agent" ]; then
  cp -a "$SRC/agent/." "$BOT/agent/"
  echo "  OK agent/"
fi

copy_if "core/engine.py"
copy_if "risk/manager.py"
copy_if "data/feed.py"
copy_if "dashboard/backend/main.py"
copy_if "dashboard/frontend/src/App.tsx"
copy_if "dashboard/frontend/vite.config.ts"
copy_if "scripts/run_backtest.py"
copy_if "config/runtime_control.json"

if [ -f "$SRC/scripts/update_all.sh" ]; then
  cp "$SRC/scripts/update_all.sh" "$BOT/scripts/update_all.sh"
  chmod +x "$BOT/scripts/update_all.sh"
fi

# .env.example only (ne overwrite .env)
if [ -f "$SRC/.env.example" ]; then
  cp "$SRC/.env.example" "$BOT/.env.example"
  echo "  OK .env.example"
fi

chmod +x "$BOT/scripts/"*.py 2>/dev/null || true
chmod +x "$BOT/scripts/"*.sh 2>/dev/null || true

echo "--- PM2 restart ---"
if command -v pm2 >/dev/null 2>&1; then
  pm2 restart trading-engine dashboard-api dashboard-ui --update-env || pm2 restart all
  sleep 2
  pm2 status
  echo "--- Last engine logs ---"
  pm2 logs trading-engine --lines 15 --nostream || true
else
  echo "PM2 nije instaliran — restartaj ručno"
fi

echo "=== Gotovo ==="
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):5173"
echo "API agent: curl -s http://127.0.0.1:8000/api/agent | head"
