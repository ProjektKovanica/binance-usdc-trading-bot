"""
Main Orchestrator / Event Loop.
Wires Strategy → Risk → Execution → Portfolio together.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
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
from agent.regime import RegimeDetector
from agent.memory import TradeMemory
from agent.decision import DecisionAgent
from agent.adaptive_risk import AdaptiveRiskController
from agent.watchdog import HealthWatchdog
from agent.smart_rules import SmartRuleAgent
from monitoring.alerts import AlertManager, AlertPriority

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
        # Agentic layer
        self.regime_detector = RegimeDetector()
        self.memory = TradeMemory()
        self.decision_agent = DecisionAgent(memory=self.memory)
        self.adaptive_risk = AdaptiveRiskController()
        self.watchdog = HealthWatchdog()
        self._last_regimes = {}
        self.smart = SmartRuleAgent()
        self.alerts: AlertManager | None = None


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
        # Telegram / Discord alerts from env
        try:
            import os
            from pathlib import Path
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parents[1] / ".env")
            self.alerts = AlertManager(
                telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                discord_webhook=os.getenv("DISCORD_WEBHOOK", ""),
            )
            await self.alerts.start()
            if os.getenv("TELEGRAM_BOT_TOKEN"):
                await self.alerts.send(
                    f"Engine started in *{self.mode.value}* mode",
                    AlertPriority.INFO,
                    title="USDC Bot",
                )
        except Exception as e:
            logger.warning("Alerts init failed: %s", e)
            self.alerts = None
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
        # Background: apply dashboard / runtime settings (kill switch + risk limits)
        self._tasks.append(asyncio.create_task(self._runtime_control_loop()))
        # Background: publish status for dashboard
        self._tasks.append(asyncio.create_task(self._status_publish_loop()))
        logger.info("Engine running with %d strategies + live data feed", len(self.strategies))

    async def stop(self) -> None:
        self._running = False
        if hasattr(self, "feed"):
            await self.feed.stop()
        for t in getattr(self, "_tasks", []):
            t.cancel()
        await self.execution.stop()
        await self.exchange.close()
        if self.alerts:
            try:
                await self.alerts.stop()
            except Exception:
                pass
        for s in self.strategies:
            await s.on_stop()
        logger.info("TradingEngine stopped")

    def _control_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "config" / "runtime_control.json"

    def _status_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "config" / "runtime_status.json"

    async def _runtime_control_loop(self) -> None:
        """Poll dashboard-written control file and apply to live RiskManager."""
        last_mtime = 0.0
        path = self._control_path()
        logger.info("Runtime control loop watching %s", path)
        while self._running:
            try:
                if path.exists():
                    mtime = path.stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        data = json.loads(path.read_text(encoding="utf-8"))
                        # Kill switch
                        if data.get("kill_switch"):
                            reason = data.get("kill_reason") or data.get("kill_description") or "Dashboard kill switch"
                            if not self.risk.kill_switch_active:
                                self.risk.activate_kill_switch(str(reason))
                                if self.alerts:
                                    await self.alerts.send(
                                        f"KILL SWITCH ON\n{reason}",
                                        AlertPriority.CRITICAL,
                                        title="Kill Switch",
                                    )
                        else:
                            if self.risk.kill_switch_active and data.get("updated_by") == "dashboard":
                                self.risk.reset_kill_switch()
                                if self.alerts:
                                    await self.alerts.send(
                                        "Kill switch reset — trading may resume",
                                        AlertPriority.WARNING,
                                        title="Kill Switch",
                                    )
                        # Risk limits
                        self.risk.update_limits(
                            max_daily_loss_usdc=data.get("max_daily_loss_usdc"),
                            max_position_pct_equity=data.get("max_position_pct_equity"),
                            max_leverage=data.get("max_leverage"),
                            max_open_positions=data.get("max_open_positions"),
                        )
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Runtime control loop error: %s", e)
                await asyncio.sleep(5)

    async def _status_publish_loop(self) -> None:
        """Write live engine snapshot for dashboard to read."""
        path = self._status_path()
        while self._running:
            try:
                snap = self.risk.get_snapshot()
                self.watchdog.mark_status()
                regimes = {
                    s: {
                        "regime": r.regime.value,
                        "atr_pct": r.atr_pct,
                        "momentum": r.momentum,
                        "confidence": r.confidence,
                    }
                    for s, r in self._last_regimes.items()
                }
                payload = {
                    "mode": self.mode.value,
                    "equity": float(self.portfolio.equity),
                    "available_balance": float(self.portfolio.available_balance),
                    "unrealized_pnl": float(getattr(self.portfolio, "unrealized_pnl", 0) or 0),
                    "realized_pnl": float(getattr(self.portfolio, "realized_pnl", 0) or 0),
                    "open_positions": [
                        {
                            "symbol": p.symbol,
                            "side": getattr(p.side, "value", str(p.side)),
                            "quantity": float(p.quantity),
                            "unrealized_pnl": float(getattr(p, "unrealized_pnl", 0) or 0),
                        }
                        for p in self.portfolio.get_open_positions()
                    ],
                    "risk": snap,
                    "agent": {
                        "memory": self.memory.summary(),
                        "regimes": regimes,
                        "health": self.watchdog.as_dict(),
                        "smart": self.smart.snapshot(),
                        "adaptive_mult_hint": float(
                            self.adaptive_risk.size_multiplier(
                                max_drawdown_pct=float(snap.get("max_drawdown_pct", 0) or 0),
                                kill_switch=bool(snap.get("kill_switch")),
                            )
                        ),
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Status publish error: %s", e)
                await asyncio.sleep(5)

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
        self.watchdog.mark_candle()

        # Regime update (agent observation)
        regime = self.regime_detector.update(
            candle.symbol,
            float(candle.close),
            float(candle.high),
            float(candle.low),
        )
        self._last_regimes[candle.symbol] = regime
        tf = getattr(candle, "timeframe", "15m") or "15m"
        self.smart.on_candle(candle.symbol, tf, float(candle.close), float(candle.high), float(candle.low))
        # also push to 1h detector on every candle as proxy if only one stream
        if tf != "1h":
            self.smart.on_candle(candle.symbol, "1h", float(candle.close), float(candle.high), float(candle.low))
        self.memory.add(
            "regime",
            symbol=candle.symbol,
            regime=regime.regime.value,
            atr_pct=regime.atr_pct,
            momentum=regime.momentum,
            confidence=regime.confidence,
        )

        ctx = StrategyContext(
            equity=self.portfolio.equity,
            available_balance=self.portfolio.available_balance,
            positions={p.symbol: p for p in self.portfolio.get_open_positions()},  # type: ignore
            open_orders=self.execution.get_open_orders(),
            indicators={"regime": regime.regime.value, "regime_confidence": regime.confidence},
        )

        for strategy in self.strategies:
            if not strategy.enabled:
                continue
            try:
                signal = await strategy.on_bar(candle, ctx)
                if signal:
                    await self._handle_signal(signal, candle.close)
            except Exception as e:
                self.watchdog.note_error(f"{strategy.strategy_id}: {e}")
                logger.exception("Strategy %s error: %s", strategy.strategy_id, e)

    async def _handle_signal(self, signal: Signal, current_price: Decimal) -> None:
        funding = await self.exchange.fetch_funding_rate(signal.symbol)

        # Ultra rule agent: MTF, funding, correlation, playbook, setup score, heat
        strength = float(signal.metadata.get("strength", signal.metadata.get("confidence", 0.55)))
        side = getattr(signal.side, "value", str(signal.side))
        strategy_id = getattr(signal, "strategy_id", "") or ""
        rr = signal.metadata.get("rr") or signal.metadata.get("risk_reward")
        open_pos = self.portfolio.get_open_positions()
        self.smart.set_open_symbols({p.symbol for p in open_pos})
        snap = self.risk.get_snapshot()

        # Auto-disable from scoreboard → strategy.enabled
        for sid, st in self.smart.scoreboard.stats.items():
            for s in self.strategies:
                if s.strategy_id == sid:
                    if st.disabled and s.enabled:
                        s.disable()
                        logger.warning("Auto-disabled strategy %s: %s", sid, st.disabled_reason)
                        if self.alerts:
                            await self.alerts.send(
                                f"Strategy *{sid}* auto-disabled\n{st.disabled_reason}",
                                AlertPriority.WARNING,
                                title="Strategy disabled",
                            )
                    elif not st.disabled and not s.enabled and not st.disabled_reason:
                        pass  # keep manual disable

        smart = self.smart.evaluate(
            symbol=signal.symbol,
            side=side,
            strength=strength,
            strategy_id=strategy_id,
            funding_rate=float(funding) if funding is not None else None,
            open_count=len(open_pos),
            max_positions=int(self.risk.limits.max_open_positions),
            rr=float(rr) if rr is not None else None,
            close=float(current_price),
        )
        if not smart.approve:
            logger.info("Signal rejected by SmartAgent: %s", smart.reason)
            self.memory.add("decision", symbol=signal.symbol, approve=False, reason=smart.reason, score=smart.setup_score)
            return

        regime = self._last_regimes.get(signal.symbol)
        mult = self.adaptive_risk.size_multiplier(
            regime=regime.regime if regime else None,
            max_drawdown_pct=float(snap.get("max_drawdown_pct", 0) or 0),
            kill_switch=bool(snap.get("kill_switch")),
        ) * Decimal(str(smart.size_mult))
        mult = max(Decimal("0.25"), min(Decimal("1.25"), mult))
        if mult <= 0:
            logger.info("Adaptive risk multiplier=0 (kill/paused)")
            return

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

        # Apply adaptive sizing
        try:
            decision.order_request.quantity = (
                Decimal(str(decision.order_request.quantity)) * mult
            ).quantize(Decimal("0.0001"))
        except Exception:
            pass

        try:
            order = await self.execution.submit(decision.order_request)
            self.memory.add(
                "signal",
                symbol=signal.symbol,
                side=side,
                score=agent_dec.score,
                mult=float(mult),
                order_id=getattr(order, "id", ""),
            )
            logger.info(
                "Order submitted: %s %s qty=%s status=%s | agent_score=%.2f mult=%s",
                order.side.value,
                order.symbol,
                order.quantity,
                order.status.value,
                agent_dec.score,
                mult,
            )
        except Exception as e:
            self.watchdog.note_error(f"execution: {e}")
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
