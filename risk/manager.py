"""
Central Risk Manager – the single gatekeeper for every order.
No order reaches the Execution Engine without passing through here.

Responsibilities:
- Position sizing
- Hard limits (daily/weekly loss, max DD, max positions, max leverage)
- Correlation / exposure checks
- Kill-switch enforcement
- Funding rate & margin awareness (futures)
- Rejection with clear reason (for logging & dashboard)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from core.types import (
    Signal,
    OrderRequest,
    OrderType,
    Side,
    Position,
    PortfolioSnapshot,
    RiskLimits,
    Event,
    EventType,
)
from risk.position_sizer import PositionSizer, PositionSizeResult

logger = logging.getLogger(__name__)


class RiskDecision(BaseModel):
    approved: bool
    order_request: Optional[OrderRequest] = None
    reason: str
    size_result: Optional[PositionSizeResult] = None
    risk_score: float = 0.0  # 0-1, higher = more aggressive


class RiskManager:
    """
    Thread-safe / async-safe design: all state is updated only through
    explicit methods called from the main event loop.
    """

    def __init__(
        self,
        limits: RiskLimits,
        position_sizing_method: str = "atr",
        starting_equity: Decimal = Decimal("10000"),
    ):
        self.limits = limits
        self.sizer = PositionSizer(limits, method=position_sizing_method)
        self.starting_equity = starting_equity
        self.current_equity = starting_equity

        # State
        self.daily_pnl: Decimal = Decimal("0")
        self.weekly_pnl: Decimal = Decimal("0")
        self.peak_equity: Decimal = starting_equity
        self.max_drawdown_pct: Decimal = Decimal("0")
        self.kill_switch_active: bool = False
        self.kill_reason: str = ""
        self.open_positions: Dict[str, Position] = {}  # symbol -> Position
        self.daily_reset_at: datetime = self._next_daily_reset()
        self.weekly_reset_at: datetime = self._next_weekly_reset()

        # Simple correlation proxy (can be replaced with real matrix later)
        self.correlated_groups: Dict[str, List[str]] = {
            "majors": ["BTCUSDC", "ETHUSDC"],
            "l1": ["SOLUSDC", "AVAXUSDC", "LINKUSDC"],
            "meme": ["DOGEUSDC"],
        }

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def evaluate_signal(
        self,
        signal: Signal,
        current_price: Decimal,
        available_balance: Decimal,
        atr: Optional[Decimal] = None,
        funding_rate: Optional[Decimal] = None,
        current_leverage: Decimal = Decimal("1"),
        win_rate: Optional[float] = None,
        avg_rr: Optional[float] = None,
    ) -> RiskDecision:
        """
        Main entry point called by the Engine for every strategy signal.
        Returns either an approved OrderRequest or a rejection reason.
        """

        # 1. Kill switch
        if self.kill_switch_active:
            return RiskDecision(
                approved=False,
                reason=f"KILL SWITCH ACTIVE: {self.kill_reason}",
            )

        # 2. Reset daily / weekly counters if needed
        self._maybe_reset_periods()

        # 3. Hard loss limits
        if self.daily_pnl <= -self.limits.max_daily_loss_usdc:
            self._activate_kill_switch("Max daily loss breached")
            return RiskDecision(approved=False, reason="Max daily loss reached")

        if self.daily_pnl <= -(self.current_equity * self.limits.max_daily_loss_pct):
            self._activate_kill_switch("Max daily loss % breached")
            return RiskDecision(approved=False, reason="Max daily loss % reached")

        if self.weekly_pnl <= -self.limits.max_weekly_loss_usdc:
            self._activate_kill_switch("Max weekly loss breached")
            return RiskDecision(approved=False, reason="Max weekly loss reached")

        # 4. Drawdown
        dd = self._current_drawdown_pct()
        if dd >= self.limits.max_drawdown_pct:
            self._activate_kill_switch(f"Max drawdown {dd:.2%} breached")
            return RiskDecision(approved=False, reason=f"Max drawdown {dd:.2%} reached")

        # 5. Max open positions
        if len(self.open_positions) >= self.limits.max_open_positions:
            # Allow only reduce-only
            if not self._is_reducing(signal):
                return RiskDecision(
                    approved=False,
                    reason=f"Max open positions ({self.limits.max_open_positions}) reached",
                )

        # 6. Existing position in same symbol – check side conflict
        existing = self.open_positions.get(signal.symbol)
        if existing and existing.is_open:
            if (existing.side.value == "long" and signal.side == Side.BUY) or (
                existing.side.value == "short" and signal.side == Side.SELL
            ):
                # Adding to position – still allowed but size will be checked
                pass
            else:
                # Opposite side → treat as close / reverse (allowed)
                pass

        # 7. Correlation / group exposure
        if not self._check_correlation_ok(signal.symbol, current_price):
            return RiskDecision(
                approved=False,
                reason="Correlated exposure limit exceeded",
            )

        # 8. Leverage
        if current_leverage > self.limits.max_leverage:
            return RiskDecision(
                approved=False,
                reason=f"Leverage {current_leverage} > max {self.limits.max_leverage}",
            )

        # 9. Funding rate awareness (futures) – soft warning, not hard reject
        funding_warning = ""
        if funding_rate is not None:
            # If we are going long and funding is very positive → expensive
            if signal.side == Side.BUY and funding_rate > Decimal("0.0005"):
                funding_warning = f"High positive funding {funding_rate}"
            elif signal.side == Side.SELL and funding_rate < Decimal("-0.0005"):
                funding_warning = f"High negative funding {funding_rate}"

        # 10. Position sizing
        size_result = self.sizer.calculate(
            signal=signal,
            equity=self.current_equity,
            current_price=current_price,
            atr=atr,
            win_rate=win_rate,
            avg_win_loss_ratio=avg_rr,
            stop_loss_price=signal.stop_loss,
        )

        if size_result.quantity <= 0:
            return RiskDecision(
                approved=False,
                reason=f"Position size calculated as zero: {size_result.reason}",
                size_result=size_result,
            )

        # 11. Final notional check vs available balance (margin)
        required_margin = size_result.notional_usdc / max(current_leverage, Decimal("1"))
        if required_margin > available_balance * Decimal("0.95"):  # leave 5% buffer
            return RiskDecision(
                approved=False,
                reason=f"Insufficient margin. Need ~{required_margin:.2f}, available {available_balance:.2f}",
                size_result=size_result,
            )

        # 12. Build OrderRequest
        order_req = OrderRequest(
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.order_type,
            quantity=size_result.quantity,
            price=signal.suggested_price,
            stop_price=signal.stop_loss,
            strategy_id=signal.strategy_id,
            reason=f"{signal.reason} | size: {size_result.reason} | {funding_warning}".strip(" |"),
            metadata={
                "signal_id": signal.id,
                "confidence": signal.confidence,
                "risk_amount": str(size_result.risk_amount_usdc),
                "sizing_method": size_result.method,
            },
        )

        # Optional: attach TP as separate order later in execution layer
        if signal.take_profit:
            order_req.metadata["take_profit"] = str(signal.take_profit)
        if signal.trailing_stop_pct:
            order_req.metadata["trailing_stop_pct"] = str(signal.trailing_stop_pct)

        risk_score = float(min(size_result.notional_usdc / self.current_equity, 1.0))

        return RiskDecision(
            approved=True,
            order_request=order_req,
            reason="Approved",
            size_result=size_result,
            risk_score=risk_score,
        )

    # ──────────────────────────────────────────────
    # State updates (called by Portfolio / Engine)
    # ──────────────────────────────────────────────

    def update_equity(self, new_equity: Decimal) -> None:
        self.current_equity = new_equity
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity
        dd = self._current_drawdown_pct()
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd

    def update_pnl(self, realized_delta: Decimal) -> None:
        self.daily_pnl += realized_delta
        self.weekly_pnl += realized_delta
        self.current_equity += realized_delta

    def register_position(self, position: Position) -> None:
        if position.quantity == 0:
            self.open_positions.pop(position.symbol, None)
        else:
            self.open_positions[position.symbol] = position

    def activate_kill_switch(self, reason: str) -> None:
        self._activate_kill_switch(reason)

    def reset_kill_switch(self) -> None:
        self.kill_switch_active = False
        self.kill_reason = ""
        logger.warning("Kill switch manually reset")

    def get_snapshot(self) -> Dict:
        return {
            "equity": float(self.current_equity),
            "peak_equity": float(self.peak_equity),
            "daily_pnl": float(self.daily_pnl),
            "weekly_pnl": float(self.weekly_pnl),
            "max_drawdown_pct": float(self.max_drawdown_pct),
            "kill_switch": self.kill_switch_active,
            "kill_reason": self.kill_reason,
            "open_positions": len(self.open_positions),
            "risk_utilization_pct": float(
                sum(p.notional for p in self.open_positions.values())
                / max(self.current_equity, Decimal("1"))
                * 100
            ),
        }

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _activate_kill_switch(self, reason: str) -> None:
        self.kill_switch_active = True
        self.kill_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def _current_drawdown_pct(self) -> Decimal:
        if self.peak_equity <= 0:
            return Decimal("0")
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def _is_reducing(self, signal: Signal) -> bool:
        pos = self.open_positions.get(signal.symbol)
        if not pos or not pos.is_open:
            return False
        if pos.side.value == "long" and signal.side == Side.SELL:
            return True
        if pos.side.value == "short" and signal.side == Side.BUY:
            return True
        return False

    def _check_correlation_ok(self, symbol: str, price: Decimal) -> bool:
        """Very simple group exposure check. Can be upgraded to real correlation matrix."""
        for group_name, members in self.correlated_groups.items():
            if symbol not in members:
                continue
            group_notional = Decimal("0")
            for m in members:
                pos = self.open_positions.get(m)
                if pos and pos.is_open:
                    group_notional += pos.notional
            # Add the new potential position roughly
            max_group = self.current_equity * self.limits.max_correlated_exposure_pct
            if group_notional > max_group * Decimal("0.9"):
                return False
        return True

    def _maybe_reset_periods(self) -> None:
        now = datetime.now(timezone.utc)
        if now >= self.daily_reset_at:
            self.daily_pnl = Decimal("0")
            self.daily_reset_at = self._next_daily_reset()
            logger.info("Daily PnL counter reset")
        if now >= self.weekly_reset_at:
            self.weekly_pnl = Decimal("0")
            self.weekly_reset_at = self._next_weekly_reset()
            logger.info("Weekly PnL counter reset")

    def _next_daily_reset(self) -> datetime:
        now = datetime.now(timezone.utc)
        tomorrow = now.date() + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)

    def _next_weekly_reset(self) -> datetime:
        now = datetime.now(timezone.utc)
        days_ahead = 7 - now.weekday()  # next Monday
        if days_ahead == 7:
            days_ahead = 0
        next_monday = now.date() + timedelta(days=days_ahead)
        return datetime(next_monday.year, next_monday.month, next_monday.day, tzinfo=timezone.utc)
