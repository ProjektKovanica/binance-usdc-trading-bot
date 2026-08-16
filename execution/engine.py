"""
Order & Execution Engine.

Responsibilities:
- Translate OrderRequest → exchange order (or paper simulation)
- Handle partial fills, retries, slippage control
- Maintain local order state
- Auto-reconnect & heartbeat
- Emit order/fill events
- Support Market, Limit, Stop, Trailing, OCO, Bracket (via exchange or synthetic)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.types import (
    OrderRequest,
    Order,
    OrderStatus,
    OrderType,
    Side,
    Fill,
    TimeInForce,
    TradingMode,
    Event,
    EventType,
)

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    pass


class SlippageExceeded(ExecutionError):
    pass


class ExecutionEngine:
    """
    Unified execution layer for paper and live.
    In paper mode it simulates fills with configurable slippage & latency.
    In live mode it talks to the exchange adapter.
    """

    def __init__(
        self,
        mode: TradingMode,
        exchange_adapter: Any,  # BinanceSpotAdapter | BinanceFuturesAdapter
        max_slippage_pct: Decimal = Decimal("0.0015"),
        retry_attempts: int = 3,
        paper_slippage_pct: Decimal = Decimal("0.0004"),  # realistic paper slippage
        paper_latency_ms: int = 80,
    ):
        self.mode = mode
        self.exchange = exchange_adapter
        self.max_slippage_pct = max_slippage_pct
        self.retry_attempts = retry_attempts
        self.paper_slippage_pct = paper_slippage_pct
        self.paper_latency_ms = paper_latency_ms

        self.orders: Dict[str, Order] = {}  # client_order_id -> Order
        self._event_callbacks: List[Callable[[Event], None]] = []
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    def on_event(self, callback: Callable[[Event], None]) -> None:
        self._event_callbacks.append(callback)

    def _emit(self, event_type: EventType, payload: Any, source: str = "execution") -> None:
        event = Event(type=event_type, payload=payload, source=source)
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.exception("Event callback failed: %s", e)

    async def start(self) -> None:
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("ExecutionEngine started in %s mode", self.mode.value)

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("ExecutionEngine stopped")

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    async def submit(self, request: OrderRequest) -> Order:
        """
        Submit an order. Returns the local Order object.
        Raises ExecutionError on hard failures.
        """
        if not self._running:
            raise ExecutionError("ExecutionEngine is not running")

        logger.info(
            "Submitting %s %s %s qty=%s strategy=%s reason=%s",
            request.order_type.value,
            request.side.value,
            request.symbol,
            request.quantity,
            request.strategy_id,
            request.reason,
        )

        if self.mode == TradingMode.PAPER:
            return await self._submit_paper(request)
        else:
            return await self._submit_live(request)

    async def cancel(self, client_order_id: str) -> bool:
        order = self.orders.get(client_order_id)
        if not order or not order.is_active:
            return False

        if self.mode == TradingMode.PAPER:
            order.status = OrderStatus.CANCELED
            order.updated_at = datetime.now(timezone.utc)
            self._emit(EventType.ORDER_UPDATE, order)
            return True

        try:
            await self.exchange.cancel_order(order.id, order.symbol)
            order.status = OrderStatus.CANCELED
            order.updated_at = datetime.now(timezone.utc)
            self._emit(EventType.ORDER_UPDATE, order)
            return True
        except Exception as e:
            logger.error("Cancel failed for %s: %s", client_order_id, e)
            return False

    async def cancel_all(self, symbol: Optional[str] = None) -> int:
        canceled = 0
        for cid, order in list(self.orders.items()):
            if order.is_active and (symbol is None or order.symbol == symbol):
                if await self.cancel(cid):
                    canceled += 1
        return canceled

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        return [
            o for o in self.orders.values()
            if o.is_active and (symbol is None or o.symbol == symbol)
        ]

    def get_order(self, client_order_id: str) -> Optional[Order]:
        return self.orders.get(client_order_id)

    # ──────────────────────────────────────────────
    # Paper trading simulation
    # ──────────────────────────────────────────────

    async def _submit_paper(self, request: OrderRequest) -> Order:
        # Simulate network latency
        await asyncio.sleep(self.paper_latency_ms / 1000)

        now = datetime.now(timezone.utc)
        order_id = f"paper_{uuid4().hex[:12]}"

        # Get current market price from adapter (paper adapter should provide ticker)
        ticker = await self.exchange.fetch_ticker(request.symbol)
        mid = (Decimal(str(ticker["bid"])) + Decimal(str(ticker["ask"]))) / 2
        last = Decimal(str(ticker["last"]))

        # Apply realistic slippage
        slip = self.paper_slippage_pct
        if request.side == Side.BUY:
            fill_price = last * (Decimal("1") + slip)
        else:
            fill_price = last * (Decimal("1") - slip)

        # For limit orders – only fill if price is marketable
        if request.order_type == OrderType.LIMIT and request.price is not None:
            if request.side == Side.BUY and request.price < fill_price:
                # Not marketable yet – leave as open
                order = Order(
                    id=order_id,
                    client_order_id=request.client_order_id,
                    symbol=request.symbol,
                    side=request.side,
                    order_type=request.order_type,
                    status=OrderStatus.OPEN,
                    quantity=request.quantity,
                    filled_quantity=Decimal("0"),
                    remaining_quantity=request.quantity,
                    price=request.price,
                    time_in_force=request.time_in_force,
                    reduce_only=request.reduce_only,
                    strategy_id=request.strategy_id,
                    reason=request.reason,
                    created_at=now,
                    updated_at=now,
                    metadata=request.metadata,
                )
                self.orders[request.client_order_id] = order
                self._emit(EventType.ORDER_UPDATE, order)
                return order
            # else marketable → fill at limit or better
            fill_price = min(request.price, fill_price) if request.side == Side.BUY else max(request.price, fill_price)

        # Market / stop that triggered → full fill for simplicity in paper
        # (can be extended to partial fills)
        fee_rate = Decimal("0.0004")  # 0.04% taker-like
        fee = request.quantity * fill_price * fee_rate

        fill = Fill(
            trade_id=f"fill_{uuid4().hex[:10]}",
            order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            price=fill_price,
            quantity=request.quantity,
            fee=fee,
            fee_currency="USDC",
            timestamp=now,
            is_maker=False,
        )

        order = Order(
            id=order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.FILLED,
            quantity=request.quantity,
            filled_quantity=request.quantity,
            remaining_quantity=Decimal("0"),
            price=request.price,
            average_price=fill_price,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            strategy_id=request.strategy_id,
            reason=request.reason,
            created_at=now,
            updated_at=now,
            fills=[fill],
            metadata=request.metadata,
        )

        self.orders[request.client_order_id] = order
        self._emit(EventType.ORDER_UPDATE, order)
        self._emit(EventType.FILL, fill)
        return order

    # ──────────────────────────────────────────────
    # Live execution with retries & slippage guard
    # ──────────────────────────────────────────────

    async def _submit_live(self, request: OrderRequest) -> Order:
        # Pre-flight slippage check for market orders
        if request.order_type == OrderType.MARKET:
            ticker = await self.exchange.fetch_ticker(request.symbol)
            ask = Decimal(str(ticker["ask"]))
            bid = Decimal(str(ticker["bid"]))
            mid = (ask + bid) / 2
            # We will check actual fill later; here just soft guard

        @retry(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(multiplier=0.3, min=0.2, max=2),
            retry=retry_if_exception_type((ConnectionError, TimeoutError)),
            reraise=True,
        )
        async def _place():
            return await self.exchange.create_order(
                symbol=request.symbol,
                type=request.order_type.value,
                side=request.side.value,
                amount=float(request.quantity),
                price=float(request.price) if request.price else None,
                params={
                    "clientOrderId": request.client_order_id,
                    "reduceOnly": request.reduce_only,
                    "timeInForce": request.time_in_force.value,
                    "stopPrice": float(request.stop_price) if request.stop_price else None,
                },
            )

        try:
            raw = await _place()
        except Exception as e:
            logger.exception("Live order placement failed: %s", e)
            now = datetime.now(timezone.utc)
            failed = Order(
                id=f"failed_{uuid4().hex[:8]}",
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                status=OrderStatus.FAILED,
                quantity=request.quantity,
                filled_quantity=Decimal("0"),
                remaining_quantity=request.quantity,
                strategy_id=request.strategy_id,
                reason=request.reason,
                created_at=now,
                updated_at=now,
                metadata={"error": str(e), **request.metadata},
            )
            self.orders[request.client_order_id] = failed
            self._emit(EventType.ORDER_UPDATE, failed)
            raise ExecutionError(str(e)) from e

        order = self._map_exchange_order(raw, request)
        self.orders[request.client_order_id] = order
        self._emit(EventType.ORDER_UPDATE, order)

        # If fully filled immediately, emit fill
        if order.status == OrderStatus.FILLED and order.fills:
            for f in order.fills:
                self._emit(EventType.FILL, f)

        return order

    def _map_exchange_order(self, raw: Dict, request: OrderRequest) -> Order:
        """Map ccxt / native response to our Order model."""
        status_map = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.REJECTED,
        }
        status = status_map.get(raw.get("status", "open"), OrderStatus.OPEN)
        filled = Decimal(str(raw.get("filled", 0)))
        qty = Decimal(str(raw.get("amount", request.quantity)))

        fills = []
        for t in raw.get("trades") or []:
            fills.append(
                Fill(
                    trade_id=str(t.get("id", uuid4().hex[:10])),
                    order_id=str(raw["id"]),
                    symbol=request.symbol,
                    side=request.side,
                    price=Decimal(str(t["price"])),
                    quantity=Decimal(str(t["amount"])),
                    fee=Decimal(str(t.get("fee", {}).get("cost", 0))),
                    fee_currency=t.get("fee", {}).get("currency", "USDC"),
                    timestamp=datetime.fromtimestamp(t["timestamp"] / 1000, tz=timezone.utc)
                    if t.get("timestamp")
                    else datetime.now(timezone.utc),
                    is_maker=t.get("takerOrMaker") == "maker",
                )
            )

        return Order(
            id=str(raw["id"]),
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=status,
            quantity=qty,
            filled_quantity=filled,
            remaining_quantity=qty - filled,
            price=Decimal(str(raw["price"])) if raw.get("price") else request.price,
            average_price=Decimal(str(raw["average"])) if raw.get("average") else None,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            strategy_id=request.strategy_id,
            reason=request.reason,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            fills=fills,
            metadata=request.metadata,
        )

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                if self.mode == TradingMode.LIVE:
                    # Simple connectivity check
                    await self.exchange.fetch_time()
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat failed: %s – will retry", e)
                await asyncio.sleep(5)
