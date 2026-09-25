#!/usr/bin/env python3
"""Phase 2 tests: Stripe checkout, webhook signature + idempotency, order
lifecycle, inventory decrement, admin auth, email graceful-skip.

No network calls: Stripe session creation is faked, webhook signatures are
computed locally (HMAC, same scheme Stripe uses), and SMTP is left
unconfigured so sending must be skipped gracefully.

Run:  cd website && .venv/bin/python test_phase2.py
"""

import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import time

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="p2_"),
                                      "store.db")
# Scrub anything that could make payments/email "configured" by accident.
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import emails      # noqa: E402
import payments    # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


_MISSING = object()


class Env:
    """Temporarily set/unset env vars, restoring afterwards."""
    def __init__(self, **kw):
        self.kw = kw
        self.saved = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.saved[k] = os.environ.get(k, _MISSING)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.kw.items():
            if self.saved[k] is _MISSING:
                os.environ.pop(k, None)
            else:
                os.environ[k] = self.saved[k]


def add_to_cart(client, qty=2):
    p = db.list_products()[0]
    vid = p["variations"][0]["id"]
    client.post("/cart/add", data={"product_id": p["id"],
                                   "variation_id": str(vid),
                                   "qty": str(qty)})
    return p


def make_order(client, qty=2):
    """Cart -> order row with a fake stripe session id. Returns (order, product)."""
    p = add_to_cart(client, qty)
    with client.session_transaction() as s:
        cart = dict(s.get("cart", {}))
    # Rebuild cart_detailed-equivalent lines directly from db for the order.
    key, item = next(iter(cart.items()))
    prod = db.get_product(item["product_id"])
    var = next(v for v in prod["variations"]
               if v["id"] == item["variation_id"])
    unit = db.variation_sell_price(prod, var)
    lines = [{"key": key, "product": prod, "variation": var,
              "variation_id": var["id"], "variation_label": var["label"],
              "size": var["option_values"].get("Size", ""),
              "color_temp": var["option_values"].get("Color temp", ""),
              "qty": item["qty"], "unit_price_cents": unit,
              "line_total": unit * item["qty"]}]
    subtotal = lines[0]["line_total"]
    oid = db.create_order({"name": "Test Buyer", "email": "buyer@example.com",
                           "line1": "1 Test St", "city": "Rosemead",
                           "state": "CA", "zip": "91770"},
                          lines, subtotal)
    db.set_stripe_session(oid, f"cs_test_{oid}")
    return db.get_order(oid), prod


def stripe_sig(payload, secret):
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def webhook_payload(session_id):
    return json.dumps({
        "id": "evt_test_1",
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id,
                            "payment_intent": "pi_test_1"}},
    }).encode()


# ---------------------------------------------------------------- 1. checkout
def test_checkout_no_keys():
    print("1 — checkout degrades gracefully without Stripe keys")
    client = appmod.app.test_client()
    check("payments not configured", not payments.stripe_configured())
    add_to_cart(client)
    r = client.get("/checkout")
    body = r.get_data(as_text=True)
    check("GET /checkout -> 200", r.status_code == 200)
    check("shows 'not turned on' message",
          "aren't turned on" in body
          and 'action="/checkout/create"' not in body)
    r = client.post("/checkout/create",
                    data={"name": "X", "email": "x@y.z"})
    body = r.get_data(as_text=True)
    check("POST /checkout/create -> 200, no crash, no redirect",
          r.status_code == 200 and "not configured" in body)


