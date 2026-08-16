"""Market regime detection (trend / range / high-vol)."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Deque, Dict, Optional


class MarketRegime(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    HIGH_VOL = "high_vol"
    UNKNOWN = "unknown"


@dataclass
class RegimeSnapshot:
    symbol: str
    regime: MarketRegime
    atr_pct: float
    momentum: float
    confidence: float


class RegimeDetector:
    """Lightweight regime classifier from recent closes."""

    def __init__(self, lookback: int = 40):
        self.lookback = lookback
        self._closes: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=lookback))
        self._highs: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=lookback))
        self._lows: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=lookback))

    def update(self, symbol: str, close: float, high: Optional[float] = None, low: Optional[float] = None) -> RegimeSnapshot:
        self._closes[symbol].append(float(close))
        if high is not None:
            self._highs[symbol].append(float(high))
        if low is not None:
            self._lows[symbol].append(float(low))
        return self.classify(symbol)

    def classify(self, symbol: str) -> RegimeSnapshot:
        closes = list(self._closes.get(symbol, []))
        if len(closes) < max(15, self.lookback // 2):
            return RegimeSnapshot(symbol, MarketRegime.UNKNOWN, 0.0, 0.0, 0.0)

        # Momentum: front vs back half
        mid = len(closes) // 2
        back = sum(closes[:mid]) / mid
        front = sum(closes[mid:]) / (len(closes) - mid)
        momentum = (front - back) / back if back else 0.0

        # ATR proxy
        highs = list(self._highs.get(symbol, closes))
        lows = list(self._lows.get(symbol, closes))
        n = min(len(closes), len(highs), len(lows), 14)
        trs = []
        for i in range(1, n):
            tr = max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i - 1]) if len(closes) > i else 0,
                abs(lows[-i] - closes[-i - 1]) if len(closes) > i else 0,
            )
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else 0.0
        atr_pct = atr / closes[-1] if closes[-1] else 0.0

        if atr_pct > 0.035:
            regime = MarketRegime.HIGH_VOL
            conf = min(1.0, atr_pct / 0.05)
        elif momentum > 0.012:
            regime = MarketRegime.TREND_UP
            conf = min(1.0, abs(momentum) / 0.03)
        elif momentum < -0.012:
            regime = MarketRegime.TREND_DOWN
            conf = min(1.0, abs(momentum) / 0.03)
        else:
            regime = MarketRegime.RANGE
            conf = 0.6

        return RegimeSnapshot(symbol, regime, atr_pct, momentum, conf)
