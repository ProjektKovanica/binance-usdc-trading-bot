"""
Institutional-grade performance metrics.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Dict, Any, Optional
import math

import numpy as np
import pandas as pd


def compute_metrics(
    equity_curve: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    starting_capital: float = 10000.0,
    periods_per_year: int = 365 * 24 * 4,  # approx for 15m bars
) -> Dict[str, Any]:
    if not equity_curve:
        return {}

    eq = pd.Series([p["equity"] for p in equity_curve])
    rets = eq.pct_change().dropna()

    total_return = (eq.iloc[-1] / starting_capital) - 1.0
    net_profit = eq.iloc[-1] - starting_capital

    # Drawdown
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    max_dd_duration = _max_dd_duration(dd)

    # Risk-adjusted
    sharpe = 0.0
    sortino = 0.0
    if len(rets) > 2 and rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * math.sqrt(periods_per_year))
        downside = rets[rets < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = float(rets.mean() / downside.std() * math.sqrt(periods_per_year))

    calmar = abs(total_return / max_dd) if max_dd != 0 else 0.0

    # Trade stats
    wins = [t for t in trades if t.get("net_pnl", 0) > 0]
    losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
    n_trades = len(trades)
    win_rate = len(wins) / n_trades if n_trades else 0.0

    avg_win = float(np.mean([t["net_pnl"] for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t["net_pnl"] for t in losses])) if losses else 0.0
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Risk of ruin (simplified Gaussian approximation)
    risk_of_ruin = _risk_of_ruin(win_rate, avg_win, avg_loss, starting_capital)

    return {
        "net_profit": round(net_profit, 2),
        "total_return_pct": round(total_return * 100, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_dd_duration_bars": max_dd_duration,
        "win_rate": round(win_rate * 100, 2),
        "expectancy": round(expectancy, 4),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "n_trades": n_trades,
        "risk_of_ruin": round(risk_of_ruin, 4),
        "final_equity": round(float(eq.iloc[-1]), 2),
    }


def _max_dd_duration(dd: pd.Series) -> int:
    in_dd = dd < 0
    if not in_dd.any():
        return 0
    groups = (~in_dd).cumsum()
    durations = in_dd.groupby(groups).sum()
    return int(durations.max()) if len(durations) else 0


def _risk_of_ruin(win_rate: float, avg_win: float, avg_loss: float, capital: float) -> float:
    """Very approximate risk of ruin."""
    if avg_loss == 0 or win_rate >= 1.0:
        return 0.0
    edge = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    if edge <= 0:
        return 1.0
    # Simplified formula
    try:
        r = abs(avg_loss) / avg_win if avg_win else 1
        p = win_rate
        q = 1 - p
        if p == q:
            return 1.0
        ruin = ((q / p) ** (capital / abs(avg_loss))) if p > q else 1.0
        return min(max(ruin, 0.0), 1.0)
    except Exception:
        return 0.5
