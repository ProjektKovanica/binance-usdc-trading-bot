# Binance USDC Trading Bot

Professional **paper / live** trading system for **Binance Spot + USDC-M Futures**, focused on **USDC pairs only**.

Includes:

- Modular engine (strategy → risk → execution → portfolio)
- Smart **rule-based agent** (regime, setup score, correlation, funding, auto enable/disable)
- Modern **web dashboard** (FastAPI + React)
- Live settings via shared control file (kill switch, risk limits)
- Telegram alerts
- Historical backtest + walk-forward scripts
- PM2 process management

> **Default mode is paper trading** ($10k simulated). Live trading requires API keys and explicit config change.

---

## Architecture

```
Strategy (EMA-ATR, …)
    → SmartRuleAgent (filter + size mult)
    → RiskManager (limits, kill switch)
    → ExecutionEngine (paper or live)
    → PortfolioManager

MarketDataFeed (REST polling, staggered)
Dashboard API ←→ config/runtime_control.json ←→ Engine
             ←── config/runtime_status.json ──┘
```

| Path | Role |
|------|------|
| `core/engine.py` | Main orchestrator |
| `agent/` | Regime, smart rules, memory, watchdog |
| `risk/` | Position limits, daily loss, kill switch |
| `execution/` | Orders (paper + live path) |
| `data/feed.py` | REST market data (45s staggered) |
| `dashboard/backend` | FastAPI + WebSocket |
| `dashboard/frontend` | React + Tailwind UI |
| `config/runtime_control.json` | Dashboard → engine settings |
| `config/runtime_status.json` | Engine → dashboard live state |
| `scripts/run_paper.py` | Paper entrypoint |
| `scripts/run_backtest.py` | Historical BT from Binance OHLC |
| `scripts/update_all.sh` | One-shot update from zip |

---

## Requirements

- Python **3.11+** (3.12 OK)
- Node.js **18+** (for dashboard UI)
- PM2 (recommended on VPS)
- Binance account (API keys only for **live**)

```bash
cd trading-bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

### Environment

```bash
cp .env.example .env
```

```env
# Mode: paper | live
MODE=paper

# Paper capital
STARTING_CAPITAL_USDC=10000

# Live only (leave empty in paper)
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_SANDBOX=false

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### YAML

See `config/settings.yaml` for symbols, risk defaults, strategies.

### Runtime control (live from dashboard)

`config/runtime_control.json` is written by the API when you change **Risk** settings or **Kill switch**. The engine reloads it every ~3 seconds.

---

## Run (local)

### Paper engine

```bash
source .venv/bin/activate
python scripts/run_paper.py
```

### Dashboard API

```bash
uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000
```

### Dashboard UI

```bash
cd dashboard/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Open: `http://YOUR_IP:5173`  
API health: `http://YOUR_IP:8000/api/health`

---

## Run (VPS + PM2)

Example `ecosystem.config.cjs`:

```js
module.exports = {
  apps: [
    {
      name: "trading-engine",
      cwd: "/root/trading-bot",
      script: "scripts/run_paper.py",
      interpreter: "/root/trading-bot/.venv/bin/python",
      autorestart: true,
      env: { PYTHONUNBUFFERED: "1" },
    },
    {
      name: "dashboard-api",
      cwd: "/root/trading-bot",
      script: "/root/trading-bot/.venv/bin/uvicorn",
      args: "dashboard.backend.main:app --host 0.0.0.0 --port 8000",
      interpreter: "none",
      autorestart: true,
    },
    {
      name: "dashboard-ui",
      cwd: "/root/trading-bot/dashboard/frontend",
      script: "npm",
      args: "run dev -- --host 0.0.0.0 --port 5173",
      interpreter: "none",
      autorestart: true,
    },
  ],
};
```

```bash
pm2 start ecosystem.config.cjs
pm2 save
pm2 status
pm2 logs trading-engine
```

### Firewall

```bash
ufw allow 8000/tcp
ufw allow 5173/tcp
```

---

## Dashboard tabs

| Tab | Function |
|-----|----------|
| Overview | Equity, PnL, positions |
| Analytics | Equity curve |
| Strategies | Enable / disable |
| Pairs | Pair toggles |
| Risk | Editable limits + **Kill switch** |
| News | Feed (demo / extensible) |
| Agent | Session, regimes, scoreboard, health, last backtest |

Kill switch **blocks new orders only** — open positions are not force-closed.

---

## Smart agent (no LLM subscription required)

Rule-based layer:

- Multi-timeframe regime (15m / 1h proxy)
- Session clock (Asia / London / NY / off-hours)
- Funding filter, correlation guard, data-quality gate
- Setup score 0–100, portfolio heat
- Playbooks per strategy type
- Auto-disable on loss streak / poor win rate
- Adaptive size multiplier
- Memory + health watchdog

Optional later: LLM commentary (API key required).

---

## Backtest

**Stop the live engine first** (same IP → Binance rate limits / bans):

```bash
pm2 stop trading-engine
source .venv/bin/activate
python scripts/run_backtest.py --days 21 --symbols BTCUSDC,ETHUSDC
python scripts/run_backtest.py --walk-forward --symbol BTCUSDC --days 60
pm2 start trading-engine
```

Results: `config/last_backtest.json` (also shown in Agent tab via API).

---

## Telegram

1. Create bot via `@BotFather`, get token  
2. Get your `chat_id`  
3. Put both in `.env`  
4. `pm2 restart trading-engine --update-env`

Alerts include: engine start, kill switch on/off, strategy auto-disable.

---

## Rate limits (important)

Binance bans IPs that poll too hard (`429` / `418 teapot`).

This project uses:

- REST polling **every 45s**, staggered per symbol  
- Avoid running **backtest + paper engine** at the same time  
- Other bots on the same VPS share the same IP quota  

If banned: `pm2 stop trading-engine`, wait 15–30+ minutes, then start again.

---

## Update from zip

```bash
cd /root
unzip -o FULL-bot-update.zip
bash trading-bot/scripts/update_all.sh /root/FULL-bot-update.zip
```

Does **not** overwrite `.env`. May reset `runtime_control.json` defaults — re-apply Risk settings in the UI if needed.

---

## Git / push

```bash
cd /root/trading-bot   # or your project dir
cp /path/to/README.md .
git add README.md
git add -A
git status
git commit -m "Docs: complete README for USDC bot, agent, dashboard, PM2"
git push origin main
```

Never commit `.env` or API secrets (see `.gitignore`).

---

## Safety

- Paper first for days/weeks  
- Kill switch + daily loss caps  
- Live mode only with small size and known risk limits  
- This is software, not financial advice; crypto trading is high risk  

---

## License

Private / use at your own risk unless otherwise specified by the repository owner.
