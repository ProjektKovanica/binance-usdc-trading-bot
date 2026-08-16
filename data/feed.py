"""
Real-time market data feed.
Prefers WebSocket (ccxt.pro style) with clean automatic fallback to REST polling.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

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

        # Check if the underlying exchange actually supports watch_* methods
        can_use_ws = False
        if self.use_websocket:
            try:
                ex = getattr(self.exchange, "exchange", None)
                if ex is not None and hasattr(ex, "watch_ohlcv") and callable(getattr(ex, "watch_ohlcv", None)):
                    # Quick capability probe – many exchanges raise "not supported yet"
                    can_use_ws = True
                    # We will still catch runtime "not supported" errors below
            except Exception:
                can_use_ws = False

        if can_use_ws:
            try:
                await self._start_websocket()
                # If we reach here without exception, WebSocket tasks are running
                logger.info("Using WebSocket market data feed")
                return
            except Exception as e:
                logger.warning("WebSocket feed not available (%s) – falling back to REST polling", e)
                # Cancel any partial tasks
                for t in self._tasks:
                    t.cancel()
                self._tasks.clear()

        # Clean REST polling fallback (always works)
        logger.info("Using REST polling market data feed (recommended for current ccxt/binanceusdm)")
        self._tasks.append(asyncio.create_task(self._rest_polling_loop()))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("MarketDataFeed stopped")

    # ──────────────────────────────────────────────
    # WebSocket path
    # ──────────────────────────────────────────────

    async def _start_websocket(self) -> None:
        ex = self.exchange.exchange
        if not hasattr(ex, "watch_ohlcv"):
            raise RuntimeError("Exchange does not support watch_ohlcv")

        # Probe once – if it immediately says "not supported", raise so we fall back
        try:
            # Just check the method exists and is callable; actual call happens in loops
            pass
        except Exception as e:
            raise RuntimeError(str(e))

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
        consecutive_errors = 0

        while self._running:
            try:
                ohlcv = await ex.watch_ohlcv(market_symbol, timeframe)
                consecutive_errors = 0
                if not ohlcv:
                    continue
                last = ohlcv[-1]
                ts = int(last[0])
                key = f"{symbol}_{timeframe}"
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
                msg = str(e).lower()
                consecutive_errors += 1
                if "not supported" in msg or "not implemented" in msg:
                    logger.warning(
                        "watch_ohlcv not supported for %s – stopping this WS task", symbol
                    )
                    break  # stop this task, let REST take over if needed
                logger.warning("watch_ohlcv error %s %s: %s", symbol, timeframe, e)
                await asyncio.sleep(min(5 * consecutive_errors, 30))

    async def _watch_ticker_loop(self, symbol: str) -> None:
        ex = self.exchange.exchange
        market_symbol = self.exchange._to_ccxt_symbol(symbol)
        consecutive_errors = 0

        while self._running:
            try:
                raw = await ex.watch_ticker(market_symbol)
                consecutive_errors = 0
                ticker = Ticker(
                    symbol=symbol,
                    bid=Decimal(str(raw.get("bid") or raw.get("last"))),
                    ask=Decimal(str(raw.get("ask") or raw.get("last"))),
                    last=Decimal(str(raw.get("last"))),
                    timestamp=datetime.now(timezone.utc),
                    mark_price=Decimal(str(raw["info"].get("markPrice", 0))) if raw.get("info") else None,
                )
                self._emit(EventType.TICKER, ticker)
            except asyncio.CancelledError:
                break
            except Exception as e:
                msg = str(e).lower()
                consecutive_errors += 1
                if "not supported" in msg or "not implemented" in msg:
                    logger.warning("watch_ticker not supported for %s – stopping this WS task", symbol)
                    break
                logger.warning("watch_ticker error %s: %s", symbol, e)
                await asyncio.sleep(min(3 * consecutive_errors, 20))

    # ──────────────────────────────────────────────
    # REST polling fallback (reliable)
    # ──────────────────────────────────────────────

    async def _rest_polling_loop(self) -> None:
        logger.info("REST polling loop started (every 45 seconds, staggered)")
        while self._running:
            try:
                for symbol in self.symbols:
                    for tf in self.timeframes:
                        try:
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
                            logger.debug("Candle %s %s close=%s", symbol, tf, candle.close)
                        except Exception as e:
                            logger.warning("REST ohlcv error %s %s: %s", symbol, tf, e)

                    # Ticker for mark price / equity updates
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
                    except Exception as e:
                        logger.debug("REST ticker error %s: %s", symbol, e)
                    await asyncio.sleep(0.4)  # stagger per symbol

                await asyncio.sleep(45)  # slower poll — avoid Binance 429
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("REST polling error: %s", e)
                await asyncio.sleep(15)