def test_checkout_create_mocked():
    print("2 — checkout session creation (mocked Stripe)")
    real = payments.create_checkout_session

    class FakeSession:
        id = "cs_mock_1"
        url = "https://checkout.stripe.com/pay/cs_mock_1"

    def fake(*a, **kw):
        fake.last = (a, kw)
        return FakeSession()
    payments.create_checkout_session = fake
    try:
        with Env(STRIPE_SECRET_KEY="sk_test_fake"):
            client = appmod.app.test_client()
            add_to_cart(client, qty=1)
            r = client.post("/checkout/create",
                            data={"name": "Jane Doe",
                                  "email": "jane@example.com",
                                  "line1": "5 Main St", "city": "Rosemead",
                                  "state": "CA", "zip": "91770"})
            check("redirects (303) to Stripe URL",
                  r.status_code == 303
                  and r.headers["Location"] == FakeSession.url,
                  f"{r.status_code} {r.headers.get('Location')}")
            order = db.get_order_by_session("cs_mock_1")
            check("order row created, status new",
                  order is not None and order["status"] == "new"
                  and order["customer_email"] == "jane@example.com")
            check("line items snapshotted server-side",
                  order is not None and len(order["line_items"]) == 1
                  and order["total_cents"] > 0)
            with client.session_transaction() as s:
                check("cart cleared after session creation",
                      not s.get("cart"))
    finally:
        payments.create_checkout_session = real


def test_checkout_validation():
    print("3 — checkout form validation")
    with Env(STRIPE_SECRET_KEY="sk_test_fake"):
        client = appmod.app.test_client()
        add_to_cart(client)
        real = payments.create_checkout_session
        payments.create_checkout_session = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not reach Stripe"))
        try:
            r = client.post("/checkout/create",
                            data={"name": "", "email": "bad-email"})
            check("bad name/email -> 200 with error, no Stripe call",
                  r.status_code == 200
                  and "valid email" in r.get_data(as_text=True))
        finally:
            payments.create_checkout_session = real


# ---------------------------------------------------------------- 2. webhook
def test_webhook():
    print("4 — webhook signature verification + idempotency")
    secret = "whsec_test_abc123"
    with Env(STRIPE_WEBHOOK_SECRET=secret):
        client = appmod.app.test_client()
        order, prod = make_order(client, qty=2)
        stock_before = db.get_stock(prod["id"])["stock"]
        payload = webhook_payload(order["stripe_session_id"])
        hdr = {"Stripe-Signature": stripe_sig(payload, secret)}

        r = client.post("/stripe/webhook", data=payload, headers=hdr,
                        content_type="application/json")
        check("valid signature -> 200", r.status_code == 200,
              r.get_data(as_text=True)[:120])
        o = db.get_order(order["id"])
        check("order marked paid", o["status"] == "paid")
        check("payment intent recorded",
              o["stripe_payment_intent_id"] == "pi_test_1")
        stock_after = db.get_stock(prod["id"])["stock"]
        check("inventory decremented once",
              stock_after == max(0, stock_before - 2),
              f"{stock_before} -> {stock_after}")

        # Duplicate delivery: must be a no-op.
        r = client.post("/stripe/webhook", data=payload, headers=hdr,
                        content_type="application/json")
        o2 = db.get_order(order["id"])
        stock_again = db.get_stock(prod["id"])["stock"]
        check("duplicate webhook -> 200, still paid",
              r.status_code == 200 and o2["status"] == "paid")
        check("no double decrement", stock_again == stock_after,
              f"{stock_after} -> {stock_again}")

        # Bad signature rejected.
        bad = dict(hdr)
        bad["Stripe-Signature"] = "t=123,v1=deadbeef"
        r = client.post("/stripe/webhook", data=payload, headers=bad,
                        content_type="application/json")
        check("bad signature -> 400", r.status_code == 400)

        # Unknown session id: accepted (200) but nothing breaks.
        payload2 = webhook_payload("cs_test_unknown")
        r = client.post("/stripe/webhook", data=payload2,
                        headers={"Stripe-Signature":
                                 stripe_sig(payload2, secret)},
                        content_type="application/json")
        check("unknown session -> 200, no crash", r.status_code == 200)

    with Env(STRIPE_WEBHOOK_SECRET=None):
        client = appmod.app.test_client()
        r = client.post("/stripe/webhook", data=b"{}",
                        headers={"Stripe-Signature": "t=1,v1=x"},
                        content_type="application/json")
        check("no webhook secret -> 400 (fail closed)", r.status_code == 400)


