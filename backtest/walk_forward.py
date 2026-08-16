"""
Walk-forward analysis helper.
Splits history into sequential in-sample / out-of-sample windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Dict, Any, Type, Optional
from decimal import Decimal

import pandas as pd

from backtest.engine import BacktestEngine, BacktestResult
from strategy.base import BaseStrategy


def walk_forward(
    strategy_class: Type[BaseStrategy],
    candles: Dict[str, pd.DataFrame],
    strategy_params: Optional[Dict] = None,
    n_splits: int = 5,
    train_ratio: float = 0.7,
    starting_capital: Decimal = Decimal("10000"),
) -> Dict[str, Any]:
    """
    Simple sequential walk-forward.
    Returns aggregate OOS metrics + per-window results.
    """
    # Use the first symbol's index as the master timeline
    symbol = list(candles.keys())[0]
    df = candles[symbol].sort_values("timestamp").reset_index(drop=True)
    n = len(df)
    window = n // n_splits

    results = []
    all_oos_equity = []
    all_oos_trades = []

    for i in range(n_splits):
        start = i * window
        end = min((i + 1) * window, n)
        if end - start < 50:
            continue

        split_point = start + int((end - start) * train_ratio)
        train_df = df.iloc[start:split_point]
        test_df = df.iloc[split_point:end]

        # Build candle dicts for this window (simplified – only one symbol)
        train_candles = {symbol: train_df}
        test_candles = {symbol: test_df}

        engine = BacktestEngine(
            strategy_class=strategy_class,
            strategy_params=strategy_params,
            symbols=[symbol],
            starting_capital=starting_capital,
        )

        # We only evaluate on the out-of-sample (test) period
        # In a more advanced version we would optimize params on train first
        oos_result = engine.run(test_candles)
        results.append({
            "window": i + 1,
            "train_bars": len(train_df),
            "test_bars": len(test_df),
            "metrics": oos_result.metrics,
        })
        all_oos_equity.extend(oos_result.equity_curve)
        all_oos_trades.extend(oos_result.trades)

    from backtest.metrics import compute_metrics
    aggregate = compute_metrics(
        all_oos_equity,
        all_oos_trades,
        starting_capital=float(starting_capital),
    )

    return {
        "aggregate_oos_metrics": aggregate,
        "windows": results,
        "n_splits": n_splits,
    }
