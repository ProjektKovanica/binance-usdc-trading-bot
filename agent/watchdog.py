"""Health watchdog for the trading stack."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class HealthReport:
    ok: bool
    checks: Dict[str, bool] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)
    ts: str = ""


class HealthWatchdog:
    def __init__(self):
        self.last_candle_ts: float = 0.0
        self.last_status_write: float = 0.0
        self.errors: List[str] = []
        self.started_at = time.time()

    def mark_candle(self) -> None:
        self.last_candle_ts = time.time()

    def mark_status(self) -> None:
        self.last_status_write = time.time()

    def note_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.errors = self.errors[-20:]
        logger.warning("Watchdog error noted: %s", msg)

    def report(self) -> HealthReport:
        now = time.time()
        checks = {
            "candle_fresh": (now - self.last_candle_ts) < 120 if self.last_candle_ts else False,
            "status_fresh": (now - self.last_status_write) < 30 if self.last_status_write else False,
            "uptime_ok": (now - self.started_at) > 5,
            "error_storm": len(self.errors) < 10,
        }
        messages = []
        if not checks["candle_fresh"]:
            messages.append("No fresh candles in >120s")
        if not checks["status_fresh"]:
            messages.append("Status file not updating")
        if not checks["error_storm"]:
            messages.append("Elevated error rate")
        ok = all(checks.values()) if self.last_candle_ts else checks["uptime_ok"]
        return HealthReport(
            ok=ok,
            checks=checks,
            messages=messages,
            ts=datetime.now(timezone.utc).isoformat(),
        )

    def as_dict(self) -> Dict[str, Any]:
        r = self.report()
        return {
            "ok": r.ok,
            "checks": r.checks,
            "messages": r.messages,
            "ts": r.ts,
            "recent_errors": self.errors[-5:],
        }
