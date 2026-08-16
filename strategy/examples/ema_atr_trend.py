"""
Example production-ready strategy:
Multi-timeframe EMA trend + ATR volatility filter + dynamic stop.

Works on both Spot and USDC-M Futures.
Easy to backtest and move to live (same code path).

Logic (simplified):
- Fast EMA > Slow EMA → bullish bias
- Price above fast EMA + ATR expansion → long entry
- Symmetric for short (futures only)
- Stop = entry ± 1.8 * ATR
- Take profit = 2.5 * risk (R:R ≈ 2.5)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Any

import pandas as pd
import numpy as np

from strategy.base import BaseStrategy, StrategyContext
from core.types import Candle, Signal, Side, SignalStrength, OrderType, MarketType


class EmaAtrTrendStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "ema_atr_trend_v1",
        symbols: list[str] | None = None,
        params: Dict[str, Any] | None = None,
        market_type: MarketType = MarketType.FUTURES,
    ):
        default_params = {
            "fast_ema": 12,
            "slow_ema": 26,
            "atr_period": 14,
            "atr_mult_stop": 1.8,
            "rr_target": 2.5,
            "min_atr_pct": 0.002,          # ignore dead markets
            "timeframe": "15m",            # primary
            "allow_short": True,          # only meaningful on futures
        }
        if params:
            default_params.update(params)
        super().__init__(strategy_id, symbols or ["BTCUSDC", "ETHUSDC"], default_params)
        self.market_type = market_type
        self._candles: Dict[str, list] = {s: [] for s in self.symbols}
        self._last_signal_bar: Dict[str, int] = {}

    async def on_start(self) -> None:
        print(f"[{self.strategy_id}] started on {self.symbols}")

    async def on_bar(self, candle: Candle, ctx: StrategyContext) -> Optional[Signal]:
        if candle.symbol not in self.symbols:
            return None
        if candle.timeframe != self.params["timeframe"]:
            return None

        # Keep rolling window of candles
        buf = self._candles[candle.symbol]
        buf.append(candle)
        max_len = max(self.params["slow_ema"], self.params["atr_period"]) + 50
        if len(buf) > max_len:
            buf.pop(0)

        if len(buf) < max_len - 10:
            return None  # not enough history yet

        df = self._to_df(buf)
        indicators = self._compute_indicators(df)
        ctx.indicators.update(indicators)  # expose for dashboard / logging

        return self._generate_signal(candle, indicators, ctx)

    def _to_df(self, candles: list[Candle]) -> pd.DataFrame:
        data = {
            "timestamp": [c.timestamp for c in candles],
            "open": [float(c.open) for c in candles],
            "high": [float(c.high) for c in candles],
            "low": [float(c.low) for c in candles],
            "close": [float(c.close) for c in candles],
            "volume": [float(c.volume) for c in candles],
        }
        return pd.DataFrame(data)

    def _compute_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        fast = close.ewm(span=self.params["fast_ema"], adjust=False).mean()
        slow = close.ewm(span=self.params["slow_ema"], adjust=False).mean()

        # ATR
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.params["atr_period"]).mean()

        return {
            "ema_fast": Decimal(str(fast.iloc[-1])),
            "ema_slow": Decimal(str(slow.iloc[-1])),
            "atr": Decimal(str(atr.iloc[-1])),
            "atr_pct": Decimal(str(atr.iloc[-1] / close.iloc[-1])),
            "trend_up": bool(fast.iloc[-1] > slow.iloc[-1]),
            "trend_down": bool(fast.iloc[-1] < slow.iloc[-1]),
            "price": Decimal(str(close.iloc[-1])),
        }

    def _generate_signal(
        self, candle: Candle, ind: Dict[str, Any], ctx: StrategyContext
    ) -> Optional[Signal]:
        symbol = candle.symbol
        price = ind["price"]
        atr = ind["atr"]
        atr_pct = ind["atr_pct"]

        # Filter dead volatility
        if atr_pct < Decimal(str(self.params["min_atr_pct"])):
            return None

        # Avoid spamming signals on same bar
        bar_ts = int(candle.timestamp.timestamp())
        if self._last_signal_bar.get(symbol) == bar_ts:
            return None

        existing = ctx.positions.get(symbol)
        has_long = existing and existing.side.value == "long" and existing.is_open
        has_short = existing and existing.side.value == "short" and existing.is_open

        stop_mult = Decimal(str(self.params["atr_mult_stop"]))
        rr = Decimal(str(self.params["rr_target"]))

        # ─── Long entry ───
        if ind["trend_up"] and price > ind["ema_fast"] and not has_long:
            stop = price - atr * stop_mult
            risk = price - stop
            tp = price + risk * rr
            self._last_signal_bar[symbol] = bar_ts
            return self.create_signal(
                symbol=symbol,
                side=Side.BUY,
                reason=f"EMA trend up + price > fast EMA | ATR={atr:.4f}",
                strength=SignalStrength.MEDIUM,
                confidence=0.65,
                stop_loss=stop,
                take_profit=tp,
                order_type=OrderType.MARKET,
                metadata={"atr": str(atr), "ema_fast": str(ind["ema_fast"])},
            )

        # ─── Short entry (futures only) ───
        if (
            self.params["allow_short"]
            and self.market_type == MarketType.FUTURES
            and ind["trend_down"]
            and price < ind["ema_fast"]
            and not has_short
        ):
            stop = price + atr * stop_mult
            risk = stop - price
            tp = price - risk * rr
            self._last_signal_bar[symbol] = bar_ts
            return self.create_signal(
                symbol=symbol,
                side=Side.SELL,
                reason=f"EMA trend down + price < fast EMA | ATR={atr:.4f}",
                strength=SignalStrength.MEDIUM,
                confidence=0.65,
                stop_loss=stop,
                take_profit=tp,
                order_type=OrderType.MARKET,
                metadata={"atr": str(atr), "ema_fast": str(ind["ema_fast"])},
            )

        # ─── Exit logic (simple) ───
        if has_long and ind["trend_down"]:
            self._last_signal_bar[symbol] = bar_ts
            return self.create_signal(
                symbol=symbol,
                side=Side.SELL,
                reason="Trend flipped down – close long",
                strength=SignalStrength.STRONG,
                confidence=0.8,
                order_type=OrderType.MARKET,
                metadata={"reduce_only": True},
            )

        if has_short and ind["trend_up"]:
            self._last_signal_bar[symbol] = bar_ts
            return self.create_signal(
                symbol=symbol,
                side=Side.BUY,
                reason="Trend flipped up – close short",
                strength=SignalStrength.STRONG,
                confidence=0.8,
                order_type=OrderType.MARKET,
                metadata={"reduce_only": True},
            )

        return None
