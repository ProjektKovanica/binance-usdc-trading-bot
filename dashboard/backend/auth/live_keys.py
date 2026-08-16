from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from dashboard.backend.auth.crypto_keys import encrypt_secret, decrypt_secret, mask_key

DB_PATH = Path(__file__).resolve().parents[3] / "config" / "users.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_live_tables() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS user_exchange_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'binance',
                api_key_enc TEXT NOT NULL,
                api_secret_enc TEXT NOT NULL,
                label TEXT DEFAULT 'default',
                can_trade INTEGER NOT NULL DEFAULT 1,
                can_withdraw INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, exchange, label),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS live_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'queued',
                result_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        # billing columns on users (safe alter)
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "stripe_customer_id" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
        if "plan" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'")
        if "live_enabled" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN live_enabled INTEGER DEFAULT 0")
        c.commit()


def save_keys(
    user_id: int,
    api_key: str,
    api_secret: str,
    exchange: str = "binance",
    label: str = "default",
) -> Dict[str, Any]:
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if len(api_key) < 10 or len(api_secret) < 10:
        raise ValueError("API key/secret too short")
    with _conn() as c:
        c.execute(
            """
            INSERT INTO user_exchange_keys (user_id, exchange, api_key_enc, api_secret_enc, label)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, exchange, label) DO UPDATE SET
                api_key_enc = excluded.api_key_enc,
                api_secret_enc = excluded.api_secret_enc,
                is_active = 1,
                created_at = datetime('now')
            """,
            (user_id, exchange, encrypt_secret(api_key), encrypt_secret(api_secret), label),
        )
        c.commit()
    return {"ok": True, "exchange": exchange, "label": label, "api_key_masked": mask_key(api_key)}


def list_keys(user_id: int) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, exchange, label, api_key_enc, can_trade, can_withdraw, is_active, created_at
            FROM user_exchange_keys WHERE user_id = ? AND is_active = 1
            """,
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            plain = decrypt_secret(r["api_key_enc"])
            masked = mask_key(plain)
        except Exception:
            masked = "****"
        out.append(
            {
                "id": r["id"],
                "exchange": r["exchange"],
                "label": r["label"],
                "api_key_masked": masked,
                "can_trade": bool(r["can_trade"]),
                "can_withdraw": bool(r["can_withdraw"]),
                "created_at": r["created_at"],
            }
        )
    return out


def delete_keys(user_id: int, key_id: int) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE user_exchange_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        c.commit()
        return cur.rowcount > 0


def get_decrypted_keys(user_id: int, exchange: str = "binance") -> Optional[Dict[str, str]]:
    with _conn() as c:
        row = c.execute(
            """
            SELECT api_key_enc, api_secret_enc FROM user_exchange_keys
            WHERE user_id = ? AND exchange = ? AND is_active = 1
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, exchange),
        ).fetchone()
    if not row:
        return None
    return {
        "api_key": decrypt_secret(row["api_key_enc"]),
        "api_secret": decrypt_secret(row["api_secret_enc"]),
    }


def enqueue_job(user_id: int, job_type: str, payload: Dict[str, Any]) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO live_jobs (user_id, job_type, payload_json, status) VALUES (?, ?, ?, 'queued')",
            (user_id, job_type, json.dumps(payload)),
        )
        c.commit()
        return int(cur.lastrowid)


def list_jobs(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, job_type, status, payload_json, result_json, created_at, updated_at
            FROM live_jobs WHERE user_id = ? ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def set_user_plan(user_id: int, plan: str, stripe_customer_id: Optional[str] = None) -> None:
    with _conn() as c:
        if stripe_customer_id:
            c.execute(
                "UPDATE users SET plan = ?, stripe_customer_id = ? WHERE id = ?",
                (plan, stripe_customer_id, user_id),
            )
        else:
            c.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))
        c.commit()


def get_user_billing(user_id: int) -> Dict[str, Any]:
    with _conn() as c:
        row = c.execute(
            "SELECT id, email, plan, stripe_customer_id, live_enabled FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return {}
    return {
        "user_id": row["id"],
        "email": row["email"],
        "plan": row["plan"] or "free",
        "stripe_customer_id": row["stripe_customer_id"],
        "live_enabled": bool(row["live_enabled"] or 0),
    }


def set_live_enabled(user_id: int, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE users SET live_enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        c.commit()
