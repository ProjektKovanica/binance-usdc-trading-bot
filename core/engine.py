"""
Main Orchestrator / Event Loop.
Wires Strategy → Risk → Execution → Portfolio together.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from core.config import Settings, load_settings
from core.types import (
    TradingMode,
    Signal,
    Candle,
    Event,
    EventType,
    OrderRequest,
)
from risk.manager import RiskManager
from core.types import RiskLimits
from execution.engine import ExecutionEngine
from portfolio.manager import PortfolioManager
from strategy.base import BaseStrategy, StrategyContext
from strategy.examples.ema_atr_trend import EmaAtrTrendStrategy
from exchanges.binance_futures import BinanceFuturesUSDC
from exchanges.binance_spot import BinanceSpotUSDC
from data.feed import MarketDataFeed

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or load_settings()
        self.mode = TradingMode(self.settings.mode)

        # Portfolio (source of truth for equity & positions)
        self.portfolio = PortfolioManager(
            starting_equity=self.settings.starting_capital_usdc,
            currency="USDC",
        )

        # Risk
        limits = RiskLimits(
            max_position_size_usdc=self.settings.risk.max_position_size_usdc,
            max_position_pct_equity=self.settings.risk.max_position_pct_equity,
            max_daily_loss_usdc=self.settings.risk.max_daily_loss_usdc,
            max_daily_loss_pct=self.settings.risk.max_daily_loss_pct,
            max_weekly_loss_usdc=self.settings.risk.max_weekly_loss_usdc,
            max_drawdown_pct=self.settings.risk.max_drawdown_pct,
            max_leverage=self.settings.risk.max_leverage,
            max_open_positions=self.settings.risk.max_open_positions,
            max_correlated_exposure_pct=self.settings.risk.max_correlated_exposure_pct,
            min_risk_reward=self.settings.risk.min_risk_reward,
        )
        self.risk = RiskManager(
            limits=limits,
            position_sizing_method=self.settings.risk.position_sizing,
            starting_equity=self.settings.starting_capital_usdc,
        )

        # Exchange (default futures USDC)
        self.exchange = BinanceFuturesUSDC(
            api_key=self.settings.binance_api_key,
            secret=self.settings.binance_api_secret,
            sandbox=self.settings.binance_sandbox,
            default_leverage=int(self.settings.risk.max_leverage),
        )

        # Execution
        self.execution = ExecutionEngine(
            mode=self.mode,
            exchange_adapter=self.exchange,
            max_slippage_pct=self.settings.execution.max_slippage_pct,
            retry_attempts=self.settings.execution.retry_attempts,
        )
        self.execution.on_event(self._on_execution_event)

        # Strategies
        self.strategies: List[BaseStrategy] = []
        self._load_default_strategies()

        self._running = False
        self._tasks: List[asyncio.Task] = []

    def _load_default_strategies(self) -> None:
        strat = EmaAtrTrendStrategy(
            strategy_id="ema_atr_trend_v1",
            symbols=self.settings.symbols[:4],  # start with liquid ones
            params={
                "timeframe": "15m",
                "fast_ema": 12,
                "slow_ema": 26,
                "atr_period": 14,
                "allow_short": True,
            },
        )
        self.strategies.append(strat)

    async def start(self) -> None:
        logger.info("Starting TradingEngine in %s mode", self.mode.value)
        await self.exchange.connect()
        await self.execution.start()

        for s in self.strategies:
            await s.on_start()

        # Market data feed – REST polling is currently more reliable with binanceusdm
        self.feed = MarketDataFeed(
            exchange_adapter=self.exchange,
            symbols=self.settings.symbols[:5],
            timeframes=["15m", "1h"],
            use_websocket=False,
        )
        self.feed.on_event(self._on_market_event)
        await self.feed.start()

        self._running = True
        logger.info("Engine running with %d strategies + live data feed", len(self.strategies))

    async def stop(self) -> None:
        self._running = False
        if hasattr(self, "feed"):
            await self.feed.stop()
        for t in self._tasks:
            t.cancel()
        await self.execution.stop()
        await self.exchange.close()
        for s in self.strategies:
            await s.on_stop()
        logger.info("TradingEngine stopped")

    def _on_market_event(self, event: Event) -> None:
        """Bridge from data feed into the async engine."""
        if event.type == EventType.CANDLE:
            asyncio.create_task(self._process_candle(event.payload))
        elif event.type == EventType.TICKER:
            # Keep marks fresh for unrealized PnL
            ticker = event.payload
            self.portfolio.update_marks({ticker.symbol: ticker.last})
            self.risk.update_equity(self.portfolio.equity)

    async def _process_candle(self, candle: Candle) -> None:
        # Keep portfolio marks fresh
        self.portfolio.update_marks({candle.symbol: candle.close})
        self.risk.update_equity(self.portfolio.equity)

        ctx = StrategyContext(
            equity=self.portfolio.equity,
            available_balance=self.portfolio.available_balance,
            positions={p.symbol: p for p in self.portfolio.get_open_positions()},  # type: ignore
            open_orders=self.execution.get_open_orders(),
            indicators={},
        )

        for strategy in self.strategies:
            if not strategy.enabled:
                continue
            try:
                signal = await strategy.on_bar(candle, ctx)
                if signal:
                    await self._handle_signal(signal, candle.close)
            except Exception as e:
                logger.exception("Strategy %s error: %s", strategy.strategy_id, e)

    async def _handle_signal(self, signal: Signal, current_price: Decimal) -> None:
        funding = await self.exchange.fetch_funding_rate(signal.symbol)

        # Pass ATR from signal metadata if present
        atr = None
        if signal.metadata.get("atr"):
            atr = Decimal(str(signal.metadata["atr"]))

        decision = self.risk.evaluate_signal(
            signal=signal,
            current_price=current_price,
            available_balance=self.portfolio.available_balance,
            atr=atr,
            funding_rate=funding,
        )

        if not decision.approved or not decision.order_request:
            logger.info("Signal rejected by Risk: %s", decision.reason)
            return

        try:
            order = await self.execution.submit(decision.order_request)
            logger.info(
                "Order submitted: %s %s qty=%s status=%s",
                order.side.value,
                order.symbol,
                order.quantity,
                order.status.value,
            )
        except Exception as e:
            logger.error("Execution failed: %s", e)

    def _on_execution_event(self, event: Event) -> None:
        if event.type == EventType.FILL:
            fill = event.payload
            realized = self.portfolio.apply_fill(fill, strategy_id=getattr(fill, "strategy_id", ""))
            self.risk.update_pnl(realized)
            self.risk.update_equity(self.portfolio.equity)

            # Sync open positions into Risk Manager
            for pos in self.portfolio.get_open_positions():
                from core.types import Position as CorePos
                core_pos = CorePos(
                    symbol=pos.symbol,
                    side=pos.side,
                    quantity=abs(pos.quantity),
                    entry_price=pos.entry_price,
                    mark_price=pos.mark_price,
                    unrealized_pnl=pos.unrealized_pnl,
                    realized_pnl=pos.realized_pnl,
                    leverage=pos.leverage,
                    strategy_id=pos.strategy_id,
                    opened_at=pos.opened_at or datetime.now(timezone.utc),
                    updated_at=pos.updated_at,
                )
                self.risk.register_position(core_pos)

            logger.info(
                "Fill processed | realized=%.4f | equity=%.2f",
                float(realized),
                float(self.portfolio.equity),
            )

        elif event.type == EventType.ORDER_UPDATE:
            pass


async def main():
    logging.basicConfig(level=logging.INFO)
    engine = TradingEngine()
    try:
        await engine.start()
        # Keep running
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
