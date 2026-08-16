"""
Professional Trading Bot Dashboard Backend
FastAPI + WebSocket real-time + full control endpoints
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, HTMLResponse
from pydantic import BaseModel

# ──────────────────────────────────────────────
# Shared runtime files (engine <-> dashboard)
# ──────────────────────────────────────────────

CONTROL_PATH = Path(__file__).resolve().parents[2] / "config" / "runtime_control.json"
STATUS_PATH = Path(__file__).resolve().parents[2] / "config" / "runtime_status.json"

KILL_SWITCH_DESCRIPTION = (
    "Emergency stop: blocks ALL NEW orders from strategies. "
    "Open positions stay open (they are not force-closed). "
    "Use this if the bot misbehaves, losses spike, or you need a hard pause. "
    "Reset only when you are sure it is safe to resume."
)

# Simple in-memory rate limiter: max N requests per IP per window
_RATE_LIMIT = 60  # requests
_RATE_WINDOW = 60  # seconds
_rate_buckets: dict = defaultdict(list)


def _check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    bucket = _rate_buckets[client_ip]
    # drop old
    _rate_buckets[client_ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_rate_buckets[client_ip]) >= _RATE_LIMIT:
        return False
    _rate_buckets[client_ip].append(now)
    return True


def _write_control(**updates) -> dict:
    data = {}
    if CONTROL_PATH.exists():
        try:
            data = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(updates)
    data["kill_description"] = KILL_SWITCH_DESCRIPTION
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["updated_by"] = "dashboard"
    CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _read_status() -> dict:
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ──────────────────────────────────────────────
# In-memory state (injected from live engine in production)
# ──────────────────────────────────────────────

app_state: Dict[str, Any] = {
    "mode": "paper",
    "equity": 10000.0,
    "starting_equity": 10000.0,
    "daily_pnl": 0.0,
    "weekly_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "realized_pnl": 0.0,
    "available_balance": 10000.0,
    "used_margin": 0.0,
    "last_update": datetime.now(timezone.utc).isoformat(),
    "risk": {
        "kill_switch": False,
        "kill_reason": "",
        "kill_description": KILL_SWITCH_DESCRIPTION,
        "risk_utilization_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "max_daily_loss_usdc": 150.0,
        "max_position_pct_equity": 0.08,
        "max_leverage": 3,
        "max_open_positions": 6,
    },
    "open_positions": [],
    "open_orders": [],
    "equity_curve": [],
    "trades": [],
    "strategies": [
        {
            "id": "ema_atr_trend_v1",
            "name": "EMA + ATR Trend",
            "enabled": True,
            "symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC"],
            "timeframe": "15m",
            "params": {"fast_ema": 12, "slow_ema": 26, "atr_period": 14, "atr_mult_stop": 1.8, "rr_target": 2.5},
            "stats": {"trades": 0, "win_rate": 0.0, "pnl": 0.0},
        },
        {
            "id": "funding_mr_v1",
            "name": "Funding Mean Reversion",
            "enabled": False,
            "symbols": ["BTCUSDC", "ETHUSDC"],
            "timeframe": "1h",
            "params": {"bb_period": 20, "funding_threshold": 0.0003},
            "stats": {"trades": 0, "win_rate": 0.0, "pnl": 0.0},
        },
    ],
    "pairs": [
        {"symbol": "BTCUSDC", "active": True, "type": "futures"},
        {"symbol": "ETHUSDC", "active": True, "type": "futures"},
        {"symbol": "SOLUSDC", "active": True, "type": "futures"},
        {"symbol": "XRPUSDC", "active": True, "type": "futures"},
        {"symbol": "DOGEUSDC", "active": True, "type": "futures"},
        {"symbol": "AVAXUSDC", "active": False, "type": "futures"},
        {"symbol": "LINKUSDC", "active": False, "type": "futures"},
        {"symbol": "BNBUSDC", "active": False, "type": "futures"},
    ],
    "system": {
        "status": "running",
        "data_feed": "REST polling",
        "exchange": "binanceusdm",
        "version": "1.0.0",
    },
}


class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Dashboard backend started")
    _write_control(
        kill_switch=app_state["risk"].get("kill_switch", False),
        kill_reason=app_state["risk"].get("kill_reason", ""),
        max_daily_loss_usdc=app_state["risk"].get("max_daily_loss_usdc", 150),
        max_position_pct_equity=app_state["risk"].get("max_position_pct_equity", 0.08),
        max_leverage=app_state["risk"].get("max_leverage", 3),
        max_open_positions=app_state["risk"].get("max_open_positions", 6),
    )
    yield
    print("Dashboard backend stopped")


app = FastAPI(
    title="Algo Trading Bot Dashboard",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    # Skip rate limit for docs/health lightly, still count API
    client = request.client.host if request.client else "unknown"
    path = request.url.path
    if path.startswith("/api") or path == "/ws":
        if not _check_rate_limit(client):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Max 60 requests / minute."},
            )
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <!DOCTYPE html>
    <html><head><title>Trading Bot API</title>
    <style>
        body{font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
        .card{background:#1e293b;padding:2.5rem;border-radius:1rem;text-align:center;max-width:520px}
        h1{color:#34d399;margin-bottom:.5rem} a{color:#38bdf8}
        .links{margin-top:1.5rem;text-align:left;line-height:1.8}
        code{background:#0f172a;padding:.15rem .4rem;border-radius:4px}
    </style></head>
    <body><div class="card">
        <h1>Algo Trading Bot API</h1>
        <p>Backend is running. Full React UI runs on port 5173.</p>
        <div class="links">
            <div><a href="/api/health">/api/health</a></div>
            <div><a href="/api/portfolio">/api/portfolio</a></div>
            <div><a href="/api/strategies">/api/strategies</a></div>
            <div><a href="/api/pairs">/api/pairs</a></div>
            <div><a href="/api/risk">/api/risk</a></div>
            <div><a href="/api/news">/api/news</a></div>
            <div><a href="/docs">/docs</a> (Swagger UI)</div>
        </div>
    </div></body></html>
    """


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": app_state["mode"],
        "system": app_state["system"],
    }


