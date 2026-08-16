"""
Core domain types used across the entire trading system.
All models are Pydantic v2 for validation, serialization and clear contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS_LIMIT = "stop_loss_limit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"
    TRAILING_STOP = "trailing_stop"
    OCO = "oco"
    BRACKET = "bracket"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"  # for hedge mode


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"  # Post Only


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"  # USDC-M perpetual / delivery


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class SignalStrength(str, Enum):
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


# ──────────────────────────────────────────────
# Money & Quantity helpers (Decimal for precision)
# ──────────────────────────────────────────────

class Money(BaseModel):
    """Precise monetary value."""
    amount: Decimal
    currency: str = "USDC"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("amount", mode="before")
    @classmethod
    def to_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    def __float__(self) -> float:
        return float(self.amount)


# ──────────────────────────────────────────────
# Market Data
# ──────────────────────────────────────────────

class Candle(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Optional[Decimal] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("open", "high", "low", "close", "volume", "quote_volume", mode="before")
    @classmethod
    def to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return Decimal(str(v))


class Ticker(BaseModel):
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    timestamp: datetime
    mark_price: Optional[Decimal] = None  # futures
    index_price: Optional[Decimal] = None
    funding_rate: Optional[Decimal] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ──────────────────────────────────────────────
# Orders & Fills
# ──────────────────────────────────────────────

class OrderRequest(BaseModel):
    """Intent to place an order. Created by Strategy or Risk Manager."""
    client_order_id: str = Field(default_factory=lambda: f"bot_{uuid4().hex[:16]}")
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    trailing_delta: Optional[Decimal] = None  # for trailing stop
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    post_only: bool = False
    strategy_id: str
    reason: str = ""  # why this order was generated
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("quantity", "price", "stop_price", "trailing_delta", mode="before")
    @classmethod
    def to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return Decimal(str(v))


class Fill(BaseModel):
    trade_id: str
    order_id: str
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_currency: str
    timestamp: datetime
    is_maker: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Order(BaseModel):
    """Live representation of an order on the exchange or paper."""
    id: str  # exchange order id or paper id
    client_order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    status: OrderStatus
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    remaining_quantity: Decimal
    price: Optional[Decimal] = None
    average_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    strategy_id: str
    reason: str = ""
    created_at: datetime
    updated_at: datetime
    fills: List[Fill] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def is_active(self) -> bool:
        return self.status in {OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}

    @property
    def is_done(self) -> bool:
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }


# ──────────────────────────────────────────────
# Positions
# ──────────────────────────────────────────────

class Position(BaseModel):
    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal = Decimal("0")
    leverage: Decimal = Decimal("1")
    margin_type: str = "cross"  # or isolated
    liquidation_price: Optional[Decimal] = None
    strategy_id: str
    opened_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def notional(self) -> Decimal:
        return abs(self.quantity * self.mark_price)

    @property
    def is_open(self) -> bool:
        return self.quantity != 0


# ──────────────────────────────────────────────
# Signals (from Strategy)
# ──────────────────────────────────────────────

class Signal(BaseModel):
    """Strategy output. Never directly places orders – goes through Risk Manager."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    strategy_id: str
    symbol: str
    side: Side
    strength: SignalStrength = SignalStrength.MEDIUM
    suggested_quantity: Optional[Decimal] = None  # if None → Risk Manager sizes it
    suggested_price: Optional[Decimal] = None
    order_type: OrderType = OrderType.MARKET
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    trailing_stop_pct: Optional[Decimal] = None
    reason: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ──────────────────────────────────────────────
# Risk related
# ──────────────────────────────────────────────

class RiskLimits(BaseModel):
    max_position_size_usdc: Decimal = Decimal("5000")
    max_position_pct_equity: Decimal = Decimal("0.10")  # 10%
    max_daily_loss_usdc: Decimal = Decimal("200")
    max_daily_loss_pct: Decimal = Decimal("0.02")
    max_weekly_loss_usdc: Decimal = Decimal("500")
    max_drawdown_pct: Decimal = Decimal("0.08")
    max_leverage: Decimal = Decimal("5")
    max_open_positions: int = 8
    max_correlated_exposure_pct: Decimal = Decimal("0.25")
    min_risk_reward: Decimal = Decimal("1.5")


class PortfolioSnapshot(BaseModel):
    timestamp: datetime
    equity: Decimal
    available_balance: Decimal
    used_margin: Decimal
    unrealized_pnl: Decimal
    realized_pnl_today: Decimal
    open_positions: List[Position]
    open_orders: List[Order]
    daily_pnl: Decimal
    weekly_pnl: Decimal
    max_drawdown_pct: Decimal
    risk_utilization_pct: Decimal  # 0-100


# ──────────────────────────────────────────────
# Events (for event bus)
# ──────────────────────────────────────────────

class EventType(str, Enum):
    CANDLE = "candle"
    TICKER = "ticker"
    ORDER_UPDATE = "order_update"
    FILL = "fill"
    POSITION_UPDATE = "position_update"
    SIGNAL = "signal"
    RISK_VIOLATION = "risk_violation"
    KILL_SWITCH = "kill_switch"
    SYSTEM_HEALTH = "system_health"
    ALERT = "alert"


class Event(BaseModel):
    type: EventType
    payload: Any
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""
