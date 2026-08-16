"""
Interactive Telegram control bot with inline buttons (admin only).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "config" / "runtime_status.json"
CONTROL = ROOT / "config" / "runtime_control.json"
SETTINGS = ROOT / "config" / "settings.yaml"


def _keyboard(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": rows}


def _btn(text: str, data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": data[:64]}


MAIN_KB = _keyboard(
    [
        [_btn("Status", "status"), _btn("Equity", "equity"), _btn("Pos", "pos")],
        [_btn("Risk", "risk"), _btn("Pairs", "pairs"), _btn("Mode", "mode")],
        [_btn("Kill ON", "kill"), _btn("Kill OFF", "unkill")],
        [_btn("Pause", "pause"), _btn("Resume", "resume")],
        [_btn("Help", "help"), _btn("Ping", "ping")],
    ]
)


class TelegramControlBot:
    def __init__(
        self,
        token: str = "",
        admin_chat_id: str = "",
        poll_seconds: float = 2.0,
    ):
        self.token = (token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        self.admin_chat_id = str(admin_chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
        self.poll_seconds = poll_seconds
        self._session: Optional[aiohttp.ClientSession] = None
        self._offset = 0
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._started_at = time.time()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.admin_chat_id)

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Telegram control bot disabled (no token/chat)")
            return
        self._session = aiohttp.ClientSession()
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        await self.send("Telegram control online.\nTap buttons or /help", reply_markup=MAIN_KB)
        logger.info("Telegram control bot started (admin=%s)", self.admin_chat_id)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None

    async def send(
        self,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._session or not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.admin_chat_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            async with self._session.post(url, json=payload, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning("Telegram send failed: %s", await resp.text())
        except Exception as e:
            logger.warning("Telegram send error: %s", e)

    async def _answer_callback(self, callback_id: str, text: str = "") -> None:
        if not self._session:
            return
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        try:
            await self._session.post(
                url,
                json={"callback_query_id": callback_id, "text": text[:200]},
                timeout=10,
            )
        except Exception:
            pass

    async def _poll_loop(self) -> None:
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        while self._running:
            try:
                params = {"timeout": 25, "offset": self._offset}
                assert self._session
                async with self._session.get(url, params=params, timeout=35) as resp:
                    data = await resp.json()
                for upd in data.get("result") or []:
                    self._offset = max(self._offset, int(upd["update_id"]) + 1)
                    await self._handle_update(upd)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Telegram poll error: %s", e)
                await asyncio.sleep(3)
            await asyncio.sleep(0.2)

    async def _handle_update(self, upd: Dict[str, Any]) -> None:
        # Inline button press
        cq = upd.get("callback_query")
        if cq:
            chat = (cq.get("message") or {}).get("chat") or {}
            chat_id = str(chat.get("id", ""))
            data = (cq.get("data") or "").strip()
            cq_id = cq.get("id", "")
            if chat_id != self.admin_chat_id:
                await self._answer_callback(cq_id, "Unauthorized")
                return
            await self._answer_callback(cq_id, "OK")
            await self._dispatch_callback(data)
            return

        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if chat_id != self.admin_chat_id:
            logger.info("Ignored telegram from non-admin %s", chat_id)
            return
        await self._dispatch(text)

    async def _dispatch_callback(self, data: str) -> None:
        mapping = {
            "status": self._cmd_status,
            "equity": self._cmd_equity,
            "pos": self._cmd_positions,
            "risk": self._cmd_risk,
            "pairs": self._cmd_pairs,
            "mode": self._cmd_mode,
            "kill": lambda: self._cmd_kill("Telegram inline Kill"),
            "unkill": self._cmd_unkill,
            "pause": self._cmd_pause,
            "resume": self._cmd_resume,
            "help": self._cmd_help,
            "ping": self._cmd_ping,
        }
        fn = mapping.get(data)
        if fn:
            await fn()
        else:
            await self.send(f"Unknown button: {data}", reply_markup=MAIN_KB)

    async def _dispatch(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/start": self._cmd_help,
            "/help": self._cmd_help,
            "/menu": self._cmd_menu,
            "/status": self._cmd_status,
            "/equity": self._cmd_equity,
            "/pos": self._cmd_positions,
            "/positions": self._cmd_positions,
            "/risk": self._cmd_risk,
            "/pairs": self._cmd_pairs,
            "/mode": self._cmd_mode,
            "/ping": self._cmd_ping,
            "/whoami": self._cmd_whoami,
            "/control": self._cmd_control,
            "/kill": lambda: self._cmd_kill(arg),
            "/unkill": self._cmd_unkill,
            "/reset": self._cmd_unkill,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/score": lambda: self._cmd_score(arg),
            "/lev": lambda: self._cmd_set_risk("max_leverage", arg),
            "/daily": lambda: self._cmd_set_risk("max_daily_loss_usdc", arg),
        }
        fn = handlers.get(cmd)
        if fn is None:
            await self.send("Unknown. /help or /menu", reply_markup=MAIN_KB)
            return
        await fn()

    async def _cmd_menu(self) -> None:
        await self.send("Control panel:", reply_markup=MAIN_KB)

    async def _cmd_help(self) -> None:
        await self.send(
            "Commands + inline buttons (/menu):\n"
            "/status /equity /pos /risk /pairs /mode\n"
            "/kill [reason] /unkill\n"
            "/pause /resume\n"
            "/score [0.4-0.9] /lev [n] /daily [n]\n"
            "/ping /whoami /control /menu",
            reply_markup=MAIN_KB,
        )

    async def _cmd_status(self) -> None:
        await self.send(self._fmt_status(), reply_markup=MAIN_KB)

    async def _cmd_equity(self) -> None:
        st = self._read_status()
        await self.send(
            f"Equity: {st.get('equity', '?')}\n"
            f"Daily PnL: {st.get('daily_pnl', '?')}\n"
            f"Positions: {self._pos_count(st)}\n"
            f"Mode: {st.get('mode', '?')}",
            reply_markup=MAIN_KB,
        )

    async def _cmd_positions(self) -> None:
        st = self._read_status()
        positions = st.get("open_positions") or []
        if not positions:
            await self.send("No open positions.", reply_markup=MAIN_KB)
            return
        lines = ["Open positions:"]
        for p in positions[:20]:
            if isinstance(p, dict):
                lines.append(
                    f"• {p.get('symbol')} {p.get('side')} qty={p.get('quantity')} "
                    f"entry={p.get('entry_price')} uPnL={p.get('unrealized_pnl')}"
                )
            else:
                lines.append(f"• {p}")
        await self.send("\n".join(lines), reply_markup=MAIN_KB)

    async def _cmd_risk(self) -> None:
        st = self._read_status()
        risk = st.get("risk") or {}
        ctrl = self._read_control()
        await self.send(
            f"Kill: {risk.get('kill_switch', st.get('kill_switch', ctrl.get('kill_switch')))}\n"
            f"Paused: {ctrl.get('pause_entries', False)}\n"
            f"Daily loss limit: {risk.get('max_daily_loss_usdc', ctrl.get('max_daily_loss_usdc', '?'))}\n"
            f"Pos % equity: {risk.get('max_position_pct_equity', ctrl.get('max_position_pct_equity', '?'))}\n"
            f"Leverage: {risk.get('max_leverage', ctrl.get('max_leverage', '?'))}\n"
            f"Max positions: {risk.get('max_open_positions', ctrl.get('max_open_positions', '?'))}\n"
            f"Daily PnL: {st.get('daily_pnl', '?')}",
            reply_markup=MAIN_KB,
        )

    async def _cmd_pairs(self) -> None:
        st = self._read_status()
        syms = st.get("symbols") or []
        if not syms:
            try:
                import yaml

                data = yaml.safe_load(SETTINGS.read_text()) or {}
                syms = data.get("symbols") or []
            except Exception:
                syms = []
        await self.send(
            "Pairs:\n" + ("\n".join(f"• {s}" for s in syms) if syms else "unknown"),
            reply_markup=MAIN_KB,
        )

    async def _cmd_mode(self) -> None:
        st = self._read_status()
        await self.send(
            f"Mode: {st.get('mode', '?')}\nFeed: {st.get('data_feed', st.get('feed', '?'))}",
            reply_markup=MAIN_KB,
        )

    async def _cmd_ping(self) -> None:
        up = int(time.time() - self._started_at)
        st = self._read_status()
        age = str(st.get("last_update") or st.get("ts") or "?")
        await self.send(f"pong\nuptime_s={up}\nstatus_ts={age}", reply_markup=MAIN_KB)

    async def _cmd_whoami(self) -> None:
        await self.send(f"admin_chat_id={self.admin_chat_id}", reply_markup=MAIN_KB)

    async def _cmd_control(self) -> None:
        ctrl = self._read_control()
        await self.send(
            "runtime_control.json:\n" + json.dumps(ctrl, indent=2)[:3500],
            reply_markup=MAIN_KB,
        )

    async def _cmd_kill(self, reason: str) -> None:
        reason = reason or "Telegram /kill"
        self._write_control(
            {"kill_switch": True, "kill_reason": reason, "updated_by": "telegram"}
        )
        await self.send(f"Kill switch ON\n{reason}", reply_markup=MAIN_KB)

    async def _cmd_unkill(self) -> None:
        self._write_control(
            {"kill_switch": False, "kill_reason": "", "updated_by": "telegram"}
        )
        await self.send("Kill switch OFF", reply_markup=MAIN_KB)

    async def _cmd_pause(self) -> None:
        self._write_control({"pause_entries": True, "updated_by": "telegram"})
        await self.send("Pause entries ON", reply_markup=MAIN_KB)

    async def _cmd_resume(self) -> None:
        self._write_control({"pause_entries": False, "updated_by": "telegram"})
        await self.send("Pause entries OFF", reply_markup=MAIN_KB)

    async def _cmd_score(self, arg: str) -> None:
        cur = os.getenv("AGENT_MIN_SCORE", "0.55")
        if not arg:
            await self.send(
                f"AGENT_MIN_SCORE={cur}\nUsage: /score 0.60",
                reply_markup=MAIN_KB,
            )
            return
        try:
            v = float(arg)
            if not 0.2 <= v <= 0.95:
                await self.send("Score must be 0.20–0.95", reply_markup=MAIN_KB)
                return
            self._write_control({"agent_min_score": v, "updated_by": "telegram"})
            await self.send(f"agent_min_score={v} written to control file", reply_markup=MAIN_KB)
        except ValueError:
            await self.send("Usage: /score 0.55", reply_markup=MAIN_KB)

    async def _cmd_set_risk(self, key: str, arg: str) -> None:
        if not arg:
            ctrl = self._read_control()
            await self.send(f"{key}={ctrl.get(key, '?')}", reply_markup=MAIN_KB)
            return
        try:
            val = float(arg)
            self._write_control({key: val, "updated_by": "telegram"})
            await self.send(f"Set {key}={val}", reply_markup=MAIN_KB)
        except ValueError:
            await self.send("Need a number", reply_markup=MAIN_KB)

    def _pos_count(self, st: Dict[str, Any]) -> Any:
        if "open_positions_count" in st:
            return st["open_positions_count"]
        return len(st.get("open_positions") or [])

    def _fmt_status(self) -> str:
        st = self._read_status()
        if not st:
            return "No runtime_status.json yet."
        risk = st.get("risk") or {}
        ctrl = self._read_control()
        return (
            f"Mode: {st.get('mode', '?')}\n"
            f"Equity: {st.get('equity', '?')}\n"
            f"Daily PnL: {st.get('daily_pnl', '?')}\n"
            f"Positions: {self._pos_count(st)}\n"
            f"Kill: {risk.get('kill_switch', st.get('kill_switch', ctrl.get('kill_switch')))}\n"
            f"Paused: {ctrl.get('pause_entries', False)}\n"
            f"Lev: {risk.get('max_leverage', ctrl.get('max_leverage', '?'))}\n"
            f"Updated: {st.get('last_update', st.get('ts', '?'))}"
        )

    def _read_status(self) -> Dict[str, Any]:
        try:
            if STATUS.exists():
                return json.loads(STATUS.read_text())
        except Exception:
            pass
        return {}

    def _read_control(self) -> Dict[str, Any]:
        try:
            if CONTROL.exists():
                return json.loads(CONTROL.read_text())
        except Exception:
            pass
        return {}

    def _write_control(self, patch: Dict[str, Any]) -> None:
        data = self._read_control()
        data.update(patch)
        CONTROL.parent.mkdir(parents=True, exist_ok=True)
        CONTROL.write_text(json.dumps(data, indent=2))


async def run_telegram_bot_forever() -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    bot = TelegramControlBot()
    await bot.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await bot.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_telegram_bot_forever())
