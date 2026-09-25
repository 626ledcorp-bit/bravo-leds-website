#!/usr/bin/env python3
"""Order/shipping backend tests: carrier detection, Pirate Ship CSV export,
bulk tracking import, Stripe refunds (mocked), notification toggles, settings
persistence, order search/filter, timeline events, packing slip, pick list,
contact-form and review alerts, low-stock digest, payment-failed notice.

No network: Stripe is mocked, SMTP stays unconfigured (send path is faked).

Run:  cd website && .venv/bin/python test_shipping.py
"""

import csv
import io
import os
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="ship_"),
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
import shipping    # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


class Sent:
    """Fake for emails.send_email_channel: records (to, subject)."""
    def __init__(self):
        self.calls = []

    def __call__(self, to, subject, body, reply_to=None):
        self.calls.append((to, subject, body))
        return True


def reset_toggles():
    """All notification toggles on (settings form posts only checked
    boxes, so test 6's POST turns the others off)."""
    for key, _label, _group in db.NOTIFY_TOGGLES:
        db.set_setting(key, "1")


def login(client, pw="secret"):
    return client.post("/admin/login", data={"password": pw})


def admin_client():
    os.environ["ADMIN_PASSWORD"] = "secret"
    c = appmod.app.test_client()
    login(c)
    return c


def make_customer(linespec=("Basic LED Bulbs", 2)):
    """Create a product + a paid order. linespec is (name, qty)."""
    name, qty = linespec
    pid = db.unique_product_id(db.slugify_product_id(name))
    sku = f"SKU-{pid.upper().replace('-', '')[:8]}"
    db.create_product({
        "id": pid,
        "name": name, "category": "LED Bulbs", "price_cents": 5000,
        "status": "active", "weight_oz": 8.0, "length_in": 6.0,
        "width_in": 4.0, "height_in": 3.0, "sku": sku,
    })
    db.set_stock(pid, 10)
    p = db.get_product(pid)
    unit = p["price_cents"]
    lines = [{"key": "k1", "product": p, "variation": None,
              "product_id": pid, "name": name, "variation_id": None,
              "variation_label": "", "size": "", "color_temp": "",
              "qty": qty, "unit_price_cents": unit,
              "line_total": unit * qty}]
    oid = db.create_order({"name": "Ship Tester",
                           "email": "ship@example.com", "line1": "1 Test St",
                           "city": "Rosemead", "state": "CA", "zip": "91770"},
                          lines, unit * qty)
    db.mark_order_paid(oid, payment_intent_id=f"pi_test_{oid}")
    return oid, pid


# ---------------------------------------------------------------- 1. carriers
def test_carrier_detection():
    print("1 — carrier detection + tracking URLs")
    cases = [
        ("1Z999AA10123456784", "UPS", "ups.com/track"),
        ("1z999aa10123456784", "UPS", "ups.com/track"),  # lowercase ok
        ("123456789012", "FedEx", "fedex.com"),
        ("123456789012345", "FedEx", "fedex.com"),
        ("9612345678901234567890", "FedEx", "fedex.com"),
        ("9400111899223344556677", "USPS", "usps.com"),
        ("9205590164917312345678", "USPS", "usps.com"),
        ("EC123456789US", "USPS", "usps.com"),
        ("1234567890", "DHL", "dhl.com"),
        ("not-a-tracking-number", "Unknown", None),
        ("", "Unknown", None),
    ]
    for num, want_carrier, want_host in cases:
        carrier, url = shipping.detect_carrier(num)
        ok = carrier == want_carrier and (url is None or want_host in url)
        check(f"detect {num[:18]!r} -> {want_carrier}", ok,
              f"got ({carrier}, {url})")
        if want_host:
            check(f"url embeds number for {num[:12]}", num.strip() in url)


