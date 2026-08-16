"""
Simple funding-rate aware mean-reversion strategy for USDC-M futures.
When funding is extremely positive → bias short (longs pay shorts).
When extremely negative → bias long.
Combined with a basic Bollinger / price deviation filter.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Any, Optional, List

import pandas as pd

from strategy.base import BaseStrategy, StrategyContext
from core.types import Candle, Signal, Side, SignalStrength, OrderType, MarketType


class FundingMeanReversionStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "funding_mr_v1",
        symbols: List[str] | None = None,
        params: Dict[str, Any] | None = None,
        market_type: MarketType = MarketType.FUTURES,
    ):
        defaults = {
            "bb_period": 20,
            "bb_std": 2.0,
            "funding_threshold": 0.0003,   # 0.03%
            "timeframe": "1h",
            "atr_period": 14,
            "atr_mult_stop": 2.0,
            "rr_target": 2.0,
        }
        if params:
            defaults.update(params)
        super().__init__(strategy_id, symbols or ["BTCUSDC", "ETHUSDC"], defaults)
        self.market_type = market_type
        self._candles: Dict[str, list] = {s: [] for s in self.symbols}
        self._last_funding: Dict[str, Decimal] = {}

    async def on_bar(self, candle: Candle, ctx: StrategyContext) -> Optional[Signal]:
        if candle.symbol not in self.symbols or candle.timeframe != self.params["timeframe"]:
            return None

        buf = self._candles[candle.symbol]
        buf.append(candle)
        if len(buf) > 100:
            buf.pop(0)
        if len(buf) < self.params["bb_period"] + 5:
            return None

        df = pd.DataFrame({
            "close": [float(c.close) for c in buf],
            "high": [float(c.high) for c in buf],
            "low": [float(c.low) for c in buf],
        })

        mid = df["close"].rolling(self.params["bb_period"]).mean()
        std = df["close"].rolling(self.params["bb_period"]).std()
        upper = mid + self.params["bb_std"] * std
        lower = mid - self.params["bb_std"] * std

        price = Decimal(str(df["close"].iloc[-1]))
        upper_b = Decimal(str(upper.iloc[-1]))
        lower_b = Decimal(str(lower.iloc[-1]))

        # ATR for stops
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = Decimal(str(tr.rolling(self.params["atr_period"]).mean().iloc[-1]))

        existing = ctx.positions.get(candle.symbol)
        has_pos = existing and existing.is_open

        funding = self._last_funding.get(candle.symbol, Decimal("0"))
        thr = Decimal(str(self.params["funding_threshold"]))

        # Extremely high funding + price at upper band → short
        if (
            funding > thr
            and price >= upper_b
            and not has_pos
            and self.market_type == MarketType.FUTURES
        ):
            stop = price + atr * Decimal(str(self.params["atr_mult_stop"]))
            risk = stop - price
            tp = price - risk * Decimal(str(self.params["rr_target"]))
            return self.create_signal(
                symbol=candle.symbol,
                side=Side.SELL,
                reason=f"High funding {funding} + upper BB rejection",
                strength=SignalStrength.MEDIUM,
                confidence=0.6,
                stop_loss=stop,
                take_profit=tp,
                metadata={"funding": str(funding), "atr": str(atr)},
            )

        # Extremely negative funding + price at lower band → long
        if funding < -thr and price <= lower_b and not has_pos:
            stop = price - atr * Decimal(str(self.params["atr_mult_stop"]))
            risk = price - stop
            tp = price + risk * Decimal(str(self.params["rr_target"]))
            return self.create_signal(
                symbol=candle.symbol,
                side=Side.BUY,
                reason=f"Negative funding {funding} + lower BB bounce",
                strength=SignalStrength.MEDIUM,
                confidence=0.6,
                stop_loss=stop,
                take_profit=tp,
                metadata={"funding": str(funding), "atr": str(atr)},
            )

        return None

    def update_funding(self, symbol: str, rate: Decimal) -> None:
        self._last_funding[symbol] = rate