@app.get("/api/portfolio")
async def get_portfolio():
    live = _read_status()
    if live:
        app_state["equity"] = live.get("equity", app_state["equity"])
        app_state["available_balance"] = live.get("available_balance", app_state["available_balance"])
        app_state["unrealized_pnl"] = live.get("unrealized_pnl", app_state["unrealized_pnl"])
        app_state["realized_pnl"] = live.get("realized_pnl", app_state["realized_pnl"])
        app_state["mode"] = live.get("mode", app_state["mode"])
        app_state["open_positions"] = live.get("open_positions", app_state["open_positions"])
        app_state["last_update"] = live.get("updated_at", app_state["last_update"])
        if live.get("risk"):
            r = live["risk"]
            app_state["risk"]["kill_switch"] = r.get("kill_switch", app_state["risk"]["kill_switch"])
            app_state["risk"]["kill_reason"] = r.get("kill_reason", app_state["risk"].get("kill_reason", ""))
            app_state["risk"]["risk_utilization_pct"] = r.get("risk_utilization_pct", 0)
            app_state["risk"]["max_drawdown_pct"] = r.get("max_drawdown_pct", 0)
            app_state["daily_pnl"] = r.get("daily_pnl", app_state["daily_pnl"])
            app_state["weekly_pnl"] = r.get("weekly_pnl", app_state["weekly_pnl"])
    app_state["risk"]["kill_description"] = KILL_SWITCH_DESCRIPTION
    return {
        "equity": app_state["equity"],
        "starting_equity": app_state["starting_equity"],
        "daily_pnl": app_state["daily_pnl"],
        "weekly_pnl": app_state["weekly_pnl"],
        "unrealized_pnl": app_state["unrealized_pnl"],
        "realized_pnl": app_state["realized_pnl"],
        "available_balance": app_state["available_balance"],
        "used_margin": app_state["used_margin"],
        "mode": app_state["mode"],
        "last_update": app_state["last_update"],
        "risk": app_state["risk"],
        "open_positions": app_state["open_positions"],
        "open_orders": app_state["open_orders"],
    }


