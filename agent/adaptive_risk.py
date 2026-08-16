"""Adaptive risk: scale size by recent performance + regime."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from agent.regime import MarketRegime


class AdaptiveRiskController:
    """
    Returns a size multiplier in [0.25, 1.25] based on:
    - recent win rate
    - current regime
    - drawdown
    """

    def __init__(self):
        self.recent_pnls: list[float] = []

    def record_trade(self, net_pnl: float) -> None:
        self.recent_pnls.append(float(net_pnl))
        self.recent_pnls = self.recent_pnls[-30:]

    def size_multiplier(
        self,
        regime: MarketRegime = MarketRegime.UNKNOWN,
        max_drawdown_pct: float = 0.0,
        kill_switch: bool = False,
    ) -> Decimal:
        if kill_switch:
            return Decimal("0")

        # Performance factor
        if len(self.recent_pnls) >= 5:
            wins = sum(1 for p in self.recent_pnls if p > 0)
            wr = wins / len(self.recent_pnls)
            if wr >= 0.55:
                perf = 1.15
            elif wr >= 0.45:
                perf = 1.0
            elif wr >= 0.35:
                perf = 0.75
            else:
                perf = 0.5
        else:
            perf = 0.85  # conservative until enough samples

        # Regime factor
        regime_map = {
            MarketRegime.TREND_UP: 1.1,
            MarketRegime.TREND_DOWN: 1.05,
            MarketRegime.RANGE: 0.7,
            MarketRegime.HIGH_VOL: 0.55,
            MarketRegime.UNKNOWN: 0.8,
        }
        reg = regime_map.get(regime, 0.8)

        # Drawdown brake
        if max_drawdown_pct >= 0.06:
            dd = 0.4
        elif max_drawdown_pct >= 0.04:
            dd = 0.65
        elif max_drawdown_pct >= 0.02:
            dd = 0.85
        else:
            dd = 1.0

        mult = perf * reg * dd
        mult = max(0.25, min(1.25, mult))
        return Decimal(str(round(mult, 3)))