# ---------------------------------------------------------------- 3. lifecycle
def test_order_lifecycle():
    print("5 — order lifecycle transitions")
    client = appmod.app.test_client()
    order, _ = make_order(client)
    oid = order["id"]
    ok, _ = db.transition_order(oid, "paid")
    check("new -> paid", ok and db.get_order(oid)["status"] == "paid")
    ok, msg = db.transition_order(oid, "new")
    check("paid -> new rejected", not ok, msg)
    ok, _ = db.transition_order(oid, "shipped", tracking_number="1Z999")
    o = db.get_order(oid)
    check("paid -> shipped with tracking",
          ok and o["status"] == "shipped" and o["tracking_number"] == "1Z999")
    ok, _ = db.transition_order(oid, "cancelled")
    check("shipped is terminal", not ok)

    order2, _ = make_order(client)
    ok, _ = db.transition_order(order2["id"], "shipped")
    check("new -> shipped rejected", not ok)
    ok, _ = db.transition_order(order2["id"], "cancelled")
    check("new -> cancelled", ok
          and db.get_order(order2["id"])["status"] == "cancelled")
    ok, _ = db.transition_order(order2["id"], "paid")
    check("cancelled is terminal", not ok)
    ok, _ = db.transition_order(999999, "paid")
    check("unknown order rejected", not ok)
    ok, _ = db.transition_order(order2["id"], "bogus")
    check("unknown status rejected", not ok)


def test_inventory_idempotent():
    print("6 — inventory decrement idempotency (db level)")
    client = appmod.app.test_client()
    order, prod = make_order(client, qty=1)
    db.mark_order_paid(order["id"])
    s0 = db.get_stock(prod["id"])["stock"]
    check("first decrement applies", db.decrement_stock_for_order(order["id"]))
    s1 = db.get_stock(prod["id"])["stock"]
    check("stock decreased by 1", s1 == max(0, s0 - 1), f"{s0}->{s1}")
    check("second call is no-op", not db.decrement_stock_for_order(order["id"]))
    check("stock unchanged", db.get_stock(prod["id"])["stock"] == s1)
    # Unpaid order never decrements.
    order2, prod2 = make_order(client, qty=1)
    check("unpaid order not decremented",
          not db.decrement_stock_for_order(order2["id"]))


# ---------------------------------------------------------------- 4. admin
def login(client, pw="s3cret"):
    return client.post("/admin/login", data={"password": pw},
                       follow_redirects=False)


def test_admin_denied_without_password():
    print("7 — admin denied when ADMIN_PASSWORD unset")
    with Env(ADMIN_PASSWORD=None):
        client = appmod.app.test_client()
        r = client.get("/admin", follow_redirects=False)
        check("GET /admin -> redirect to login",
              r.status_code == 302 and "/admin/login" in r.headers["Location"])
        r = client.post("/admin/login", data={"password": "anything"})
        check("POST /admin/login -> 403 disabled",
              r.status_code == 403
              and "not configured" in r.get_data(as_text=True))
        r = client.post("/admin/order/1/status", data={"action": "paid"})
        check("status change without auth -> redirect to login",
              r.status_code == 302 and "/admin/login" in r.headers["Location"])


def test_admin_flow():
    print("8 — admin login/logout + protected routes + actions")
    with Env(ADMIN_PASSWORD="s3cret"):
        client = appmod.app.test_client()
        r = login(client, "wrong")
        check("wrong password -> 401", r.status_code == 401)
        r = client.get("/admin")
        check("still locked after bad login",
              r.status_code == 302 and "/admin/login" in r.headers["Location"])
        r = login(client, "s3cret")
        check("right password -> 302 to /admin",
              r.status_code == 302 and r.headers["Location"].endswith("/admin"))
        r = client.get("/admin")
        body = r.get_data(as_text=True)
        check("GET /admin -> 200 with dashboard",
              r.status_code == 200 and "Admin Dashboard" in body
              and "Inventory" in body)

        # Admin-driven status change incl. tracking number.
        order, _ = make_order(client)
        r = client.post(f"/admin/order/{order['id']}/status",
                        data={"action": "paid"}, follow_redirects=False)
        check("mark paid -> redirect",
              r.status_code == 302
              and db.get_order(order["id"])["status"] == "paid")
        r = client.post(f"/admin/order/{order['id']}/status",
                        data={"action": "shipped", "tracking": "1ZTRACK1"},
                        follow_redirects=False)
        o = db.get_order(order["id"])
        check("mark shipped stores tracking",
              r.status_code == 302 and o["status"] == "shipped"
              and o["tracking_number"] == "1ZTRACK1")

        # Inventory edit.
        prod = db.list_products()[0]
        r = client.post("/admin/inventory",
                        data={"product_id": prod["id"], "stock": "7",
                              "low_threshold": "10"},
                        follow_redirects=False)
        inv = db.get_stock(prod["id"])
        check("inventory edit saved",
              r.status_code == 302 and inv["stock"] == 7
              and inv["low_threshold"] == 10)
        r = client.get("/admin")
        check("low-stock flagged in dashboard",
              "⚠️" in r.get_data(as_text=True))

        # Logout locks it again.
        client.get("/admin/logout")
        r = client.get("/admin")
        check("logout -> protected again",
              r.status_code == 302 and "/admin/login" in r.headers["Location"])