# ---------------------------------------------------------------- 2. bulk parse
def test_bulk_parse():
    print("2 — bulk tracking textarea parsing")
    entries, errors = shipping.parse_bulk_tracking(
        "1042 1Z999AA10123456784\n"
        "# a comment\n"
        "\n"
        "1043   9400111899223344556677  \n"
        "oops-no-id 1Z123\n"
        "1044\n")
    check("parses 2 valid entries",
          entries == [(1042, "1Z999AA10123456784"),
                      (1043, "9400111899223344556677")], str(entries))
    check("2 parse errors reported", len(errors) == 2, str(errors))


# ---------------------------------------------------------------- 3. CSV export
def test_pirate_ship_csv():
    print("3 — Pirate Ship CSV export")
    oid, pid = make_customer(("CSV Bulb", 3))
    order = db.get_order(oid)
    text = shipping.pirate_ship_csv([order], get_product=db.get_product,
                                    tare_oz=3.0)
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    check("exact header set",
          list(rows[0].keys()) == shipping.PIRATE_SHIP_COLUMNS,
          str(list(rows[0].keys())))
    r = rows[0]
    check("order id + address fields",
          r["Order ID"] == str(oid) and r["Address Line 1"] == "1 Test St"
          and r["City"] == "Rosemead" and r["State"] == "CA"
          and r["Zipcode"] == "91770" and r["Email"] == "ship@example.com",
          str(r))
    check("weight = 3x8oz + 3oz tare",
          r["Weight (oz)"] == "27.0", r["Weight (oz)"])
    check("dims carried over",
          (r["Length (in)"], r["Width (in)"], r["Height (in)"])
          == ("6.0", "4.0", "3.0"))
    check("contents + total",
          "3x CSV Bulb" in r["Package Contents"]
          and r["Order Total"] == "150.00", str(r))


# ---------------------------------------------------------------- 4. refunds
def test_refunds():
    print("4 — Stripe refunds (mocked)")
    oid, _ = make_customer(("Refund Bulb", 1))  # $50.00 paid
    client = admin_client()

    real = payments.refund_payment_intent
    calls = []

    def fake_refund(pi_id, amount_cents=None, reason=None):
        calls.append((pi_id, amount_cents, reason))
        return {"id": "re_test", "status": "succeeded"}

    payments.refund_payment_intent = fake_refund
    try:
        # partial refund $10
        r = client.post(f"/admin/order/{oid}/refund",
                        data={"amount": "10.00", "reason": "requested"},
                        follow_redirects=False)
        o = db.get_order(oid)
        check("partial refund recorded",
              o["refunded_cents"] == 1000, str(o["refunded_cents"]))
        check("stripe called with 1000c + reason",
              calls[-1] == (f"pi_test_{oid}", 1000, "requested_by_customer"),
              str(calls[-1]))
        # over-refund rejected
        r = client.post(f"/admin/order/{oid}/refund",
                        data={"amount": "999.00", "reason": "requested"},
                        follow_redirects=True)
        o = db.get_order(oid)
        check("over-refund rejected",
              o["refunded_cents"] == 1000
              and "between" in r.get_data(as_text=True))
        check("stripe not called again", len(calls) == 1)
        # full refund via blank amount
        r = client.post(f"/admin/order/{oid}/refund",
                        data={"amount": "", "reason": "duplicate"},
                        follow_redirects=True)
        o = db.get_order(oid)
        check("full refund tops up to total",
              o["refunded_cents"] == 5000, str(o["refunded_cents"]))
        check("stripe called with amount=None (full)",
              calls[-1][1] is None and calls[-1][2] == "duplicate",
              str(calls[-1]))
        evs = db.list_order_events(oid)
        check("timeline has refund events",
              sum(1 for e in evs if e["event"] == "refund") == 2,
              str([e["event"] for e in evs]))
    finally:
        payments.refund_payment_intent = real

    # order without a Stripe payment -> friendly message, no crash
    oid2 = db.create_order({"name": "Manual", "email": "m@example.com"},
                           [], 0)
    r = admin_client().post(f"/admin/order/{oid2}/refund",
                            data={"amount": "5.00"},
                            follow_redirects=True)
    check("refund w/o stripe payment -> friendly msg",
          "Stripe dashboard" in r.get_data(as_text=True))


