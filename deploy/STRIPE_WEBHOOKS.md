# Stripe webhooks — trader.kovanica.online

## 1. Env on VPS (`~/trading-bot/.env`)

```env
STRIPE_SECRET_KEY=sk_live_...          # or sk_test_...
STRIPE_PRICE_ID=price_...              # Pro monthly price id
STRIPE_WEBHOOK_SECRET=whsec_...        # from Stripe webhook endpoint
PUBLIC_URL=https://trader.kovanica.online
MASTER_SECRET=...                      # already set for API key encryption
```

```bash
pm2 restart dashboard-api --update-env
```

## 2. Stripe Dashboard

1. Developers → **Webhooks** → Add endpoint  
2. URL: `https://trader.kovanica.online/api/billing/webhook`  
3. Events:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.paid`
   - `invoice.payment_failed`
4. Copy **Signing secret** → `STRIPE_WEBHOOK_SECRET`

## 3. Nginx

`/api/` must proxy to port 8000 (already in trader nginx conf).  
Do **not** buffer/alter body for this path if possible (default proxy_pass is fine).

## 4. Test

```bash
# Status
curl -s https://trader.kovanica.online/api/billing/webhook-info

# Stripe CLI (local/dev)
stripe listen --forward-to https://trader.kovanica.online/api/billing/webhook
stripe trigger checkout.session.completed
```

## 5. Behaviour

| Event | Action |
|-------|--------|
| checkout.session.completed | plan=pro, live_enabled=1, store customer id |
| subscription.updated active/trialing | plan=pro |
| subscription.updated canceled/unpaid | plan=free, live_enabled=0 |
| subscription.deleted | plan=free |
| invoice.paid | plan=pro |
| invoice.payment_failed | logged only |

## 6. Checkout

Logged-in user → UI **Upgrade to Pro** → `POST /api/billing/checkout` → Stripe hosted page.
