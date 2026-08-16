"""
Modern FastAPI backend for the trading dashboard.
Real-time via WebSockets + REST for history & control.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel

# These will be injected by the main bot process later
# For standalone dashboard we keep a mock state for development

app_state: Dict[str, Any] = {
    "equity": 10000.0,
    "daily_pnl": 0.0,
    "open_positions": [],
    "open_orders": [],
    "risk": {
        "kill_switch": False,
        "risk_utilization_pct": 0.0,
        "max_drawdown_pct": 0.0,
    },
    "mode": "paper",
    "last_update": datetime.now(timezone.utc).isoformat(),
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
    # Startup
    print("Dashboard backend started")
    yield
    # Shutdown
    print("Dashboard backend stopped")


app = FastAPI(
    title="Algo Trading Bot Dashboard",
    version="1.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# REST endpoints
# ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/portfolio")
async def get_portfolio():
    return {
        "equity": app_state["equity"],
        "daily_pnl": app_state["daily_pnl"],
        "mode": app_state["mode"],
        "last_update": app_state["last_update"],
        "risk": app_state["risk"],
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
    app_state["risk"]["kill_switch"] = body.active
    # In real integration this will call risk_manager.activate_kill_switch / reset
    return {"ok": True, "kill_switch": body.active, "reason": body.reason}


@app.get("/api/metrics/equity-curve")
async def equity_curve(limit: int = 500):
    points = app_state.get("equity_curve", [])
    return {"points": points[-limit:]}


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    trades = app_state.get("trades", [])
    return {"trades": trades[-limit:]}


# ──────────────────────────────────────────────
# WebSocket – real-time push
# ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Send initial snapshot
        await ws.send_json({"type": "snapshot", "data": app_state})
        while True:
            # Keep alive / receive commands from UI
            data = await ws.receive_json()
            if data.get("action") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(ws)


# Helper used by the main bot process to push updates
async def push_update(update: Dict[str, Any]):
    app_state.update(update)
    app_state["last_update"] = datetime.now(timezone.utc).isoformat()
    await manager.broadcast({"type": "update", "data": app_state})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.backend.main:app", host="0.0.0.0", port=8000, reload=True)
