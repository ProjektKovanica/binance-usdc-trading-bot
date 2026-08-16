"""
Multi-channel alerting: Telegram, Discord, console.
Priority: info / warning / critical.
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class AlertPriority(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertManager:
    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        discord_webhook: str = "",
        enabled: bool = True,
    ):
        self.telegram_token = (telegram_token or "").strip()
        self.telegram_chat_id = str(telegram_chat_id or "").strip()
        self.discord_webhook = (discord_webhook or "").strip()
        self.enabled = enabled
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def send(
        self,
        message: str,
        priority: AlertPriority = AlertPriority.INFO,
        title: str = "Trading Bot",
    ) -> None:
        if not self.enabled:
            return

        prefix = {
            AlertPriority.INFO: "ℹ️",
            AlertPriority.WARNING: "⚠️",
            AlertPriority.CRITICAL: "🚨",
        }.get(priority, "")

        full = f"{prefix} <b>{_esc(title)}</b>\n{_esc(message)}"

        log_fn = (
            logger.critical
            if priority == AlertPriority.CRITICAL
            else logger.warning
            if priority == AlertPriority.WARNING
            else logger.info
        )
        log_fn("ALERT [%s] %s", priority.value, message)

        tasks = []
        if self.telegram_token and self.telegram_chat_id:
            tasks.append(self._send_telegram(full))
        if self.discord_webhook:
            tasks.append(self._send_discord(f"{prefix} **{title}**\n{message}", priority))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def trade(
        self,
        side: str,
        symbol: str,
        qty: str,
        price: str,
        pnl: str = "",
        mode: str = "paper",
    ) -> None:
        msg = f"{side.upper()} {symbol}\nQty: {qty} @ {price}"
        if pnl:
            msg += f"\nPnL: {pnl}"
        msg += f"\nMode: {mode}"
        await self.send(msg, AlertPriority.INFO, title="Trade")

    async def _send_telegram(self, text: str) -> None:
        if not self._session:
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with self._session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    # fallback plain
                    payload.pop("parse_mode", None)
                    async with self._session.post(url, json=payload, timeout=10) as resp2:
                        if resp2.status != 200:
                            logger.warning("Telegram alert failed: %s", await resp2.text())
        except Exception as e:
            logger.warning("Telegram send error: %s", e)

    async def _send_discord(self, text: str, priority: AlertPriority) -> None:
        if not self._session:
            return
        color = {
            AlertPriority.INFO: 0x3498DB,
            AlertPriority.WARNING: 0xF1C40F,
            AlertPriority.CRITICAL: 0xE74C3C,
        }.get(priority, 0x95A5A6)
        payload = {
            "embeds": [
                {
                    "title": "Trading Bot Alert",
                    "description": text[:4000],
                    "color": color,
                }
            ]
        }
        try:
            async with self._session.post(self.discord_webhook, json=payload, timeout=10) as resp:
                if resp.status not in (200, 204):
                    logger.warning("Discord alert failed: %s", await resp.text())
        except Exception as e:
            logger.warning("Discord send error: %s", e)


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
