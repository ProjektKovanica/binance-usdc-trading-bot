#!/bin/bash
# Ensure PM2 processes see LIVE_TRADING_ENABLED
cd /root/trading-bot
set -a
source .env
set +a
pm2 restart dashboard-api live-worker --update-env
pm2 save
echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
