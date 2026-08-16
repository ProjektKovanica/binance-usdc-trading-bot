"""
Strategy Framework – clean interface that works identically in backtest and live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from core.types import Candle, Signal, Order, Position, Ticker, Side, SignalStrength, OrderType


class StrategyContext:
    """
    Read-only context passed to strategy methods.
    Contains current portfolio state, indicators, etc.
    """

    def __init__(
        self,
        equity: Decimal,
        available_balance: Decimal,
        positions: Dict[str, Position],
        open_orders: List[Order],
        indicators: Dict[str, Any],
    ):
        self.equity = equity
        self.available_balance = available_balance
        self.positions = positions
        self.open_orders = open_orders
        self.indicators = indicators


class BaseStrategy(ABC):
    """
    Every strategy must inherit from this class.
    Implement the hooks you need. Unused hooks can stay as no-op.
    """

    def __init__(self, strategy_id: str, symbols: List[str], params: Optional[Dict] = None):
        self.strategy_id = strategy_id
        self.symbols = symbols
        self.params = params or {}
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    # ──────────────────────────────────────────────
    # Lifecycle / data hooks
    # ──────────────────────────────────────────────

    async def on_start(self) -> None:
        """Called once when strategy is loaded."""
        pass

    async def on_stop(self) -> None:
        """Called on graceful shutdown."""
        pass

    @abstractmethod
    async def on_bar(self, candle: Candle, ctx: StrategyContext) -> Optional[Signal]:
        """
        Called on every closed candle of the subscribed timeframes.
        This is the main place to generate signals.
        Return Signal or None.
        """
        ...

    async def on_tick(self, ticker: Ticker, ctx: StrategyContext) -> Optional[Signal]:
        """Optional high-frequency tick handler. Default: ignore."""
        return None

    async def on_order_update(self, order: Order, ctx: StrategyContext) -> None:
        """Called when one of our orders changes state."""
        pass

    async def on_fill(self, fill: Any, ctx: StrategyContext) -> None:
        """Called on every fill that belongs to this strategy."""
        pass

    async def on_position_update(self, position: Position, ctx: StrategyContext) -> None:
        pass

    # Helper to build a clean Signal
    def create_signal(
        self,
        symbol: str,
        side: Side,
        reason: str,
        strength: SignalStrength = SignalStrength.MEDIUM,
        confidence: float = 0.6,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        order_type: OrderType = OrderType.MARKET,
        suggested_quantity: Optional[Decimal] = None,
        metadata: Optional[Dict] = None,
    ) -> Signal:
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            side=side,
            strength=strength,
            confidence=confidence,
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_type=order_type,
            suggested_quantity=suggested_quantity,
            metadata=metadata or {},
        )
