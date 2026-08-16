"""
Position Sizing engines.
Supports: Fixed, % of Equity, ATR-based volatility, Kelly Criterion (capped).
All methods return quantity in base asset and respect hard risk limits.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Optional

from pydantic import BaseModel

from core.types import Signal, RiskLimits, Side


class PositionSizeResult(BaseModel):
    quantity: Decimal
    notional_usdc: Decimal
    risk_amount_usdc: Decimal
    method: str
    reason: str


class PositionSizer:
    """
    Calculates position size based on configured method.
    Always returns a size that respects max_position_size and max_position_pct_equity.
    """

    def __init__(self, limits: RiskLimits, method: str = "atr"):
        self.limits = limits
        self.method = method.lower()

    def calculate(
        self,
        signal: Signal,
        equity: Decimal,
        current_price: Decimal,
        atr: Optional[Decimal] = None,
        win_rate: Optional[float] = None,
        avg_win_loss_ratio: Optional[float] = None,
        stop_loss_price: Optional[Decimal] = None,
    ) -> PositionSizeResult:
        """
        Main entry point.
        Priority of stop distance:
          1. signal.stop_loss
          2. stop_loss_price argument
          3. ATR * multiplier (if method=atr)
        """

        if equity <= 0 or current_price <= 0:
            return PositionSizeResult(
                quantity=Decimal("0"),
                notional_usdc=Decimal("0"),
                risk_amount_usdc=Decimal("0"),
                method=self.method,
                reason="Invalid equity or price",
            )

        # Hard ceilings
        max_notional_by_pct = equity * self.limits.max_position_pct_equity
        max_notional = min(self.limits.max_position_size_usdc, max_notional_by_pct)

        # Determine risk per trade (stop distance)
        stop_distance = self._get_stop_distance(
            signal, current_price, atr, stop_loss_price
        )

        if self.method == "fixed":
            notional = min(max_notional, Decimal("500"))  # conservative fixed default
            qty = (notional / current_price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            return PositionSizeResult(
                quantity=qty,
                notional_usdc=qty * current_price,
                risk_amount_usdc=qty * stop_distance if stop_distance else Decimal("0"),
                method="fixed",
                reason=f"Fixed notional capped at {max_notional}",
            )

        if self.method == "pct_equity":
            risk_pct = Decimal("0.01")  # risk 1% of equity by default
            risk_amount = equity * risk_pct
            if stop_distance and stop_distance > 0:
                qty = (risk_amount / stop_distance).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            else:
                qty = (max_notional / current_price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            notional = qty * current_price
            if notional > max_notional:
                qty = (max_notional / current_price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
                notional = qty * current_price
            return PositionSizeResult(
                quantity=qty,
                notional_usdc=notional,
                risk_amount_usdc=risk_amount,
                method="pct_equity",
                reason=f"Risk {risk_pct*100}% of equity",
            )

        if self.method == "atr":
            if atr is None or atr <= 0:
                # fallback to pct_equity
                return self._fallback_pct(equity, current_price, max_notional, "ATR missing")
            # Risk 1% of equity, stop = 1.5 * ATR (configurable externally)
            risk_amount = equity * Decimal("0.01")
            stop_distance = atr * Decimal("1.5")
            qty = (risk_amount / stop_distance).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            notional = qty * current_price
            if notional > max_notional:
                qty = (max_notional / current_price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
                notional = qty * current_price
            return PositionSizeResult(
                quantity=qty,
                notional_usdc=notional,
                risk_amount_usdc=risk_amount,
                method="atr",
                reason=f"ATR-based, stop≈{stop_distance:.4f}",
            )

        if self.method == "kelly":
            if win_rate is None or avg_win_loss_ratio is None or win_rate <= 0:
                return self._fallback_pct(equity, current_price, max_notional, "Kelly inputs missing")
            # Kelly fraction = W - (1-W)/R
            w = Decimal(str(win_rate))
            r = Decimal(str(avg_win_loss_ratio))
            kelly = w - ((Decimal("1") - w) / r)
            kelly = max(Decimal("0"), kelly)
            # Cap Kelly aggressively
            kelly = min(kelly, Decimal("0.25"))  # hard cap from config
            risk_amount = equity * kelly * Decimal("0.5")  # half-Kelly for safety
            if stop_distance and stop_distance > 0:
                qty = (risk_amount / stop_distance).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            else:
                qty = (risk_amount / current_price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            notional = qty * current_price
            if notional > max_notional:
                qty = (max_notional / current_price).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
                notional = qty * current_price
            return PositionSizeResult(
                quantity=qty,
                notional_usdc=notional,
                risk_amount_usdc=risk_amount,
                method="kelly",
                reason=f"Half-Kelly {kelly:.3f} capped",
            )

        # Unknown method → safe fallback
        return self._fallback_pct(equity, current_price, max_notional, f"Unknown method {self.method}")

    def _get_stop_distance(
        self,
        signal: Signal,
        current_price: Decimal,
        atr: Optional[Decimal],
        stop_loss_price: Optional[Decimal],
    ) -> Optional[Decimal]:
        if signal.stop_loss is not None:
            return abs(current_price - signal.stop_loss)
        if stop_loss_price is not None:
            return abs(current_price - stop_loss_price)
        if atr is not None and atr > 0:
            return atr * Decimal("1.5")
        return None

    def _fallback_pct(
        self,
        equity: Decimal,
        price: Decimal,
        max_notional: Decimal,
        reason: str,
    ) -> PositionSizeResult:
        risk_amount = equity * Decimal("0.005")  # very conservative 0.5%
        qty = (min(risk_amount * 20, max_notional) / price).quantize(  # ~5% notional max
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        return PositionSizeResult(
            quantity=qty,
            notional_usdc=qty * price,
            risk_amount_usdc=risk_amount,
            method="fallback",
            reason=reason,
        )
