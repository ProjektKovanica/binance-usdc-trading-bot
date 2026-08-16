"""
Configuration loader – YAML + environment variables.
Secrets never live in plain YAML in production.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class RiskConfig(BaseModel):
    max_position_size_usdc: Decimal = Decimal("2000")
    max_position_pct_equity: Decimal = Decimal("0.08")
    max_daily_loss_usdc: Decimal = Decimal("150")
    max_daily_loss_pct: Decimal = Decimal("0.015")
    max_weekly_loss_usdc: Decimal = Decimal("400")
    max_drawdown_pct: Decimal = Decimal("0.07")
    max_leverage: Decimal = Decimal("3")
    max_open_positions: int = 6
    max_correlated_exposure_pct: Decimal = Decimal("0.20")
    min_risk_reward: Decimal = Decimal("1.8")
    position_sizing: str = "atr"
    atr_period: int = 14
    atr_multiplier: float = 1.5
    kelly_fraction_cap: float = 0.25
    kill_switch_enabled: bool = True
    auto_kill_on_daily_loss: bool = True
    auto_kill_on_max_dd: bool = True


class ExecutionConfig(BaseModel):
    default_order_type: str = "market"
    max_slippage_pct: Decimal = Decimal("0.0015")
    retry_attempts: int = 3
    retry_delay_ms: int = 400
    partial_fill_timeout_sec: int = 30
    heartbeat_interval_sec: int = 15
    reconnect_max_attempts: int = 10
    use_post_only_when_possible: bool = False


class DashboardConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: List[str] = ["http://localhost:5173"]
    jwt_secret: str = "CHANGE_ME"
    jwt_expire_minutes: int = 1440


class Settings(BaseSettings):
    mode: str = "paper"
    starting_capital_usdc: Decimal = Decimal("10000")
    min_live_capital_usdc: Decimal = Decimal("10")

    symbols: List[str] = [
        "BTCUSDC",
        "ETHUSDC",
        "SOLUSDC",
        "XRPUSDC",
        "DOGEUSDC",
        "AVAXUSDC",
        "LINKUSDC",
        "BNBUSDC",
    ]
    timeframes: List[str] = ["1m", "5m", "15m", "1h", "4h"]

    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    # Secrets from env
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_sandbox: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


def load_settings(config_path: str | Path = "config/settings.yaml") -> Settings:
    path = Path(config_path)
    data: Dict[str, Any] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Flatten some nested keys for pydantic
    settings = Settings(
        mode=data.get("mode", "paper"),
        starting_capital_usdc=Decimal(str(data.get("starting_capital_usdc", 10000))),
        min_live_capital_usdc=Decimal(str(data.get("min_live_capital_usdc", 10))),
        symbols=data.get("symbols", Settings().symbols),
        timeframes=data.get("timeframes", Settings().timeframes),
        risk=RiskConfig(**(data.get("risk") or {})),
        execution=ExecutionConfig(**(data.get("execution") or {})),
        dashboard=DashboardConfig(**(data.get("dashboard") or {})),
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
        binance_sandbox=os.getenv("BINANCE_SANDBOX", "true").lower() == "true",
    )
    return settings
