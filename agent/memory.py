"""Trade / decision memory for the agent."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional


@dataclass
class MemoryEvent:
    ts: str
    kind: str  # signal | trade | regime | decision | note
    symbol: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


class TradeMemory:
    def __init__(self, path: Optional[Path] = None, maxlen: int = 500):
        self.path = path or Path(__file__).resolve().parents[1] / "config" / "agent_memory.json"
        self.events: Deque[MemoryEvent] = deque(maxlen=maxlen)
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for e in raw.get("events", [])[-500:]:
                    self.events.append(MemoryEvent(**e))
            except Exception:
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"events": [asdict(e) for e in self.events], "updated_at": datetime.now(timezone.utc).isoformat()}
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, kind: str, symbol: str = "", **payload) -> None:
        self.events.append(
            MemoryEvent(
                ts=datetime.now(timezone.utc).isoformat(),
                kind=kind,
                symbol=symbol,
                payload=payload,
            )
        )
        # persist periodically every 10 events
        if len(self.events) % 10 == 0:
            self.save()

    def recent(self, n: int = 20, kind: Optional[str] = None) -> List[MemoryEvent]:
        items = list(self.events)
        if kind:
            items = [e for e in items if e.kind == kind]
        return items[-n:]

    def summary(self) -> Dict[str, Any]:
        trades = [e for e in self.events if e.kind == "trade"]
        wins = sum(1 for e in trades if float(e.payload.get("net_pnl", 0)) > 0)
        total = len(trades)
        return {
            "events": len(self.events),
            "trades": total,
            "wins": wins,
            "win_rate": (wins / total * 100) if total else 0.0,
            "last_decision": asdict(self.events[-1]) if self.events else None,
        }
