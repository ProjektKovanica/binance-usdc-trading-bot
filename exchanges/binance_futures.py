"""
Binance USDC-M Futures adapter (perpetual + delivery if needed).
Uses ccxt async for robustness + rate-limit handling.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt

from exchanges.base import ExchangeAdapter

logger = logging.getLogger(__name__)


class BinanceFuturesUSDC(ExchangeAdapter):
    """
    Focused on USDC-margined perpetual futures.
    Symbols look like BTCUSDC, ETHUSDC, etc.
    """

    name = "binance_futures_usdc"
    market_type = "futures"

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        sandbox: bool = False,
        default_leverage: int = 3,
    ):
        self.api_key = api_key
        self.secret = secret
        self.sandbox = sandbox
        self.default_leverage = default_leverage
        self.exchange: Optional[ccxt.binanceusdm] = None  # USDM is the closest; we filter USDC

    async def connect(self) -> None:
        options = {
            "defaultType": "future",
            "adjustForTimeDifference": True,
            "recvWindow": 10000,
            "portfolioMargin": False,
        }
        self.exchange = ccxt.binanceusdm(
            {
                "apiKey": self.api_key,
                "secret": self.secret,
                "enableRateLimit": True,
                "options": options,
                "sandbox": self.sandbox,
            }
        )
        # Load markets so precision & limits are known
        await self.exchange.load_markets()
        logger.info(
            "Connected to Binance USDC-M Futures (%s markets loaded)",
            len(self.exchange.markets),
        )

    async def close(self) -> None:
        if self.exchange:
            await self.exchange.close()
            self.exchange = None

    def _ensure(self) -> ccxt.binanceusdm:
        if not self.exchange:
            raise RuntimeError("Exchange not connected. Call connect() first.")
        return self.exchange

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        ex = self._ensure()
        # Normalize symbol if needed (BTCUSDC → BTC/USDC:USDC)
        market_symbol = self._to_ccxt_symbol(symbol)
        ticker = await ex.fetch_ticker(market_symbol)
        return ticker

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 200
    ) -> List[list]:
        ex = self._ensure()
        market_symbol = self._to_ccxt_symbol(symbol)
        return await ex.fetch_ohlcv(market_symbol, timeframe=timeframe, limit=limit)

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict] = None,
    ) -> Dict:
        ex = self._ensure()
        market_symbol = self._to_ccxt_symbol(symbol)
        params = params or {}

        # Ensure leverage is set (idempotent)
        try:
            await self.set_leverage(symbol, self.default_leverage)
        except Exception as e:
            logger.warning("Could not set leverage for %s: %s", symbol, e)

        order = await ex.create_order(
            symbol=market_symbol,
            type=type,
            side=side,
            amount=amount,
            price=price,
            params=params,
        )
        return order

    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        ex = self._ensure()
        market_symbol = self._to_ccxt_symbol(symbol)
        return await ex.cancel_order(order_id, market_symbol)

    async def fetch_order(self, order_id: str, symbol: str) -> Dict:
        ex = self._ensure()
        market_symbol = self._to_ccxt_symbol(symbol)
        return await ex.fetch_order(order_id, market_symbol)

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        ex = self._ensure()
        market_symbol = self._to_ccxt_symbol(symbol) if symbol else None
        return await ex.fetch_open_orders(market_symbol)

    async def fetch_balance(self) -> Dict:
        ex = self._ensure()
        balance = await ex.fetch_balance()
        # Prefer USDC balance
        return balance

    async def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        ex = self._ensure()
        positions = await ex.fetch_positions(symbols)
        # Filter only non-zero
        return [p for p in positions if float(p.get("contracts", 0)) != 0]

    async def fetch_time(self) -> int:
        ex = self._ensure()
        return await ex.fetch_time()

    async def fetch_funding_rate(self, symbol: str) -> Optional[Decimal]:
        ex = self._ensure()
        try:
            market_symbol = self._to_ccxt_symbol(symbol)
            fr = await ex.fetch_funding_rate(market_symbol)
            return Decimal(str(fr.get("fundingRate", 0)))
        except Exception as e:
            logger.debug("Funding rate fetch failed for %s: %s", symbol, e)
            return None

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        ex = self._ensure()
        market_symbol = self._to_ccxt_symbol(symbol)
        await ex.set_leverage(leverage, market_symbol)

    def _to_ccxt_symbol(self, symbol: str) -> str:
        """
        Convert internal BTCUSDC → BTC/USDC:USDC (ccxt unified for USDM)
        or keep as-is if already unified.
        """
        if "/" in symbol:
            return symbol
        # Simple heuristic for USDC pairs
        if symbol.endswith("USDC"):
            base = symbol[:-4]
            return f"{base}/USDC:USDC"
        return symbol