@app.get("/api/positions")
async def get_positions():
    return {"positions": app_state["open_positions"]}


@app.get("/api/orders")
async def get_orders():
    return {"orders": app_state["open_orders"]}


@app.get("/api/risk")
async def get_risk():
    return app_state["risk"]


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = "Manual from dashboard"


@app.post("/api/risk/kill-switch")
async def set_kill_switch(body: KillSwitchRequest):
    reason = body.reason if body.active else ""
    if body.active and (not reason or reason.startswith("Manual") or reason.startswith("Activated")):
        reason = KILL_SWITCH_DESCRIPTION
    app_state["risk"]["kill_switch"] = body.active
    app_state["risk"]["kill_reason"] = reason
    app_state["risk"]["kill_description"] = KILL_SWITCH_DESCRIPTION
    _write_control(
        kill_switch=body.active,
        kill_reason=reason,
        max_daily_loss_usdc=app_state["risk"].get("max_daily_loss_usdc"),
        max_position_pct_equity=app_state["risk"].get("max_position_pct_equity"),
        max_leverage=app_state["risk"].get("max_leverage"),
        max_open_positions=app_state["risk"].get("max_open_positions"),
    )
    await manager.broadcast({"type": "update", "data": app_state})
    return {
        "ok": True,
        "kill_switch": body.active,
        "reason": reason,
        "description": KILL_SWITCH_DESCRIPTION,
    }


class RiskUpdate(BaseModel):
    max_daily_loss_usdc: Optional[float] = None
    max_position_pct_equity: Optional[float] = None
    max_leverage: Optional[float] = None
    max_open_positions: Optional[int] = None


@app.patch("/api/risk")
async def update_risk(body: RiskUpdate):
    for k, v in body.model_dump(exclude_none=True).items():
        if k in app_state["risk"]:
            app_state["risk"][k] = v
    _write_control(
        kill_switch=app_state["risk"].get("kill_switch", False),
        kill_reason=app_state["risk"].get("kill_reason", ""),
        max_daily_loss_usdc=app_state["risk"].get("max_daily_loss_usdc"),
        max_position_pct_equity=app_state["risk"].get("max_position_pct_equity"),
        max_leverage=app_state["risk"].get("max_leverage"),
        max_open_positions=app_state["risk"].get("max_open_positions"),
    )
    await manager.broadcast({"type": "update", "data": app_state})
    return {"ok": True, "risk": app_state["risk"]}


@app.get("/api/metrics/equity-curve")
async def equity_curve(limit: int = 500):
    return {"points": app_state.get("equity_curve", [])[-limit:]}


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    return {"trades": app_state.get("trades", [])[-limit:]}


@app.get("/api/strategies")
async def get_strategies():
    return {"strategies": app_state["strategies"]}


class StrategyToggle(BaseModel):
    enabled: bool


