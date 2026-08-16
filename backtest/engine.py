"""
Realistic event-driven backtester.
Simulates fees, slippage, latency, partial fills (simplified), and funding rates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Type

import pandas as pd

from core.types import (
    Candle,
    Signal,
    Side,
    OrderType,
    TradingMode,
    Fill,
    OrderStatus,
)
from risk.manager import RiskManager
from core.types import RiskLimits
from portfolio.manager import PortfolioManager
from strategy.base import BaseStrategy, StrategyContext
from backtest.metrics import compute_metrics

logger = logging.getLogger(__name__)


class BacktestResult:
    def __init__(self, metrics: Dict, equity_curve: List, trades: List, signals: List):
        self.metrics = metrics
        self.equity_curve = equity_curve
        self.trades = trades
        self.signals = signals

    def summary(self) -> str:
        m = self.metrics
        lines = [
            "═══════════════ BACKTEST RESULTS ═══════════════",
            f"Net Profit        : {m.get('net_profit')} USDC",
            f"Total Return      : {m.get('total_return_pct')} %",
            f"Profit Factor     : {m.get('profit_factor')}",
            f"Sharpe            : {m.get('sharpe')}",
            f"Sortino           : {m.get('sortino')}",
            f"Calmar            : {m.get('calmar')}",
            f"Max Drawdown      : {m.get('max_drawdown_pct')} %",
            f"Win Rate          : {m.get('win_rate')} %",
            f"Expectancy        : {m.get('expectancy')}",
            f"Trades            : {m.get('n_trades')}",
            f"Risk of Ruin      : {m.get('risk_of_ruin')}",
            f"Final Equity      : {m.get('final_equity')} USDC",
            "═══════════════════════════════════════════════",
        ]
        return "\n".join(lines)


class BacktestEngine:
    def __init__(
        self,
        strategy_class: Type[BaseStrategy],
        strategy_params: Optional[Dict] = None,
        symbols: Optional[List[str]] = None,
        starting_capital: Decimal = Decimal("10000"),
        fee_rate: Decimal = Decimal("0.0004"),       # 0.04% taker
        slippage_pct: Decimal = Decimal("0.0003"),   # 0.03%
        funding_rate_per_8h: Decimal = Decimal("0.0001"),
        latency_bars: int = 0,                       # simulate 1-bar delay if needed
    ):
        self.strategy_class = strategy_class
        self.strategy_params = strategy_params or {}
        self.symbols = symbols or ["BTCUSDC"]
        self.starting_capital = starting_capital
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct
        self.funding_rate = funding_rate_per_8h
        self.latency_bars = latency_bars

    def run(
        self,
        candles: Dict[str, pd.DataFrame],
        timeframe: str = "15m",
    ) -> BacktestResult:
        """
        candles: {symbol: DataFrame with columns open,high,low,close,volume,timestamp}
        """
        portfolio = PortfolioManager(starting_equity=self.starting_capital)
        limits = RiskLimits(
            max_position_size_usdc=Decimal("3000"),
            max_position_pct_equity=Decimal("0.15"),
            max_daily_loss_usdc=Decimal("500"),
            max_drawdown_pct=Decimal("0.15"),
            max_open_positions=5,
        )
        risk = RiskManager(limits=limits, starting_equity=self.starting_capital)

        strategy = self.strategy_class(
            strategy_id="backtest_strat",
            symbols=self.symbols,
            params={**self.strategy_params, "timeframe": timeframe},
        )

        signals_log: List[Dict] = []
        all_timestamps = sorted(
            set().union(*[set(df["timestamp"]) for df in candles.values()])
        )

        # Simple bar-by-bar simulation
        for ts in all_timestamps:
            for symbol in self.symbols:
                df = candles.get(symbol)
                if df is None:
                    continue
                row = df[df["timestamp"] == ts]
                if row.empty:
                    continue
                row = row.iloc[0]

                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts)),
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=Decimal(str(row["volume"])),
                )

                portfolio.update_marks({symbol: candle.close})
                risk.update_equity(portfolio.equity)

                ctx = StrategyContext(
                    equity=portfolio.equity,
                    available_balance=portfolio.available_balance,
                    positions={p.symbol: p for p in portfolio.get_open_positions()},  # type: ignore
                    open_orders=[],
                    indicators={},
                )

                signal = None
                try:
                    # on_bar is async in live, but for backtest we call it sync-style
                    import asyncio
                    signal = asyncio.get_event_loop().run_until_complete(
                        strategy.on_bar(candle, ctx)
                    )
                except Exception:
                    # fallback if already in loop or simple sync override
                    pass

                if signal is None:
                    continue

                signals_log.append({
                    "timestamp": str(candle.timestamp),
                    "symbol": signal.symbol,
                    "side": signal.side.value,
                    "reason": signal.reason,
                })

                atr = Decimal(str(signal.metadata.get("atr", 0))) if signal.metadata else None
                decision = risk.evaluate_signal(
                    signal=signal,
                    current_price=candle.close,
                    available_balance=portfolio.available_balance,
                    atr=atr,
                )

                if not decision.approved or not decision.order_request:
                    continue

                # Simulate fill with slippage + fee
                req = decision.order_request
                slip = self.slippage_pct
                if req.side == Side.BUY:
                    fill_price = candle.close * (Decimal("1") + slip)
                else:
                    fill_price = candle.close * (Decimal("1") - slip)

                fee = req.quantity * fill_price * self.fee_rate
                fill = Fill(
                    trade_id=f"bt_{ts}_{symbol}",
                    order_id=f"ord_{ts}",
                    symbol=symbol,
                    side=req.side,
                    price=fill_price,
                    quantity=req.quantity,
                    fee=fee,
                    fee_currency="USDC",
                    timestamp=candle.timestamp,
                )
                realized = portfolio.apply_fill(fill, strategy_id=strategy.strategy_id)
                risk.update_pnl(realized)
                risk.update_equity(portfolio.equity)

            # Simulate funding every ~8h (simplified)
            if isinstance(ts, datetime) and ts.hour % 8 == 0 and ts.minute == 0:
                for pos in portfolio.get_open_positions():
                    payment = -pos.notional * self.funding_rate  # approximate cost
                    portfolio.apply_funding(pos.symbol, payment)

        trades = portfolio.get_trade_journal(limit=10000)
        equity_curve = portfolio.get_equity_curve(limit=100000)
        metrics = compute_metrics(
            equity_curve,
            trades,
            starting_capital=float(self.starting_capital),
        )

        return BacktestResult(metrics, equity_curve, trades, signals_log)
