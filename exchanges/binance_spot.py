"""
Binance Spot adapter focused on USDC pairs (BTCUSDC, ETHUSDC…).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt

from exchanges.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class BinanceSpotUSDC(ExchangeAdapter):
    name = "binance_spot_usdc"
    market_type = "spot"

    def __init__(self, api_key: str = "", secret: str = "", sandbox: bool = False):
        self.api_key = api_key
        self.secret = secret
        self.sandbox = sandbox
        self.exchange: Optional[ccxt.binance] = None

    async def connect(self) -> None:
        self.exchange = ccxt.binance(
            {
                "apiKey": self.api_key,
                "secret": self.secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                    "recvWindow": 10000,
                },
                "sandbox": self.sandbox,
            }
        )
        await self.exchange.load_markets()
        logger.info("Connected to Binance Spot (%s markets)", len(self.exchange.markets))

    async def close(self) -> None:
        if self.exchange:
            await self.exchange.close()
            self.exchange = None

    def _ensure(self) -> ccxt.binance:
        if not self.exchange:
            raise RuntimeError("Exchange not connected")
        return self.exchange

    def _to_ccxt_symbol(self, symbol: str) -> str:
        if "/" in symbol:
            return symbol
        if symbol.endswith("USDC"):
            base = symbol[:-4]
            return f"{base}/USDC"
        return symbol

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return await self._ensure().fetch_ticker(self._to_ccxt_symbol(symbol))

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 200
    ) -> List[list]:
        return await self._ensure().fetch_ohlcv(
            self._to_ccxt_symbol(symbol), timeframe=timeframe, limit=limit
        )

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict] = None,
    ) -> Dict:
        return await self._ensure().create_order(
            symbol=self._to_ccxt_symbol(symbol),
            type=type,
            side=side,
            amount=amount,
            price=price,
            params=params or {},
        )

    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        return await self._ensure().cancel_order(order_id, self._to_ccxt_symbol(symbol))

    async def fetch_order(self, order_id: str, symbol: str) -> Dict:
        return await self._ensure().fetch_order(order_id, self._to_ccxt_symbol(symbol))

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        sym = self._to_ccxt_symbol(symbol) if symbol else None
        return await self._ensure().fetch_open_orders(sym)

    async def fetch_balance(self) -> Dict:
        return await self._ensure().fetch_balance()

    async def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        # Spot has no positions in the futures sense
        return []

    async def fetch_time(self) -> int:
        return await self._ensure().fetch_time()
