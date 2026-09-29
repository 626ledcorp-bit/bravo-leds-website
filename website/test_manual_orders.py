#!/usr/bin/env python3
"""Manual (phone) order tests: pay-token DB plumbing, the /admin/orders/new
form, the public /pay/<token> invoice page, and the invoice checkout route.

No network calls: Stripe is left unconfigured so the invoice checkout route
must degrade gracefully.

Run:  cd website && .venv/bin/python test_manual_orders.py
"""

import os
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="mo_"),
                                      "store.db")
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
           "SMTP_PASS", "SMTP_FROM", "ORDER_NOTIFY_EMAIL",
           "ADMIN_TOTP_SECRET"):
    os.environ.pop(_v, None)
os.environ["ADMIN_PASSWORD"] = "s3cret-test"

import db          # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


def make_catalog_line(price_cents=2500, qty=2):
    return {"product": {"id": "p1", "name": "Basic LED Bulbs",
                        "price_cents": price_cents},
            "variation": None, "variation_label": "H11 / 6000K",
            "size": "H11", "color_temp": "6000K", "qty": qty,
            "unit_price_cents": price_cents,
            "line_total": price_cents * qty, "vehicle": ""}


print("1 — pay_token DB plumbing")
con_cols = [r[1] for r in
            __import__("sqlite3").connect(os.environ["STORE_DB"])
            .execute("PRAGMA table_info(orders)").fetchall()]
check("orders.pay_token column exists", "pay_token" in con_cols)

oid = db.create_order({"name": "Phone Customer", "email": "pc@example.com"},
                      [make_catalog_line()], 5000, pay_token="tok_abc123")
o = db.get_order(oid)
check("create_order stores pay_token", o["pay_token"] == "tok_abc123")
check("get_order_by_pay_token roundtrip",
      (db.get_order_by_pay_token("tok_abc123") or {}).get("id") == oid)
check("unknown token -> None", db.get_order_by_pay_token("nope") is None)
check("empty token -> None", db.get_order_by_pay_token("") is None)

oid2 = db.create_order({"name": "Web Buyer", "email": "w@example.com"},
                       [make_catalog_line()], 5000)
check("web order has no pay_token",
      db.get_order(oid2)["pay_token"] in (None, ""))

t1 = appmod._new_pay_token()
t2 = appmod._new_pay_token()
check("minted tokens are unique", t1 != t2 and len(t1) >= 32)
check("minted token not guessable format", len(t2) >= 32)

print("2 — admin form + invoice pages")
client = appmod.app.test_client()

r = client.get("/admin/orders/new", follow_redirects=False)
check("GET /admin/orders/new unauthenticated -> login",
      r.status_code == 302 and "/admin/login" in r.headers["Location"])

r = client.post("/admin/login", data={"password": "s3cret-test"},
                follow_redirects=False)
check("admin login ok", r.status_code == 302)

r = client.get("/admin/orders/new")
check("GET /admin/orders/new -> 200", r.status_code == 200,
      f"got {r.status_code}")
check("form has customer fields",
      b'name="name"' in r.data and b'name="email"' in r.data)
check("form has custom-item fields", b'name="cname"' in r.data)

# Validation: missing customer name -> 400 with errors, rows preserved.
r = client.post("/admin/orders/new", data={
    "name": "", "email": "pc@example.com", "fulfillment": "ship",
    "cname": ["Install labor"], "cprice": ["40"], "cqty": ["1"],
    "shipping": "", "discount": "", "notes": ""})
check("invalid form -> 400", r.status_code == 400, f"got {r.status_code}")
check("error message shown", b"valid email" in r.data or b"required" in r.data)

# Happy path with a custom line only.
r = client.post("/admin/orders/new", data={
    "name": "Phone Customer", "email": "pc@example.com",
    "fulfillment": "pickup",
    "cname": ["Install labor"], "cprice": ["40"], "cqty": ["1"],
    "shipping": "", "discount": "5", "notes": "called about Tacoma"},
              follow_redirects=False)
