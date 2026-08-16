"""
Ultra rule-based agent: multi-TF regime, sessions, funding, correlation,
data quality, streak cooldown, setup score, portfolio heat, playbooks,
strategy scoreboard with auto enable/disable.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Deque, Dict, List, Optional, Set

from agent.regime import MarketRegime, RegimeDetector, RegimeSnapshot

logger = logging.getLogger(__name__)


# ─── Session clock (UTC) ───────────────────────────────────────────

def utc_session() -> str:
    h = datetime.now(timezone.utc).hour
    if 0 <= h < 7:
        return "asia"
    if 7 <= h < 13:
        return "london"
    if 13 <= h < 21:
        return "newyork"
    return "off_hours"


SESSION_SIZE_MULT = {
    "asia": 0.75,
    "london": 1.05,
    "newyork": 1.1,
    "off_hours": 0.55,
}


# ─── Playbooks ─────────────────────────────────────────────────────

PLAYBOOKS = {
    "trend_follow": {
        "allow_regimes": {MarketRegime.TREND_UP, MarketRegime.TREND_DOWN},
        "prefer_aligned": True,
    },
    "mean_reversion": {
        "allow_regimes": {MarketRegime.RANGE},
        "prefer_aligned": False,
    },
    "breakout": {
        "allow_regimes": {MarketRegime.TREND_UP, MarketRegime.TREND_DOWN, MarketRegime.HIGH_VOL},
        "prefer_aligned": True,
    },
}

STRATEGY_PLAYBOOK = {
    "ema_atr_trend_v1": "trend_follow",
    "funding_mr_v1": "mean_reversion",
}


# ─── Correlation groups ────────────────────────────────────────────

CORR_GROUPS = {
    "btc_eth": {"BTCUSDC", "ETHUSDC"},
    "l1": {"SOLUSDC", "AVAXUSDC", "LINKUSDC", "BNBUSDC"},
    "meme": {"DOGEUSDC"},
}


@dataclass
class SetupScore:
    score: float  # 0-100
    reasons: List[str] = field(default_factory=list)


@dataclass
class SmartDecision:
    approve: bool
    score: float
    setup_score: float
    size_mult: float
    reason: str
    tags: List[str]
    playbook: str = ""


@dataclass
class StrategyStats:
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    disabled: bool = False
    disabled_reason: str = ""
    last_trade_ts: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0


class StrategyScoreboard:
    """Auto enable/disable strategies by performance."""

    def __init__(self, min_trades: int = 8, min_win_rate: float = 35.0, max_loss_streak: int = 5):
        self.stats: Dict[str, StrategyStats] = defaultdict(StrategyStats)
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.max_loss_streak = max_loss_streak
        self._loss_streak: Dict[str, int] = defaultdict(int)

    def record(self, strategy_id: str, net_pnl: float) -> Optional[str]:
        """Return disable reason if auto-disabled."""
        s = self.stats[strategy_id]
        s.trades += 1
        s.pnl += net_pnl
        s.last_trade_ts = time.time()
        if net_pnl > 0:
            s.wins += 1
            self._loss_streak[strategy_id] = 0
        else:
            self._loss_streak[strategy_id] += 1

        if self._loss_streak[strategy_id] >= self.max_loss_streak:
            s.disabled = True
            s.disabled_reason = f"Loss streak {self._loss_streak[strategy_id]}"
            return s.disabled_reason

        if s.trades >= self.min_trades and s.win_rate < self.min_win_rate and s.pnl < 0:
            s.disabled = True
            s.disabled_reason = f"Win rate {s.win_rate:.0f}% < {self.min_win_rate}% after {s.trades} trades"
            return s.disabled_reason
        return None

    def enable(self, strategy_id: str) -> None:
        s = self.stats[strategy_id]
        s.disabled = False
        s.disabled_reason = ""
        self._loss_streak[strategy_id] = 0

    def is_enabled(self, strategy_id: str) -> bool:
        return not self.stats[strategy_id].disabled

    def snapshot(self) -> Dict[str, Any]:
        return {
            sid: {
                "trades": s.trades,
                "wins": s.wins,
                "win_rate": round(s.win_rate, 1),
                "pnl": round(s.pnl, 2),
                "disabled": s.disabled,
                "disabled_reason": s.disabled_reason,
            }
            for sid, s in self.stats.items()
        }


class SmartRuleAgent:
    def __init__(self):
        self.regime_tf: Dict[str, RegimeDetector] = {
            "15m": RegimeDetector(40),
            "1h": RegimeDetector(40),
        }
        self.last_regime: Dict[str, Dict[str, RegimeSnapshot]] = defaultdict(dict)
        self.last_price: Dict[str, float] = {}
        self.last_candle_ts: Dict[str, float] = {}
        self.scoreboard = StrategyScoreboard()
        self.loss_streak_global = 0
        self.cooldown_until = 0.0
        self.open_symbols: Set[str] = set()
        self.recent_scores: Deque[float] = deque(maxlen=50)

    # ── observations ──

    def on_candle(self, symbol: str, timeframe: str, close: float, high: float, low: float, ts: float = 0) -> RegimeSnapshot:
        det = self.regime_tf.get(timeframe) or self.regime_tf["15m"]
        snap = det.update(symbol, close, high, low)
        self.last_regime[symbol][timeframe] = snap
        self.last_price[symbol] = close
        self.last_candle_ts[symbol] = ts or time.time()
        return snap

    def set_open_symbols(self, symbols: Set[str]) -> None:
        self.open_symbols = set(symbols)

    # ── gates ──

    def data_quality_ok(self, symbol: str, close: float) -> tuple[bool, str]:
        now = time.time()
        last_ts = self.last_candle_ts.get(symbol, 0)
        if last_ts and now - last_ts > 900:
            return False, "Stale data (>15m without update)"
        prev = self.last_price.get(symbol)
        if prev and prev > 0:
            chg = abs(close - prev) / prev
            if chg > 0.08:
                return False, f"Spike {chg:.1%} — skip"
        return True, "ok"

    def correlation_ok(self, symbol: str, side: str) -> tuple[bool, str]:
        for name, group in CORR_GROUPS.items():
            if symbol not in group:
                continue
            overlap = self.open_symbols & group
            if len(overlap) >= 2 and symbol not in overlap:
                return False, f"Correlation group {name} already hot: {overlap}"
            if len(overlap) >= 2:
                return False, f"Max correlated positions in {name}"
        return True, "ok"

    def funding_ok(self, side: str, funding_rate: Optional[float]) -> tuple[bool, str]:
        if funding_rate is None:
            return True, "no_funding"
        fr = float(funding_rate)
        # long pays positive funding; avoid extreme
        if side.lower() in ("long", "buy") and fr > 0.0008:
            return False, f"Funding too high for long ({fr:.4%})"
        if side.lower() in ("short", "sell") and fr < -0.0008:
            return False, f"Funding too high for short ({fr:.4%})"
        return True, "ok"

    def in_cooldown(self) -> tuple[bool, str]:
        if time.time() < self.cooldown_until:
            left = int(self.cooldown_until - time.time())
            return True, f"Cooldown cooldown {left}s left"
        return False, "ok"

    def portfolio_heat_ok(self, open_count: int, max_positions: int) -> tuple[bool, str]:
        if open_count >= max_positions:
            return False, "Max open positions"
        heat = open_count / max(max_positions, 1)
        if heat >= 0.85:
            return False, f"Portfolio heat {heat:.0%}"
        return True, "ok"

    def setup_quality(
        self,
        strength: float,
        regime_15: Optional[RegimeSnapshot],
        regime_1h: Optional[RegimeSnapshot],
        side: str,
        rr: Optional[float] = None,
    ) -> SetupScore:
        score = 50.0
        reasons = []

        score += max(-20, min(25, (strength - 0.5) * 50))
        if strength >= 0.7:
            reasons.append("strong_signal")

        if regime_1h and regime_15:
            if regime_1h.regime == regime_15.regime and regime_1h.regime != MarketRegime.UNKNOWN:
                score += 15
                reasons.append("mtf_aligned")
            elif regime_1h.regime in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN) and regime_15.regime == MarketRegime.RANGE:
                score -= 10
                reasons.append("htf_trend_ltf_range")

        if regime_15:
            if regime_15.regime == MarketRegime.HIGH_VOL:
                score -= 15
                reasons.append("high_vol")
            aligned = (
                (regime_15.regime == MarketRegime.TREND_UP and side.lower() in ("long", "buy"))
                or (regime_15.regime == MarketRegime.TREND_DOWN and side.lower() in ("short", "sell"))
            )
            if aligned:
                score += 12
                reasons.append("trend_aligned")
            elif regime_15.regime in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
                score -= 18
                reasons.append("counter_trend")

        if rr is not None:
            if rr >= 2.0:
                score += 10
                reasons.append("rr_good")
            elif rr < 1.2:
                score -= 12
                reasons.append("rr_poor")

        sess = utc_session()
        if sess == "off_hours":
            score -= 8
            reasons.append("off_hours")
        elif sess in ("london", "newyork"):
            score += 5
            reasons.append(sess)

        score = max(0, min(100, score))
        return SetupScore(score=score, reasons=reasons)

    def playbook_allows(self, strategy_id: str, regime: Optional[MarketRegime], side: str) -> tuple[bool, str]:
        pb_name = STRATEGY_PLAYBOOK.get(strategy_id, "trend_follow")
        pb = PLAYBOOKS.get(pb_name, PLAYBOOKS["trend_follow"])
        if regime and regime not in pb["allow_regimes"] and regime != MarketRegime.UNKNOWN:
            return False, f"Playbook {pb_name} blocks regime {regime.value}"
        return True, pb_name

    def evaluate(
        self,
        symbol: str,
        side: str,
        strength: float,
        strategy_id: str,
        funding_rate: Optional[float] = None,
        open_count: int = 0,
        max_positions: int = 6,
        rr: Optional[float] = None,
        close: Optional[float] = None,
    ) -> SmartDecision:
        tags: List[str] = []
        reasons: List[str] = []

        # Global cooldown
        cd, cd_msg = self.in_cooldown()
        if cd:
            return SmartDecision(False, 0, 0, 0, cd_msg, ["cooldown"])

        # Strategy scoreboard
        if not self.scoreboard.is_enabled(strategy_id):
            st = self.scoreboard.stats[strategy_id]
            return SmartDecision(False, 0, 0, 0, f"Strategy disabled: {st.disabled_reason}", ["auto_disabled"])

        # Data quality
        if close is not None:
            ok, msg = self.data_quality_ok(symbol, close)
            if not ok:
                return SmartDecision(False, 0, 0, 0, msg, ["data_quality"])

        # Correlation
        ok, msg = self.correlation_ok(symbol, side)
        if not ok:
            return SmartDecision(False, 0, 0, 0, msg, ["correlation"])

        # Funding
        ok, msg = self.funding_ok(side, funding_rate)
        if not ok:
            return SmartDecision(False, 0, 0, 0, msg, ["funding"])

        # Portfolio heat
        ok, msg = self.portfolio_heat_ok(open_count, max_positions)
        if not ok:
            return SmartDecision(False, 0, 0, 0, msg, ["heat"])

        r15 = self.last_regime.get(symbol, {}).get("15m")
        r1h = self.last_regime.get(symbol, {}).get("1h")
        reg = r15.regime if r15 else MarketRegime.UNKNOWN

        ok, pb = self.playbook_allows(strategy_id, reg, side)
        if not ok:
            return SmartDecision(False, 0, 0, 0, pb, ["playbook"])

        setup = self.setup_quality(strength, r15, r1h, side, rr)
        tags.extend(setup.reasons)

        # Size mult from session + setup
        sess_m = SESSION_SIZE_MULT.get(utc_session(), 0.8)
        setup_m = 0.5 + (setup.score / 100.0) * 0.75  # 0.5–1.25
        size_mult = max(0.25, min(1.25, sess_m * setup_m))

        approve = setup.score >= 48
        reason = f"setup={setup.score:.0f} playbook={pb} session={utc_session()}"
        if not approve:
            reason = f"Setup score {setup.score:.0f} < 48; " + ",".join(setup.reasons)

        self.recent_scores.append(setup.score)
        return SmartDecision(
            approve=approve,
            score=setup.score / 100.0,
            setup_score=setup.score,
            size_mult=size_mult,
            reason=reason,
            tags=tags,
            playbook=pb if ok else "",
        )

    def on_trade_closed(self, strategy_id: str, net_pnl: float) -> Optional[str]:
        if net_pnl < 0:
            self.loss_streak_global += 1
        else:
            self.loss_streak_global = 0

        if self.loss_streak_global >= 4:
            self.cooldown_until = time.time() + 30 * 60  # 30 min
            logger.warning("Global loss streak → 30m cooldown")

        return self.scoreboard.record(strategy_id, net_pnl)

    def snapshot(self) -> Dict[str, Any]:
        regimes = {
            sym: {tf: {"regime": s.regime.value, "conf": s.confidence, "mom": s.momentum}
                  for tf, s in tfs.items()}
            for sym, tfs in self.last_regime.items()
        }
        return {
            "session": utc_session(),
            "cooldown_until": self.cooldown_until,
            "loss_streak_global": self.loss_streak_global,
            "regimes": regimes,
            "scoreboard": self.scoreboard.snapshot(),
            "avg_setup_score": sum(self.recent_scores) / len(self.recent_scores) if self.recent_scores else 0,
        }