# ---------------------------------------------------------------- 5. email
def test_email_graceful():
    print("9 — email skipped gracefully without SMTP")
    with Env(SMTP_HOST=None):
        check("smtp not configured", not emails.smtp_configured())
        check("send returns False, no raise",
              emails.send_email_channel("a@b.c", "s", "b") is False)
        order = {"id": 1, "customer_name": "T", "customer_email": "t@e.com",
                 "total_cents": 2500, "stripe_session_id": "cs_x",
                 "addr_line1": "1", "addr_line2": "", "addr_city": "R",
                 "addr_state": "CA", "addr_zip": "9", "tracking_number": None,
                 "line_items": [{"qty": 1, "name": "Basic LED Bulbs",
                                 "variation_label": "H11 / 6000K",
                                 "size": "H11", "color_temp": "",
                                 "line_total_cents": 2500}]}
        check("order-paid notify no-raise",
              emails.notify_customer_order_paid(order) is False)
        check("shipped notify no-raise",
              emails.notify_customer_shipped(order) is False)
        check("owner notify no-raise",
              emails.notify_owner_new_order(order) is False)


def test_notify_email_default():
    print("10 — ORDER_NOTIFY_EMAIL default (test address, env-overridable)")
    with Env(ORDER_NOTIFY_EMAIL=None):
        check("defaults to zoltar.works@gmail.com",
              emails.order_notify_email() == "zoltar.works@gmail.com")
    with Env(ORDER_NOTIFY_EMAIL="orders@example.com"):
        check("env var overrides default",
              emails.order_notify_email() == "orders@example.com")


def _strip_strings(text):
    """Blank out Python string literals so compliance validation code that
    *mentions* the forbidden word isn't flagged — only customer-facing copy
    (templates, product data) counts."""
    return re.sub(r"('''.*?'''|\"\"\".*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\")",
                  '""', text, flags=re.S)


# ---------------------------------------------------------------- 6. copy guard
def test_copy_guards():
    print("11 — copy guards on Phase 2 files")
    owned = ["app.py", "db.py", "payments.py", "emails.py",
             "templates/checkout.html", "templates/checkout_success.html",
             "templates/checkout_cancel.html", "templates/admin.html",
             "templates/admin_login.html",
             "templates/product.html",
             "templates/cart.html"]
    bad = []
    for f in owned:
        text = open(os.path.join(WEB, f), encoding="utf-8").read().lower()
        if f.endswith(".py"):
            text = _strip_strings(text)
        if "headlight" in text:
            bad.append(f)
    check("no 'headlight' in Phase 2 files", not bad, str(bad))
    names = [p["name"] for p in db.list_products()]
    check("no product named with 'off-road'",
          not any("off-road" in n.lower() for n in names), str(names))


def main():
    test_checkout_no_keys()
    test_checkout_create_mocked()
    test_checkout_validation()
    test_webhook()
    test_order_lifecycle()
    test_inventory_idempotent()
    test_admin_denied_without_password()
    test_admin_flow()
    test_email_graceful()
    test_notify_email_default()
    test_copy_guards()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL PHASE 2 TESTS PASSED")


if __name__ == "__main__":
    main()
