#!/usr/bin/env python3
"""
Full historical backtest: fetch OHLCV from Binance (ccxt) → BacktestEngine → metrics.
Optional walk-forward windows.

Usage:
  python scripts/run_backtest.py
  python scripts/run_backtest.py --symbol BTCUSDC --timeframe 15m --days 30
  python scripts/run_backtest.py --walk-forward
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

try:
    import ccxt
except ImportError:
    print("ccxt required: pip install ccxt")
    sys.exit(1)

from backtest.engine import BacktestEngine
from strategy.examples.ema_atr_trend import EmaAtrTrendStrategy


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "15m",
    days: int = 30,
) -> pd.DataFrame:
    """Fetch USDC-M futures OHLCV via ccxt binanceusdm."""
    exchange = ccxt.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "swap"}})

    candidates = []
    if "/" in symbol:
        candidates.append(symbol)
    else:
        base = symbol.replace("USDC", "").replace("USDT", "")
        candidates.extend([f"{base}/USDC:USDC", f"{base}/USDC", f"{base}/USDT:USDT", symbol])

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    all_rows: List[list] = []
    limit = 1000
    market = None
    last_err = None

    for m in candidates:
        try:
            print(f"Fetching {m} {timeframe} last ~{days}d …")
            batch = exchange.fetch_ohlcv(m, timeframe=timeframe, since=since_ms, limit=limit)
            if batch:
                market = m
                all_rows.extend(batch)
                break
        except Exception as e:
            last_err = e
            print(f"  {m} failed: {e}")
            continue

    if not all_rows or market is None:
        raise RuntimeError(f"No OHLCV for {symbol}: {last_err}")

    since_ms = all_rows[-1][0] + 1
    while True:
        try:
            batch = exchange.fetch_ohlcv(market, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception as e:
            print(f"  pagination stop: {e}")
            break
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= since_ms or len(batch) < limit:
            break
        since_ms = last_ts + 1
        time.sleep(max(exchange.rateLimit / 1000.0, 0.05))

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    print(f"  → {len(df)} bars ({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]})")
    return df


def run_single(symbols: List[str], timeframe: str, days: int, capital: float) -> dict:
    candles: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            candles[sym] = fetch_ohlcv(sym, timeframe=timeframe, days=days)
        except Exception as e:
            print(f"Skip {sym}: {e}")

    if not candles:
        raise RuntimeError("No symbols loaded")

    engine = BacktestEngine(
        strategy_class=EmaAtrTrendStrategy,
        strategy_params={},
        symbols=list(candles.keys()),
        starting_capital=Decimal(str(capital)),
    )
    result = engine.run(candles, timeframe=timeframe)
    print(result.summary())

    return {
        "ok": True,
        "mode": "historical",
        "symbols": list(candles.keys()),
        "timeframe": timeframe,
        "days": days,
        "bars": {s: len(df) for s, df in candles.items()},
        "metrics": result.metrics,
        "n_trades": result.metrics.get("n_trades", len(result.trades)),
        "n_signals": len(result.signals),
        "equity_tail": result.equity_curve[-20:] if result.equity_curve else [],
        "trades_tail": result.trades[-15:] if result.trades else [],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def run_walk_forward(symbol: str, timeframe: str, days: int, capital: float, n_windows: int = 3) -> dict:
    df = fetch_ohlcv(symbol, timeframe=timeframe, days=days)
    n = len(df)
    if n < 100:
        return {"ok": False, "error": f"Not enough bars ({n}) for walk-forward"}

    window = n // (n_windows + 1)
    results = []
    all_eq = []
    all_tr = []

    for i in range(n_windows):
        train_end = window * (i + 1)
        test_end = min(train_end + window, n)
        test_df = df.iloc[train_end:test_end].copy()
        if len(test_df) < 30:
            continue
        engine = BacktestEngine(
            strategy_class=EmaAtrTrendStrategy,
            symbols=[symbol],
            starting_capital=Decimal(str(capital)),
        )
        oos = engine.run({symbol: test_df}, timeframe=timeframe)
        results.append({
            "window": i + 1,
            "test_bars": len(test_df),
            "metrics": oos.metrics,
        })
        all_eq.extend(oos.equity_curve)
        all_tr.extend(oos.trades)
        print(f"Window {i+1}: trades={oos.metrics.get('n_trades')} sharpe={oos.metrics.get('sharpe')}")

    from backtest.metrics import compute_metrics
    aggregate = compute_metrics(all_eq, all_tr, starting_capital=capital) if all_eq else {}

    return {
        "ok": True,
        "mode": "walk_forward",
        "symbol": symbol,
        "windows": results,
        "aggregate_metrics": aggregate,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Binance USDC historical backtest")
    parser.add_argument("--symbol", default="BTCUSDC")
    parser.add_argument("--symbols", default="BTCUSDC,ETHUSDC")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.walk_forward:
        print("=== Walk-forward ===")
        payload = run_walk_forward(args.symbol, args.timeframe, args.days, args.capital)
    else:
        print("=== Historical backtest ===")
        payload = run_single(symbols, args.timeframe, args.days, args.capital)

    out = ROOT / "config" / "last_backtest.json"

    def _default(o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, pd.Timestamp)):
            return o.isoformat()
        return str(o)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=_default), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