# ---------------------------------------------------------------- 5. toggles
def test_notification_toggles():
    print("5 — every notification toggle suppresses its email")
    sent = Sent()
    real = emails.send_email_channel
    emails.send_email_channel = sent
    try:
        oid, _ = make_customer(("Toggle Bulb", 1))
        order = db.get_order(oid)

        def one(toggle, fn, *args):
            db.set_setting(toggle, "0")
            before = len(sent.calls)
            fn(*args)
            suppressed = len(sent.calls) == before
            db.set_setting(toggle, "1")
            before = len(sent.calls)
            fn(*args)
            delivered = len(sent.calls) == before + 1
            check(f"{toggle}: off suppresses / on delivers",
                  suppressed and delivered)

        one("notify_customer_order_confirmation",
            emails.notify_customer_order_paid, order)
        one("notify_customer_payment_failed",
            emails.notify_customer_payment_failed, order)
        one("notify_customer_shipped",
            emails.notify_customer_shipped, order)
        one("notify_owner_new_order",
            emails.notify_owner_new_order, order)
        one("notify_owner_low_stock",
            emails.notify_owner_low_stock, [("Bulb", "SKU-1", 2)])
        one("notify_owner_contact_message",
            emails.notify_owner_contact_message,
            "Jane", "j@example.com", "hello")
        one("notify_owner_review_submitted",
            emails.notify_owner_review_submitted,
            "Bulb", "Jane", 5, "great")
    finally:
        emails.send_email_channel = real


# ---------------------------------------------------------------- 6. settings
def test_settings_page():
    print("6 — settings persistence via /admin/settings")
    client = admin_client()
    r = client.post("/admin/settings", data={
        "notify_owner_new_order": "on",
        # notify_customer_shipped omitted -> off
        "email_owner_orders": "orders@example.com",
        "email_owner_alerts": "alerts@example.com",
        "shipping_tare_oz": "5",
    }, follow_redirects=True)
    check("settings POST redirects + saves",
          r.status_code == 200
          and db.get_setting("email_owner_orders") == "orders@example.com"
          and db.get_setting("email_owner_alerts") == "alerts@example.com"
          and db.get_setting("shipping_tare_oz") == "5.0")
    check("checked toggle on, unchecked off",
          db.get_setting("notify_owner_new_order") == "1"
          and db.get_setting("notify_customer_shipped") == "0")
    check("owner email resolution prefers settings",
          emails.order_notify_email() == "orders@example.com")
    r = client.get("/admin/settings")
    check("settings page renders",
          r.status_code == 200 and "orders@example.com" in
          r.get_data(as_text=True))


# ---------------------------------------------------------------- 7. search
def test_search_filter():
    reset_toggles()
    print("7 — order search / status / date filters")
    res = db.search_orders(q="ship@example.com")
    check("search by email finds orders",
          len(res) >= 3, str(len(res)))
    res = db.search_orders(q=str(res[0]["id"]))
    check("search by order id", len(res) == 1)
    res = db.search_orders(status="paid")
    check("status filter paid", all(o["status"] == "paid" for o in res)
          and len(res) >= 3)
    res = db.search_orders(status="shipped")
    check("status filter shipped (none yet)", res == [])
    res = db.search_orders(date_from="2026-01-01", date_to="2026-12-31")
    check("date range covers test orders", len(res) >= 3)
    res = db.search_orders(date_from="2030-01-01")
    check("future date range empty", res == [])
    res = db.search_orders(q="no-such-buyer-zzz")
    check("no-match search empty", res == [])