check("valid form -> redirect to detail",
      r.status_code == 302 and "/admin/order/" in r.headers["Location"],
      f"got {r.status_code}")
new_oid = int(r.headers["Location"].rstrip("/").split("/")[-1])
no = db.get_order(new_oid)
check("manual order status is new", no["status"] == "new")
check("manual order fulfillment pickup", no["fulfillment"] == "pickup")
check("manual order has pay_token", bool(no["pay_token"]))
check("manual order totals: 40 - 5 = 35",
      no["subtotal_cents"] == 4000 and no["discount_cents"] == 500
      and no["total_cents"] == 3500,
      f"sub={no['subtotal_cents']} disc={no['discount_cents']} "
      f"tot={no['total_cents']}")
check("staff note recorded", "Manual order" in (no["notes"] or ""))
check("custom line snapshotted",
      no["line_items"] and no["line_items"][0]["name"] == "Install labor"
      and no["line_items"][0]["product_id"] == "custom")

token = no["pay_token"]
r = client.get(f"/pay/{token}")
check("GET /pay/<token> -> 200", r.status_code == 200,
      f"got {r.status_code}")
check("invoice shows total", b"$35.00" in r.data)
check("invoice shows customer name", b"Phone Customer" in r.data)
# No Stripe keys in this test env: the invoice must degrade gracefully
# instead of showing a pay button.
check("invoice w/o Stripe shows graceful message, no pay button",
      b"isn't available" in r.data and b"/checkout" not in r.data)
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
r = client.get(f"/pay/{token}")
check("invoice with Stripe shows pay button",
      f"/pay/{token}/checkout".encode() in r.data and b"Pay $35.00" in r.data)
del os.environ["STRIPE_SECRET_KEY"]
check("invoice pickup note", b"pickup" in r.data.lower())

r = client.get("/pay/does-not-exist")
check("GET /pay/<bad> -> 404", r.status_code == 404)

r = client.post(f"/pay/{token}/checkout")
check("POST checkout w/o Stripe -> 200 graceful",
      r.status_code == 200 and b"isn't available" in r.data,
      f"got {r.status_code}")
check("order still new after failed pay setup",
      db.get_order(new_oid)["status"] == "new"
      and not db.get_order(new_oid)["stripe_session_id"])

# Catalog line through the real product picker (server-side price lookup).
prods = db.list_products(include_drafts=True)
real = next((p for p in prods if (p.get("variations") or [])), None)
if real:
    var = real["variations"][0]
    unit = var["effective_price_cents"]
    r = client.post("/admin/orders/new", data={
        "name": "Catalog Caller", "email": "cc@example.com",
        "fulfillment": "ship",
        "pid": [str(real["id"])], "vid": [str(var["id"])], "qty": ["3"],
        "shipping": "8.50", "discount": "", "notes": ""},
        follow_redirects=False)
    check("catalog-line order -> redirect", r.status_code == 302,
          f"got {r.status_code}")
    coid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    co = db.get_order(coid)
    check("catalog line uses server price",
          co["line_items"][0]["price_cents"] == unit
          and co["line_items"][0]["qty"] == 3
          and co["subtotal_cents"] == unit * 3,
          f"unit={co['line_items'][0]['price_cents']} want={unit}")
    check("shipping recorded",
          co["shipping_cents"] == 850
          and co["total_cents"] == unit * 3 + 850)
else:
    check("catalog-line order (no products with variations in DB)", False,
          "skipped — seed data missing")

print("3 — paid/cancelled invoice states")
db.mark_order_paid(new_oid, payment_intent_id="pi_test")
r = client.get(f"/pay/{token}")
check("paid invoice shows paid message",
      r.status_code == 200 and b"has been paid" in r.data)
r = client.post(f"/pay/{token}/checkout", follow_redirects=False)
check("POST checkout on paid order -> redirect back to invoice",
      r.status_code == 302 and f"/pay/{token}" in r.headers["Location"])

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
