"""
Real-time market data feed.
Prefers ccxt.pro (websocket) with automatic fallback to REST polling.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

from core.types import Candle, Ticker, Event, EventType

logger = logging.getLogger(__name__)


class MarketDataFeed:
    """
    Unified async market data feed for multiple symbols & timeframes.
    Emits Candle and Ticker events via callbacks.
    """

    def __init__(
        self,
        exchange_adapter: Any,
        symbols: List[str],
        timeframes: List[str] = None,
        use_websocket: bool = True,
    ):
        self.exchange = exchange_adapter
        self.symbols = symbols
        self.timeframes = timeframes or ["15m"]
        self.use_websocket = use_websocket

        self._callbacks: List[Callable[[Event], None]] = []
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._last_candle_ts: Dict[str, int] = {}  # symbol_tf -> timestamp

    def on_event(self, callback: Callable[[Event], None]) -> None:
        self._callbacks.append(callback)

    def _emit(self, event_type: EventType, payload: Any) -> None:
        event = Event(type=event_type, payload=payload, source="data_feed")
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.exception("Data feed callback error: %s", e)

    async def start(self) -> None:
        self._running = True
        logger.info(
            "MarketDataFeed starting | symbols=%s | timeframes=%s | ws=%s",
            self.symbols,
            self.timeframes,
            self.use_websocket,
        )

        if self.use_websocket:
            try:
                await self._start_websocket()
                return
            except Exception as e:
                logger.warning("WebSocket feed failed (%s) – falling back to REST polling", e)

        # Fallback REST polling
        self._tasks.append(asyncio.create_task(self._rest_polling_loop()))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("MarketDataFeed stopped")

    # ──────────────────────────────────────────────
    # WebSocket path (ccxt.pro style)
    # ──────────────────────────────────────────────

    async def _start_websocket(self) -> None:
        """
        Attempt to use watch_ohlcv / watch_ticker if the adapter supports it.
        Many ccxt.pro exchanges expose these methods.
        """
        ex = self.exchange.exchange  # underlying ccxt instance
        if not hasattr(ex, "watch_ohlcv"):
            raise RuntimeError("Exchange does not support watch_ohlcv")

        for symbol in self.symbols:
            for tf in self.timeframes:
                self._tasks.append(
                    asyncio.create_task(self._watch_ohlcv_loop(symbol, tf))
                )
            self._tasks.append(
                asyncio.create_task(self._watch_ticker_loop(symbol))
            )

    async def _watch_ohlcv_loop(self, symbol: str, timeframe: str) -> None:
        ex = self.exchange.exchange
        market_symbol = self.exchange._to_ccxt_symbol(symbol)
        while self._running:
            try:
                ohlcv = await ex.watch_ohlcv(market_symbol, timeframe)
                if not ohlcv:
                    continue
                # ohlcv is list of candles; last one is the most recent
                last = ohlcv[-1]
                ts = int(last[0])
                key = f"{symbol}_{timeframe}"
                # Only emit on closed candle (new timestamp)
                if self._last_candle_ts.get(key) == ts:
                    continue
                self._last_candle_ts[key] = ts

                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    open=Decimal(str(last[1])),
                    high=Decimal(str(last[2])),
                    low=Decimal(str(last[3])),
                    close=Decimal(str(last[4])),
                    volume=Decimal(str(last[5])),
                )
                self._emit(EventType.CANDLE, candle)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("watch_ohlcv error %s %s: %s – retrying", symbol, timeframe, e)
                await asyncio.sleep(3)

    async def _watch_ticker_loop(self, symbol: str) -> None:
        ex = self.exchange.exchange
        market_symbol = self.exchange._to_ccxt_symbol(symbol)
        while self._running:
            try:
                raw = await ex.watch_ticker(market_symbol)
                ticker = Ticker(
                    symbol=symbol,
                    bid=Decimal(str(raw.get("bid") or raw.get("last"))),
                    ask=Decimal(str(raw.get("ask") or raw.get("last"))),
                    last=Decimal(str(raw.get("last"))),
                    timestamp=datetime.now(timezone.utc),
                    mark_price=Decimal(str(raw["info"].get("markPrice", 0))) if raw.get("info") else None,
                    funding_rate=None,
                )
                self._emit(EventType.TICKER, ticker)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("watch_ticker error %s: %s", symbol, e)
                await asyncio.sleep(2)

    # ──────────────────────────────────────────────
    # REST polling fallback
    # ──────────────────────────────────────────────

    async def _rest_polling_loop(self) -> None:
        logger.info("Using REST polling fallback for market data")
        while self._running:
            try:
                for symbol in self.symbols:
                    for tf in self.timeframes:
                        ohlcv = await self.exchange.fetch_ohlcv(symbol, tf, limit=5)
                        if not ohlcv:
                            continue
                        last = ohlcv[-1]
                        ts = int(last[0])
                        key = f"{symbol}_{tf}"
                        if self._last_candle_ts.get(key) == ts:
                            continue
                        self._last_candle_ts[key] = ts

                        candle = Candle(
                            symbol=symbol,
                            timeframe=tf,
                            timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                            open=Decimal(str(last[1])),
                            high=Decimal(str(last[2])),
                            low=Decimal(str(last[3])),
                            close=Decimal(str(last[4])),
                            volume=Decimal(str(last[5])),
                        )
                        self._emit(EventType.CANDLE, candle)

                    # Also push a ticker
                    try:
                        raw = await self.exchange.fetch_ticker(symbol)
                        ticker = Ticker(
                            symbol=symbol,
                            bid=Decimal(str(raw.get("bid") or raw["last"])),
                            ask=Decimal(str(raw.get("ask") or raw["last"])),
                            last=Decimal(str(raw["last"])),
                            timestamp=datetime.now(timezone.utc),
                        )
                        self._emit(EventType.TICKER, ticker)
                    except Exception:
                        pass

                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("REST polling error: %s", e)
                await asyncio.sleep(10)
