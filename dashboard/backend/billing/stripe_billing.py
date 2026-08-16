"""Stripe billing + webhooks (Phase E)."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _load_env() -> None:
    """Ensure ~/trading-bot/.env is loaded (PM2 often does not export .env)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    candidates = [
        Path(__file__).resolve().parents[3] / ".env",
        Path("/root/trading-bot/.env"),
        Path.cwd() / ".env",
    ]
    for p in candidates:
        if p.is_file():
            load_dotenv(p, override=True)
            return


def _cfg() -> Dict[str, str]:
    _load_env()
    return {
        "secret": (os.getenv("STRIPE_SECRET_KEY") or "").strip().strip('"').strip("'"),
        "price": (os.getenv("STRIPE_PRICE_ID") or "").strip().strip('"').strip("'"),
        "webhook": (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip().strip('"').strip("'"),
        "public_url": (os.getenv("PUBLIC_URL") or "https://trader.kovanica.online").strip().strip('"').strip("'"),
    }


def stripe_configured() -> bool:
    c = _cfg()
    return bool(c["secret"] and c["price"] and c["secret"].startswith("sk_"))


def webhook_configured() -> bool:
    c = _cfg()
    return bool(c["secret"] and c["webhook"] and c["webhook"].startswith("whsec_"))


def create_checkout_session(
    customer_email: str,
    user_id: int,
    customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    c = _cfg()
    if not stripe_configured():
        return {
            "ok": False,
            "dry_run": True,
            "message": "Stripe not configured. Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID in .env",
            "checkout_url": None,
            "debug": {
                "has_secret": bool(c["secret"]),
                "has_price": bool(c["price"]),
                "secret_prefix": (c["secret"][:7] + "…") if c["secret"] else "",
                "price_prefix": (c["price"][:10] + "…") if c["price"] else "",
            },
        }

    import stripe

    stripe.api_key = c["secret"]
    params: Dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": c["price"], "quantity": 1}],
        "success_url": f"{c['public_url'].rstrip('/')}/?billing=success",
        "cancel_url": f"{c['public_url'].rstrip('/')}/?billing=cancel",
        "client_reference_id": str(user_id),
        "metadata": {"user_id": str(user_id)},
        "allow_promotion_codes": True,
        "subscription_data": {"metadata": {"user_id": str(user_id)}},
    }
    if customer_id:
        params["customer"] = customer_id
    else:
        params["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**params)
    return {"ok": True, "checkout_url": session.url, "session_id": session.id}


def construct_webhook_event(payload: bytes, sig_header: str):
    import stripe

    c = _cfg()
    stripe.api_key = c["secret"]
    if not c["webhook"]:
        raise ValueError("STRIPE_WEBHOOK_SECRET not set")
    return stripe.Webhook.construct_event(payload, sig_header, c["webhook"])


def _user_id_from_session(session: dict) -> Optional[int]:
    raw = session.get("client_reference_id") or (session.get("metadata") or {}).get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _user_id_from_subscription(sub: dict) -> Optional[int]:
    meta = sub.get("metadata") or {}
    if meta.get("user_id"):
        try:
            return int(meta["user_id"])
        except (TypeError, ValueError):
            pass
    return None


def _find_user_by_customer(customer_id: str) -> Optional[int]:
    import sqlite3

    db = Path(__file__).resolve().parents[3] / "config" / "users.db"
    if not db.exists():
        return None
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE stripe_customer_id = ? LIMIT 1",
            (customer_id,),
        ).fetchone()
    return int(row[0]) if row else None


def handle_webhook_event(event: dict) -> Dict[str, Any]:
    from dashboard.backend.auth.live_keys import set_user_plan, set_live_enabled

    etype = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    actions = []

    if etype == "checkout.session.completed":
        uid = _user_id_from_session(data)
        customer = data.get("customer")
        if uid:
            set_user_plan(uid, "pro", str(customer) if customer else None)
            set_live_enabled(uid, True)
            actions.append(f"user {uid} → pro (checkout completed)")

    elif etype == "customer.subscription.updated":
        uid = _user_id_from_subscription(data)
        status = data.get("status")
        customer = data.get("customer")
        if uid:
            if status in ("active", "trialing"):
                set_user_plan(uid, "pro", str(customer) if customer else None)
                set_live_enabled(uid, True)
                actions.append(f"user {uid} → pro (sub {status})")
            elif status in ("canceled", "unpaid", "incomplete_expired"):
                set_user_plan(uid, "free", str(customer) if customer else None)
                set_live_enabled(uid, False)
                actions.append(f"user {uid} → free (sub {status})")
            else:
                actions.append(f"user {uid} sub status={status}")

    elif etype == "customer.subscription.deleted":
        uid = _user_id_from_subscription(data)
        customer = data.get("customer")
        if not uid and customer:
            uid = _find_user_by_customer(str(customer))
        if uid:
            set_user_plan(uid, "free", str(customer) if customer else None)
            set_live_enabled(uid, False)
            actions.append(f"user {uid} → free (subscription deleted)")

    elif etype == "invoice.payment_failed":
        customer = data.get("customer")
        uid = _find_user_by_customer(str(customer)) if customer else None
        actions.append(f"payment_failed user={uid} customer={customer}")

    elif etype == "invoice.paid":
        customer = data.get("customer")
        uid = _find_user_by_customer(str(customer)) if customer else None
        if uid:
            set_user_plan(uid, "pro", str(customer) if customer else None)
            actions.append(f"user {uid} invoice paid → pro")

    else:
        actions.append(f"ignored event {etype}")

    logger.info("Stripe webhook %s: %s", etype, "; ".join(actions) or "noop")
    return {"ok": True, "type": etype, "actions": actions}


# Back-compat for webhook-info endpoint
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://trader.kovanica.online")
