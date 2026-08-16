# Professional Algorithmic Trading Bot – Binance Spot + USDC-M Futures

Production-oriented modular trading system with strict risk management, paper/live parity, realistic backtesting and a modern real-time web dashboard.

## Implemented Features (full roadmap completed)

### Core Architecture
- Strategy → Risk Manager → Execution Engine → Portfolio Manager
- Multi-strategy support via registry
- Paper / Live mode with identical code paths
- Async event-driven design

### Risk Management (highest priority)
- Position sizing: Fixed, % Equity, ATR, Kelly (capped + half-Kelly)
- Hard limits: max position, daily/weekly loss, max drawdown, max leverage, max open positions
- Correlation group exposure control
- Funding-rate awareness (futures)
- Automatic + manual kill-switch

### Execution
- Market / Limit / Stop support
- Paper simulation with realistic slippage, latency & fees
- Live path with retry, heartbeat, reconnect foundation
- Detailed order & fill event stream

### Portfolio & Accounting
- Accurate average-price position tracking
- Realized / unrealized PnL
- Fees + funding payments
- Equity curve + trade journal

### Market Data
- WebSocket-first feed (ccxt.pro style) with automatic REST fallback
- Multi-symbol / multi-timeframe

### Strategy Framework
- Clean `BaseStrategy` interface (`on_bar`, `on_tick`, …)
- Strategy Registry
- Example 1: EMA + ATR Trend (long/short)
- Example 2: Funding Mean-Reversion + Bollinger

### Backtesting
- Realistic event-driven backtester (fees, slippage, funding)
- Institutional metrics: Sharpe, Sortino, Calmar, Profit Factor, Expectancy, Max DD, Risk of Ruin
- Walk-forward analysis helper

### Monitoring & Alerts
- Structured JSON logging (structlog)
- Telegram + Discord multi-channel alerts with priority levels

### Modern Web Dashboard
- FastAPI backend + WebSocket real-time push
- React + Vite + Tailwind + Recharts
- Live equity, daily PnL, risk utilization, max DD
- Equity curve chart
- Open positions / orders
- Trade journal table
- Kill-switch control

### Production Hardening
- Dockerfile + docker-compose
- Non-root container user
- Config via YAML + environment variables
- `.env.example` for secrets

## Quick Start

```bash
cd trading-bot

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env – use Binance Testnet keys first!

# Paper trading
python scripts/run_paper.py

# Dashboard API
uvicorn dashboard.backend.main:app --reload --port 8000

# Frontend
cd dashboard/frontend
npm install
npm run dev
# → http://localhost:5173
```

### Docker

```bash
docker-compose up --build
```

## Capital Settings
- Paper default: **$10 000 USDC**
- Live hard floor: **$10 USDC** (only for connectivity testing – fees dominate)

## Project Layout

```
trading-bot/
├── core/           # types, config, main engine
├── risk/           # Risk Manager + Position Sizer
├── execution/      # Order & Execution Engine
├── portfolio/      # Accounting & positions
├── exchanges/      # Binance Spot + USDC-M Futures
├── strategy/       # Framework + examples + registry
├── data/           # Market data feed (WS + REST)
├── backtest/       # Realistic backtester + metrics + walk-forward
├── monitoring/     # Logging + Alerts
├── dashboard/      # FastAPI + React
├── config/         # settings.yaml
├── scripts/        # run_paper.py
├── tests/          # unit tests (risk critical path)
├── Dockerfile
└── docker-compose.yml
```

## Safety

- Always start on **Binance Testnet** or pure paper mode.
- Kill-switch is enforced both automatically and from the dashboard.
- Never commit real API keys.
- This is not financial advice. Trade at your own risk.