# ---------------------------------------------------------------- 8. timeline
def test_timeline_and_notes():
    reset_toggles()
    print("8 — order timeline + admin notes")
    oid, _ = make_customer(("Timeline Bulb", 1))
    evs = db.list_order_events(oid)
    check("created + paid events logged",
          [e["event"] for e in evs] == ["created", "paid"],
          str([e["event"] for e in evs]))
    ok, _ = db.transition_order(oid, "shipped",
                                tracking_number="1Z999AA10123456784")
    evs = db.list_order_events(oid)
    check("shipped event logs tracking",
          ok and evs[-1]["event"] == "shipped"
          and "1Z999" in evs[-1]["detail"])
    db.update_order_notes(oid, "gift wrap please")
    check("note saved + logged",
          db.get_order(oid)["notes"] == "gift wrap please"
          and db.list_order_events(oid)[-1]["event"] == "note")


# ---------------------------------------------------------------- 9. admin pages
def test_admin_order_pages():
    reset_toggles()
    print("9 — admin order pages (dashboard, detail, slip, pick list)")
    oid, _ = make_customer(("Page Bulb", 1))
    client = admin_client()

    r = client.get("/admin/orders")
    body = r.get_data(as_text=True)
    check("GET /admin/orders", r.status_code == 200 and "Orders" in body)

    r = client.get(f"/admin/order/{oid}")
    body = r.get_data(as_text=True)
    check("GET /admin/order/<id>",
          r.status_code == 200 and f"Order #{oid}" in body
          and "pi_test_" in body)
    r = client.get("/admin/order/999999")
    check("detail 404 for missing order", r.status_code == 404)

    r = client.get(f"/admin/order/{oid}/packing-slip")
    check("packing slip renders",
          r.status_code == 200 and "PACKING SLIP" in
          r.get_data(as_text=True))

    r = client.get("/admin/orders/pick-list")
    check("pick list renders",
          r.status_code == 200 and "Pick List" in r.get_data(as_text=True))

    r = client.get("/admin/orders/tracking")
    check("tracking page renders", r.status_code == 200
          and "order_id tracking_number" in r.get_data(as_text=True))

    # CSV export: only selected paid orders
    oid_new = db.create_order({"name": "New Guy", "email": "n@example.com"},
                              [], 0)
    r = client.post("/admin/orders/export",
                    data={"order_ids": [str(oid), str(oid_new)]})
    check("CSV export 200 + attachment",
          r.status_code == 200 and "pirate-ship-orders.csv" in
          r.headers.get("Content-Disposition", ""))
    rows = list(csv.DictReader(
        io.StringIO(r.get_data(as_text=True).lstrip("\ufeff"))))
    check("only the paid order exported",
          len(rows) == 1 and rows[0]["Order ID"] == str(oid),
          str([x["Order ID"] for x in rows]))


# ---------------------------------------------------------------- 10. bulk tracking
def test_bulk_tracking_flow():
    reset_toggles()
    print("10 — bulk tracking import flow")
    sent = Sent()
    real = emails.send_email_channel
    emails.send_email_channel = sent
    db.set_setting("notify_customer_shipped", "1")
    try:
        oid1, _ = make_customer(("Bulk A", 1))
        oid2, _ = make_customer(("Bulk B", 1))
        client = admin_client()
        r = client.post("/admin/orders/tracking", data={
            "bulk": f"{oid1} 1Z999AA10123456784\n"
                    f"{oid2} 9400111899223344556677\n"
                    f"999999 1Z123\n"
                    f"badline\n"},
            follow_redirects=True)
        body = r.get_data(as_text=True)
        o1, o2 = db.get_order(oid1), db.get_order(oid2)
        check("both orders shipped w/ tracking",
              o1["status"] == "shipped"
              and o1["tracking_number"] == "1Z999AA10123456784"
              and o2["status"] == "shipped")
        check("shipped emails sent (toggle on)",
              sum("shipped" in s for _, s, _ in sent.calls) == 2,
              str([s for _, s, _ in sent.calls]))
        check("bad lines reported",
              "order not found" in body and "order_id tracking_number" in body)
        # toggle off -> no email on next shipment
        db.set_setting("notify_customer_shipped", "0")
        oid3, _ = make_customer(("Bulk C", 1))
        sent.calls.clear()
        client.post("/admin/orders/tracking",
                    data={"bulk": f"{oid3} 123456789012"},
                    follow_redirects=True)
        check("no shipped email when toggle off",
              sent.calls == [] and db.get_order(oid3)["status"] == "shipped")
        db.set_setting("notify_customer_shipped", "1")
    finally:
        emails.send_email_channel = real


