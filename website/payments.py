"""Stripe Checkout integration for Bravo LEDs.

Keys come ONLY from environment variables — never in the repo:
    STRIPE_SECRET_KEY        — secret key (sk_test_... in test mode)
    STRIPE_PUBLISHABLE_KEY   — publishable key (not used server-side, listed
                               for completeness; the frontend doesn't need it
                               because we redirect to Stripe's hosted page)
    STRIPE_WEBHOOK_SECRET    — signing secret for /stripe/webhook (whsec_...)

If STRIPE_SECRET_KEY is absent, payments are "not configured": checkout
degrades gracefully (clear message, no crash). Supports test mode — Stripe
automatically uses test mode when the keys are sk_test_/pk_test_.
"""

import logging
import os
import re

log = logging.getLogger(__name__)

try:
    import stripe
    _STRIPE_LIB = True
except ImportError:  # pragma: no cover - stripe is in requirements.txt
    stripe = None
    _STRIPE_LIB = False


def stripe_configured():
    """True only when the Stripe library is present AND a secret key is set."""
    return _STRIPE_LIB and bool(os.environ.get("STRIPE_SECRET_KEY"))


def _api():
    """Configure and return the stripe module. Raises if not configured."""
    if not _STRIPE_LIB:
        raise RuntimeError("stripe library not installed")
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key
    return stripe


def _coupon_for_promo(promo):
    """Map our promo code to a Stripe Coupon id for the Checkout Session.

    Test-mode-safe: returns None (no crash) whenever Stripe isn't
    configured — the caller still records the discount in local totals.
    Never raises: any Stripe-side failure is logged and the session is
    created without the Stripe-side coupon.

    Coupons are created once per code (idempotent by id). If the code's
    value changes later, the first-created coupon params stick — rename
    the code instead of reusing it (see README).
    """
    if not promo:
        return None
    try:
        s = _api()
    except Exception:
        return None
    cid = "promo_" + re.sub(r"[^a-z0-9]+", "-",
                            str(promo["code"]).lower()).strip("-")
    if not cid or cid == "promo_":
        return None
    try:
        if promo["kind"] == "percent":
            s.Coupon.create(id=cid, percent_off=int(promo["percent"]),
                            duration="once")
        else:
            s.Coupon.create(id=cid, amount_off=int(promo["amount_cents"]),
                            currency="usd", duration="once")
        return cid
    except Exception as exc:
        # Probably "already exists" — retrieve and reuse it.
        try:
            s.Coupon.retrieve(cid)
            return cid
        except Exception:
            log.warning("stripe coupon mapping failed for %s: %s",
                        promo.get("code"), exc)
            return None


def create_checkout_session(order_id, lines, customer_email, success_url,
                            cancel_url, promo=None, fulfillment="ship"):
    """Create a Stripe Checkout Session from the server-validated cart.

    lines: cart_detailed() output — prices re-looked-up server-side.
    promo: an optional validated promo-code dict (code/kind/percent/
    amount_cents). When present and Stripe is configured, it is attached
    as a real Stripe Coupon discount on the session. When Stripe is NOT
    configured, the coupon mapping is skipped silently and the local
    order snapshot still records the discount.
    fulfillment: 'ship' (default) collects a US shipping address at Stripe;
    'pickup' (local store pickup) skips address collection.
    Returns the Stripe Session object (has .id and .url).
    """
    s = _api()
    stripe_lines = []
    for l in lines:
        p = l["product"]
        unit = l.get("unit_price_cents", p["price_cents"])
        desc_parts = []
        if l["size"]:
            desc_parts.append(f"Size {l['size']}")
        if l["color_temp"]:
            desc_parts.append(f"{l['color_temp']}")
        desc_parts.append("1-year warranty")
        stripe_lines.append({
            "price_data": {
                "currency": "usd",
                "unit_amount": unit,
                "product_data": {
                    "name": p["name"],
                    "description": ", ".join(desc_parts),
                },
            },
            "quantity": l["qty"],
        })
    kwargs = {
        "mode": "payment",
        "line_items": stripe_lines,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"order_id": str(order_id), "store": "bravoleds"},
        "billing_address_collection": "required",
    }
    if fulfillment == "pickup":
        # Local pickup: no shipping address needed at Stripe. The pickup
        # choice is recorded on the order itself (metadata + local DB).
        kwargs["metadata"]["fulfillment"] = "pickup"
    else:
        kwargs["shipping_address_collection"] = {"allowed_countries": ["US"]}
    if customer_email:
        kwargs["customer_email"] = customer_email
    coupon_id = _coupon_for_promo(promo)
    if coupon_id:
        kwargs["discounts"] = [{"coupon": coupon_id}]
    return s.checkout.Session.create(**kwargs)


def refund_payment_intent(payment_intent_id, amount_cents=None, reason=None):
    """Issue a Stripe refund (full when amount_cents is None).

    Returns the refund dict. Raises RuntimeError when Stripe is not
    configured; any Stripe-side failure raises too — callers catch and
    surface a friendly message. Never partially records: the caller only
    writes to the DB after this returns.
    """
    s = _api()
    kwargs = {"payment_intent": payment_intent_id}
    if amount_cents is not None:
        kwargs["amount"] = max(1, int(amount_cents))
    if reason:
        kwargs["reason"] = reason  # requested_by_customer / duplicate / fraud
    refund = s.Refund.create(**kwargs)
    return refund.to_dict() if hasattr(refund, "to_dict") else dict(refund)


def verify_webhook(payload, sig_header):
    """Verify a Stripe webhook signature. Raises on any problem.

    Returns the parsed event as a plain dict. Fail closed: without the
    webhook secret there is no way to authenticate events, so verification
    always fails.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not _STRIPE_LIB:
        raise RuntimeError("stripe library not installed")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")
    if not sig_header:
        raise ValueError("missing Stripe-Signature header")
    event = stripe.Webhook.construct_event(payload, sig_header, secret)
    # Normalize StripeObject -> plain dict so callers can use .get()/[].
    return event.to_dict() if hasattr(event, "to_dict") else dict(event)
