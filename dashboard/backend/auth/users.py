from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DB_PATH = Path(__file__).resolve().parents[3] / "config" / "users.db"

DEFAULT_RISK = {
    "max_daily_loss_usdc": 150.0,
    "max_position_pct_equity": 0.08,
    "max_leverage": 3.0,
    "max_open_positions": 6,
    "max_drawdown_pct": 0.0,
    "risk_utilization_pct": 0.0,
    "kill_switch": False,
    "kill_reason": "",
}


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                paper_equity REAL NOT NULL DEFAULT 10000,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS user_paper_state (
                user_id INTEGER PRIMARY KEY,
                starting_equity REAL NOT NULL DEFAULT 10000,
                equity REAL NOT NULL DEFAULT 10000,
                available_balance REAL NOT NULL DEFAULT 10000,
                daily_pnl REAL NOT NULL DEFAULT 0,
                weekly_pnl REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                used_margin REAL NOT NULL DEFAULT 0,
                risk_json TEXT NOT NULL DEFAULT '{}',
                positions_json TEXT NOT NULL DEFAULT '[]',
                orders_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        c.commit()


def create_user(email: str, password: str) -> Dict[str, Any]:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Invalid email")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    h = pwd_context.hash(password)
    with _conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO users (email, password_hash, paper_equity) VALUES (?, ?, 10000)",
                (email, h),
            )
            c.commit()
            uid = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError("Email already registered")
        risk = dict(DEFAULT_RISK)
        c.execute(
            """
            INSERT INTO user_paper_state (
                user_id, starting_equity, equity, available_balance, risk_json
            ) VALUES (?, 10000, 10000, 10000, ?)
            """,
            (uid, json.dumps(risk)),
        )
        c.commit()
    return {"id": uid, "email": email, "paper_equity": 10000.0}


def authenticate_user(email: str, password: str) -> Optional[Dict[str, Any]]:
    email = email.strip().lower()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)
        ).fetchone()
    if not row:
        return None
    if not pwd_context.verify(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "paper_equity": row["paper_equity"],
    }


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT id, email, paper_equity, created_at FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT id, email, paper_equity, created_at FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


def _ensure_state(user_id: int) -> None:
    with _conn() as c:
        row = c.execute(
            "SELECT user_id FROM user_paper_state WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            c.execute(
                """
                INSERT INTO user_paper_state (
                    user_id, starting_equity, equity, available_balance, risk_json
                ) VALUES (?, 10000, 10000, 10000, ?)
                """,
                (user_id, json.dumps(DEFAULT_RISK)),
            )
            c.commit()


def get_paper_state(user_id: int) -> Dict[str, Any]:
    _ensure_state(user_id)
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM user_paper_state WHERE user_id = ?", (user_id,)
        ).fetchone()
    risk = DEFAULT_RISK.copy()
    try:
        risk.update(json.loads(row["risk_json"] or "{}"))
    except Exception:
        pass
    positions = []
    orders = []
    try:
        positions = json.loads(row["positions_json"] or "[]")
        orders = json.loads(row["orders_json"] or "[]")
    except Exception:
        pass
    return {
        "equity": row["equity"],
        "starting_equity": row["starting_equity"],
        "available_balance": row["available_balance"],
        "daily_pnl": row["daily_pnl"],
        "weekly_pnl": row["weekly_pnl"],
        "unrealized_pnl": row["unrealized_pnl"],
        "realized_pnl": row["realized_pnl"],
        "used_margin": row["used_margin"],
        "mode": "paper",
        "risk": risk,
        "open_positions": positions,
        "open_orders": orders,
        "last_update": row["updated_at"],
    }


def update_paper_risk(user_id: int, risk_patch: Dict[str, Any]) -> Dict[str, Any]:
    state = get_paper_state(user_id)
    risk = state["risk"]
    for k, v in risk_patch.items():
        if v is not None and k in risk or k in (
            "max_daily_loss_usdc",
            "max_position_pct_equity",
            "max_leverage",
            "max_open_positions",
            "kill_switch",
            "kill_reason",
        ):
            risk[k] = v
    with _conn() as c:
        c.execute(
            """
            UPDATE user_paper_state
            SET risk_json = ?, updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (json.dumps(risk), user_id),
        )
        c.execute(
            "UPDATE users SET paper_equity = ? WHERE id = ?",
            (state["equity"], user_id),
        )
        c.commit()
    return get_paper_state(user_id)


def set_kill_switch(user_id: int, active: bool, reason: str = "") -> Dict[str, Any]:
    return update_paper_risk(
        user_id,
        {
            "kill_switch": active,
            "kill_reason": reason if active else "",
        },
    )


def reset_paper_account(user_id: int, equity: float = 10000.0) -> Dict[str, Any]:
    risk = dict(DEFAULT_RISK)
    with _conn() as c:
        c.execute(
            """
            UPDATE user_paper_state SET
                starting_equity = ?, equity = ?, available_balance = ?,
                daily_pnl = 0, weekly_pnl = 0, unrealized_pnl = 0, realized_pnl = 0,
                used_margin = 0, risk_json = ?, positions_json = '[]', orders_json = '[]',
                updated_at = datetime('now')
            WHERE user_id = ?
            """,
            (equity, equity, equity, json.dumps(risk), user_id),
        )
        c.execute("UPDATE users SET paper_equity = ? WHERE id = ?", (equity, user_id))
        c.commit()
    return get_paper_state(user_id)