# ---------------------------------------------------------------- 11. contact + review
def test_contact_and_review_alerts():
    reset_toggles()
    print("11 — contact form + review owner alerts")
    sent = Sent()
    real = emails.send_email_channel
    emails.send_email_channel = sent
    try:
        client = appmod.app.test_client()
        r = client.post("/contact", data={"name": "Pat",
                                          "email": "pat@example.com",
                                          "message": "fitment question"})
        check("contact POST thanks user",
              r.status_code == 200 and "on its way" in
              r.get_data(as_text=True))
        check("owner contact alert queued",
              any("Contact form" in s for _, s, _ in sent.calls))
        r = client.post("/contact", data={"name": "", "email": "x",
                                          "message": ""})
        check("contact validation error",
              "valid email" in r.get_data(as_text=True))

        _, pid = make_customer(("Review Bulb", 1))
        r = client.post(f"/product/{pid}/review",
                        data={"name": "Sam", "rating": "5",
                              "body": "bright!"})
        check("review submit -> owner alert",
              any("New review" in s for _, s, _ in sent.calls),
              str([s for _, s, _ in sent.calls]))
    finally:
        emails.send_email_channel = real


# ---------------------------------------------------------------- 12. low stock
def test_low_stock_digest():
    reset_toggles()
    print("12 — low-stock digest fires only on crossing the threshold")
    sent = Sent()
    real = emails.send_email_channel
    emails.send_email_channel = sent
    db.set_setting("notify_owner_low_stock", "1")
    try:
        low_pid = db.unique_product_id(db.slugify_product_id("Low Bulb"))
        pid = db.create_product({"id": low_pid, "name": "Low Bulb",
                                 "category": "LED Bulbs",
                                 "price_cents": 1000, "status": "active"})
        db.set_stock(pid, 3, 2)  # 3 in stock, threshold 2
        before = db.low_stock_keys()
        check("not low yet", f"product:{pid}" not in before)
        db.set_stock(pid, 2)
        after = db.low_stock_keys()
        newly = after - before
        check("crossing threshold detected", f"product:{pid}" in newly)
        emails.notify_owner_low_stock(db.low_stock_details(newly))
        check("low-stock email sent",
              any("Low stock" in s for _, s, _ in sent.calls))
        check("detail line names product + qty",
              any("Low Bulb" in b and "2 left" in b
                  for _, _, b in sent.calls))
    finally:
        emails.send_email_channel = real


# ---------------------------------------------------------------- 13. payment failed
def test_payment_failed_notice():
    print("13 — payment-failed customer notice")
    sent = Sent()
    real = emails.send_email_channel
    emails.send_email_channel = sent
    db.set_setting("notify_customer_payment_failed", "1")
    try:
        oid, _ = make_customer(("Fail Bulb", 1))
        order = db.get_order(oid)
        emails.notify_customer_payment_failed(order)
        check("payment-failed email sent",
              any("didn't go through" in s for _, s, _ in sent.calls))
        db.set_setting("notify_customer_payment_failed", "0")
        sent.calls.clear()
        emails.notify_customer_payment_failed(order)
        check("suppressed when toggle off", sent.calls == [])
    finally:
        emails.send_email_channel = real
        db.set_setting("notify_customer_payment_failed", "1")


def main():
    test_carrier_detection()
    test_bulk_parse()
    test_pirate_ship_csv()
    test_refunds()
    test_notification_toggles()
    test_settings_page()
    test_search_filter()
    test_timeline_and_notes()
    test_admin_order_pages()
    test_bulk_tracking_flow()
    test_contact_and_review_alerts()
    test_low_stock_digest()
    test_payment_failed_notice()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:")
        for name in FAIL:
            print(" -", name)
        sys.exit(1)


if __name__ == "__main__":
    main()
