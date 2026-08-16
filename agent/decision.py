"""Decision agent: filters / scores signals before risk manager."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from agent.memory import TradeMemory
from agent.regime import MarketRegime, RegimeSnapshot

logger = logging.getLogger(__name__)


@dataclass
class AgentDecision:
    approve: bool
    score: float  # 0-1
    reason: str
    tags: list


class DecisionAgent:
    """
    Rule-based agentic filter (LLM hook optional later).
    Observe regime + signal quality → approve/reject with score.
    """

    def __init__(self, memory: Optional[TradeMemory] = None):
        self.memory = memory or TradeMemory()

    def evaluate_signal(
        self,
        symbol: str,
        side: str,
        strength: float,
        regime: Optional[RegimeSnapshot] = None,
        strategy_id: str = "",
    ) -> AgentDecision:
        tags = []
        score = float(strength) if strength else 0.5
        reasons = []

        reg = regime.regime if regime else MarketRegime.UNKNOWN
        conf = regime.confidence if regime else 0.0

        # Regime alignment
        if reg == MarketRegime.HIGH_VOL:
            score *= 0.55
            tags.append("high_vol_penalty")
            reasons.append("High volatility: size/priority reduced")
        elif reg == MarketRegime.RANGE:
            if abs(strength) < 0.6:
                score *= 0.5
                tags.append("range_weak")
                reasons.append("Ranging market + weak signal")
            else:
                score *= 0.85
                tags.append("range_ok")
        elif reg == MarketRegime.TREND_UP and side.lower() in ("long", "buy"):
            score *= 1.15
            tags.append("trend_aligned")
            reasons.append("Long aligned with uptrend")
        elif reg == MarketRegime.TREND_DOWN and side.lower() in ("short", "sell"):
            score *= 1.15
            tags.append("trend_aligned")
            reasons.append("Short aligned with downtrend")
        elif reg in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
            score *= 0.65
            tags.append("counter_trend")
            reasons.append("Counter-trend signal deprioritized")

        if conf < 0.35 and reg != MarketRegime.UNKNOWN:
            score *= 0.9
            tags.append("low_regime_conf")

        score = max(0.0, min(1.0, score))
        approve = score >= 0.42

        reason = "; ".join(reasons) if reasons else ("Approved" if approve else "Score too low")
        decision = AgentDecision(approve=approve, score=score, reason=reason, tags=tags)

        self.memory.add(
            "decision",
            symbol=symbol,
            side=side,
            strategy_id=strategy_id,
            approve=approve,
            score=score,
            reason=reason,
            regime=reg.value if hasattr(reg, "value") else str(reg),
            tags=tags,
        )
        logger.info(
            "Agent decision %s %s score=%.2f approve=%s | %s",
            symbol,
            side,
            score,
            approve,
            reason,
        )
        return decision