@app.post("/api/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, body: StrategyToggle):
    for s in app_state["strategies"]:
        if s["id"] == strategy_id:
            s["enabled"] = body.enabled
            await manager.broadcast({"type": "update", "data": app_state})
            return {"ok": True, "strategy": s}
    raise HTTPException(404, "Strategy not found")


@app.get("/api/pairs")
async def get_pairs():
    return {"pairs": app_state["pairs"]}


class PairToggle(BaseModel):
    active: bool


@app.post("/api/pairs/{symbol}/toggle")
async def toggle_pair(symbol: str, body: PairToggle):
    for p in app_state["pairs"]:
        if p["symbol"] == symbol:
            p["active"] = body.active
            await manager.broadcast({"type": "update", "data": app_state})
            return {"ok": True, "pair": p}
    raise HTTPException(404, "Pair not found")


@app.get("/api/system")
async def get_system():
    return app_state["system"]


@app.get("/api/news")
async def get_news():
    return {
        "news": [
            {
                "title": "Bitcoin holds key support as USDC pairs see increased volume",
                "source": "Demo Feed",
                "url": "#",
                "published": datetime.now(timezone.utc).isoformat(),
                "sentiment": "neutral",
            },
            {
                "title": "Funding rates on major USDC-M perpetuals remain elevated",
                "source": "Demo Feed",
                "url": "#",
                "published": datetime.now(timezone.utc).isoformat(),
                "sentiment": "bearish",
            },
            {
                "title": "ETHUSDC volatility expanding – ATR strategy watchlist",
                "source": "Demo Feed",
                "url": "#",
                "published": datetime.now(timezone.utc).isoformat(),
                "sentiment": "bullish",
            },
        ]
    }




@app.get("/api/agent")
async def get_agent_state():
    """Agent memory, regimes, health, smart scoreboard from live engine status."""
    live = _read_status()
    agent = live.get("agent") or {}
    smart = agent.get("smart") or {}
    mem_path = Path(__file__).resolve().parents[2] / "config" / "agent_memory.json"
    memory = agent.get("memory")
    if not memory and mem_path.exists():
        try:
            raw = json.loads(mem_path.read_text(encoding="utf-8"))
            memory = {"events": len(raw.get("events", [])), "raw_tail": raw.get("events", [])[-10:]}
        except Exception:
            memory = {}
    return {
        "memory": memory,
        "regimes": agent.get("regimes", smart.get("regimes", {})),
        "health": agent.get("health", {}),
        "smart": smart,
        "scoreboard": smart.get("scoreboard", {}),
        "session": smart.get("session"),
        "adaptive_mult_hint": agent.get("adaptive_mult_hint"),
        "updated_at": live.get("updated_at"),
    }


@app.post("/api/agent/strategy/{strategy_id}/enable")
async def agent_enable_strategy(strategy_id: str):
    """Clear auto-disable flag in control file for engine to pick up."""
    _write_control(reenable_strategy=strategy_id)
    return {"ok": True, "strategy_id": strategy_id, "action": "reenable"}


@app.get("/api/backtest/info")
async def backtest_info():
    return {
        "available": True,
        "note": "Run scripts/run_backtest.py on VPS for full offline backtest",
        "walk_forward": True,
        "metrics": ["sharpe", "sortino", "max_dd", "profit_factor", "win_rate"],
    }


@app.post("/api/backtest/run")
async def backtest_run():
    """Lightweight placeholder — full BT is CPU heavy; returns last metrics file if any."""
    metrics_path = Path(__file__).resolve().parents[2] / "config" / "last_backtest.json"
    if metrics_path.exists():
        try:
            return json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "ok": False,
        "message": "No cached backtest. SSH: python scripts/run_backtest.py",
        "hint": "Walk-forward: python -c 'from backtest.walk_forward import *; ...'",
    }

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"type": "snapshot", "data": app_state})
        while True:
            try:
                data = await ws.receive_json()
            except Exception:
                # non-JSON messages / empty frames
                data = {}
            if isinstance(data, dict) and data.get("action") == "ping":
                await ws.send_json({"type": "pong"})
                continue
            # keep connection alive; ignore unknown client messages
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(ws)


async def push_update(update: Dict[str, Any]):
    app_state.update(update)
    app_state["last_update"] = datetime.now(timezone.utc).isoformat()
    await manager.broadcast({"type": "update", "data": app_state})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.backend.main:app", host="0.0.0.0", port=8000, reload=True)
