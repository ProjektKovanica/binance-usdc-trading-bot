"""
Abstract exchange interface.
All concrete adapters (Binance Spot, Binance USDC-M Futures) implement this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, List, Optional

from core.types import Order, Position, Ticker, Candle


class ExchangeAdapter(ABC):
    """Minimal interface required by ExecutionEngine and PortfolioManager."""

    name: str
    market_type: str  # "spot" | "futures"

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> List[list]:
        ...

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict] = None,
    ) -> Dict:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        ...

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> Dict:
        ...

    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        ...

    @abstractmethod
    async def fetch_balance(self) -> Dict:
        ...

    @abstractmethod
    async def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        ...

    @abstractmethod
    async def fetch_time(self) -> int:
        ...

    # Optional but useful for futures
    async def fetch_funding_rate(self, symbol: str) -> Optional[Decimal]:
        return None

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        pass
