#!/usr/bin/env python3
"""
Convenience launcher for paper trading mode.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import TradingEngine
from core.config import load_settings


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = load_settings(ROOT / "config" / "settings.yaml")
    settings.mode = "paper"  # force paper

    engine = TradingEngine(settings)
    try:
        await engine.start()
        print("\n✅ Paper trading engine running. Press Ctrl+C to stop.\n")
        while True:
            await asyncio.sleep(60)
            snap = engine.risk.get_snapshot()
            print(
                f"Equity: {snap['equity']:.2f} | "
                f"Daily PnL: {snap['daily_pnl']:.2f} | "
                f"Positions: {snap['open_positions']} | "
                f"Kill: {snap['kill_switch']}"
            )
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
