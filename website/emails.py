"""Transactional email for Bravo LEDs orders.

Config via env vars:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM

If SMTP_HOST is unset, sending is skipped gracefully (logged, never raises),
so the store works in dev without an SMTP server.

Notification structure: emails are sent through per-channel sender functions
so a future SMS channel can hook in at the same dispatch points without
touching the order logic. See send_email_channel(); an SMS channel would add
a parallel send_sms_channel() and be called from the same notify_*()
functions.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

import db

log = logging.getLogger(__name__)

# Order-notification address (owner gets a ping for every paid order).
# Resolution order: /admin/settings value -> ORDER_NOTIFY_EMAIL env var ->
# the test address below. The settings page is the no-code way to change it.
DEFAULT_ORDER_NOTIFY_EMAIL = "zoltar.works@gmail.com"


def order_notify_email():
    return (db.get_setting("email_owner_orders", "").strip()
            or os.environ.get("ORDER_NOTIFY_EMAIL")
            or DEFAULT_ORDER_NOTIFY_EMAIL)


def owner_alerts_email():
    return (db.get_setting("email_owner_alerts", "").strip()
            or os.environ.get("ORDER_NOTIFY_EMAIL")
            or DEFAULT_ORDER_NOTIFY_EMAIL)


def notifications_on(toggle_key):
    """Every send path checks its toggle first — notification behavior is
    fully controlled from /admin/settings, never code."""
    return db.notifications_enabled(toggle_key)


def smtp_configured():
    return bool(os.environ.get("SMTP_HOST"))


def _smtp_settings():
    return {
        "host": os.environ.get("SMTP_HOST"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASS", ""),
        "from_addr": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", ""),
    }


def send_email_channel(to_addr, subject, body_text):
    """Email channel sender. Returns True if sent, False if skipped/failed.

    Never raises: an unconfigured or failing SMTP must not break checkout,
    webhooks, or admin actions.
    """
    if not to_addr:
        log.info("email skipped: no recipient")
        return False
    if not smtp_configured():
        log.info("email skipped (SMTP_HOST unset): to=%s subject=%s",
                 to_addr, subject)
        return False
    cfg = _smtp_settings()
    msg = EmailMessage()
    msg["From"] = cfg["from_addr"] or cfg["user"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)
    try:
        if cfg["port"] == 465:
            smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
        with smtp:
            if cfg["port"] != 465:
                smtp.starttls()
            if cfg["user"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        log.info("email sent: to=%s subject=%s", to_addr, subject)
        return True
    except Exception as exc:  # noqa: BLE001 - must never break the request
        log.warning("email failed: to=%s subject=%s err=%s",
                    to_addr, subject, exc)
        return False


# ---------------------------------------------------------------- notifications
def _fmt_money(cents):
    return f"${cents / 100:,.2f}"


def _items_text(order):
    lines = []
    groups, order_seen = {}, []
    for i in order["line_items"]:
        v = i.get("vehicle") or ""
        if v not in groups:
            groups[v] = []
            order_seen.append(v)
        groups[v].append(i)
    for v in ([""] if "" in groups else []) + [x for x in order_seen if x]:
        if v:
            lines.append(f"{v}:")
        for i in groups[v]:
            variant = i.get("variation_label") or (
                f"{i.get('size', '')}"
                f"{', ' + i['color_temp'] if i.get('color_temp') else ''}"
            ).strip()
            lines.append(f"- {i['qty']}x {i['name']}"
                         f"{' (' + variant + ')' if variant else ''}"
                         f" — {_fmt_money(i['line_total_cents'])}")
    return "\n".join(lines)


def notify_customer_order_paid(order):
    """Order confirmation to the customer when payment completes."""
    if not notifications_on("notify_customer_order_confirmation"):
        log.info("suppressed by toggle: customer order confirmation")
        return False
    body = (
        f"Hi {order['customer_name'] or 'there'},\n\n"
        f"Thanks for your order from Bravo LEDs! Your payment was received.\n\n"
        f"Order #{order['id']}\n{_items_text(order)}\n"
        f"Total: {_fmt_money(order['total_cents'])}\n\n"
        f"We'll email you again when it ships (1-2 business days via USPS/UPS).\n\n"
        f"1-year warranty on all products. 30-day returns on unused items.\n"
        f"For off-road and fog light use only. Not DOT/SAE approved for "
        f"on-road use. Check your local laws.\n\n"
        f"— Bravo LEDs, Rosemead, CA"
    )
    return send_email_channel(order.get("customer_email"),
                              f"Bravo LEDs order #{order['id']} confirmed",
                              body)


def notify_customer_payment_failed(order):
    """Notice to the customer when their payment fails or is cancelled."""
    if not notifications_on("notify_customer_payment_failed"):
        log.info("suppressed by toggle: customer payment failed")
        return False
    body = (
        f"Hi {order['customer_name'] or 'there'},\n\n"
        f"We couldn't complete payment for your Bravo LEDs order "
        f"#{order['id']} ({_fmt_money(order['total_cents'])}).\n\n"
        f"No charge was made. You can try again from your cart, or reply "
        f"to this email and we'll help you complete the order.\n\n"
        f"— Bravo LEDs, Rosemead, CA"
    )
    return send_email_channel(order.get("customer_email"),
                              f"Bravo LEDs order #{order['id']}: "
                              f"payment didn't go through",
                              body)


def notify_customer_shipped(order, carrier=None, tracking_url=None):
    """'Shipped' email, including the tracking number when set."""
    if not notifications_on("notify_customer_shipped"):
        log.info("suppressed by toggle: customer shipped")
        return False
    tracking = order.get("tracking_number") or "not yet assigned"
    track_line = f"Tracking number: {tracking}\n"
    if carrier and carrier != "Unknown":
        track_line = f"Carrier: {carrier}\nTracking number: {tracking}\n"
    if tracking_url:
        track_line += f"Track your package: {tracking_url}\n"
    body = (
        f"Hi {order['customer_name'] or 'there'},\n\n"
        f"Your Bravo LEDs order #{order['id']} has shipped!\n\n"
        f"{track_line}"
        f"{_items_text(order)}\n"
        f"Total: {_fmt_money(order['total_cents'])}\n\n"
        f"Questions? Reply to this email.\n\n"
        f"— Bravo LEDs, Rosemead, CA"
    )
    return send_email_channel(order.get("customer_email"),
                              f"Bravo LEDs order #{order['id']} shipped",
                              body)


def notify_owner_new_order(order):
    """Owner ping for every paid order -> owner orders address."""
    if not notifications_on("notify_owner_new_order"):
        log.info("suppressed by toggle: owner new order")
        return False
    to_addr = order_notify_email()
    body = (
        f"New paid order #{order['id']} — {_fmt_money(order['total_cents'])}\n\n"
        f"Customer: {order['customer_name']} <{order['customer_email']}>\n"
        f"Ship to: {order['addr_line1']} {order['addr_line2']}, "
        f"{order['addr_city']}, {order['addr_state']} {order['addr_zip']}\n"
        f"Stripe session: {order['stripe_session_id']}\n\n"
        f"{_items_text(order)}\n"
    )
    return send_email_channel(to_addr,
                              f"[Bravo LEDs] New order #{order['id']} "
                              f"({_fmt_money(order['total_cents'])})",
                              body)


def notify_owner_low_stock(items):
    """Owner alert when products/variations hit their low-stock threshold.

    items: list of (label, sku, qty)."""
    if not notifications_on("notify_owner_low_stock"):
        log.info("suppressed by toggle: owner low stock")
        return False
    if not items:
        return False
    lines = "\n".join(f"- {label} (SKU {sku}): {qty} left"
                      for label, sku, qty in items)
    body = (
        f"The following items are at or below their low-stock threshold:\n\n"
        f"{lines}\n\n"
        f"Restock in /admin -> Inventory.\n\n"
        f"— Bravo LEDs store"
    )
    return send_email_channel(owner_alerts_email(),
                              f"[Bravo LEDs] Low stock: {len(items)} item(s)",
                              body)


def notify_owner_contact_message(name, email, message):
    """Owner alert for a contact-form message."""
    if not notifications_on("notify_owner_contact_message"):
        log.info("suppressed by toggle: owner contact message")
        return False
    body = (
        f"New message from the Bravo LEDs contact form:\n\n"
        f"From: {name} <{email}>\n\n{message}\n"
    )
    return send_email_channel(owner_alerts_email(),
                              f"[Bravo LEDs] Contact form: {name}",
                              body)


def notify_owner_review_submitted(product_name, reviewer, rating, body_text):
    """Owner alert when a customer submits a product review."""
    if not notifications_on("notify_owner_review_submitted"):
        log.info("suppressed by toggle: owner review submitted")
        return False
    body = (
        f"A new product review is awaiting moderation:\n\n"
        f"Product: {product_name}\n"
        f"Reviewer: {reviewer} — {rating}/5 stars\n\n"
        f"{body_text}\n\n"
        f"Approve it in /admin -> Reviews.\n\n"
        f"— Bravo LEDs store"
    )
    return send_email_channel(
        owner_alerts_email(),
        f"[Bravo LEDs] New review for {product_name}",
        body)


# Future channels plug in here, e.g.:
# def send_sms_channel(to_number, text): ...
# and are called from the same notify_*() functions above.
