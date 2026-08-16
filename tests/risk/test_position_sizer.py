"""
Critical path unit tests for PositionSizer.
"""

from decimal import Decimal
import pytest

from risk.position_sizer import PositionSizer
from core.types import Signal, Side, SignalStrength, RiskLimits, OrderType


@pytest.fixture
def limits():
    return RiskLimits(
        max_position_size_usdc=Decimal("2000"),
        max_position_pct_equity=Decimal("0.10"),
    )


@pytest.fixture
def signal():
    return Signal(
        strategy_id="test",
        symbol="BTCUSDC",
        side=Side.BUY,
        strength=SignalStrength.MEDIUM,
        reason="unit test",
        confidence=0.7,
        stop_loss=Decimal("60000"),
    )


def test_atr_sizing_respects_max_notional(limits, signal):
    sizer = PositionSizer(limits, method="atr")
    result = sizer.calculate(
        signal=signal,
        equity=Decimal("10000"),
        current_price=Decimal("65000"),
        atr=Decimal("800"),
    )
    assert result.quantity > 0
    assert result.notional_usdc <= limits.max_position_size_usdc
    assert result.notional_usdc <= Decimal("10000") * limits.max_position_pct_equity
    assert result.method == "atr"


def test_zero_equity_returns_zero(limits, signal):
    sizer = PositionSizer(limits, method="pct_equity")
    result = sizer.calculate(
        signal=signal,
        equity=Decimal("0"),
        current_price=Decimal("65000"),
    )
    assert result.quantity == 0


def test_kelly_capped(limits, signal):
    sizer = PositionSizer(limits, method="kelly")
    result = sizer.calculate(
        signal=signal,
        equity=Decimal("10000"),
        current_price=Decimal("65000"),
        win_rate=0.6,
        avg_win_loss_ratio=2.0,
        atr=Decimal("500"),
    )
    # Half-Kelly of a reasonable edge should still be capped by max notional
    assert result.notional_usdc <= limits.max_position_size_usdc
    assert result.method == "kelly"
