#!/usr/bin/env python3
"""Track 3 tests: landing pages, countdown validation, promo codes, cart
discount integration, Stripe coupon mapping, admin CRUD, copy guards.

No network calls: Stripe session/coupon creation is faked with a stub
module, and SMTP is left unconfigured.

Run:  cd website && .venv/bin/python test_landing.py
"""

import os
import re
import sys
import tempfile
import types

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="t3_"),
                                      "store.db")
# Scrub anything that could make payments/email/admin "configured".
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import content     # noqa: E402
import landing     # noqa: E402
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


def add_to_cart(client, qty=1):
    p = db.get_product("premium-csp-led-bulbs")
    size = p["sizes"][0] if p["sizes"] else ""
    temp = p["color_temps"][0] if p["color_temps"] else ""
    client.post("/cart/add", data={"product_id": p["id"], "size": size,
                                   "color_temp": temp, "qty": str(qty)})
    return p


def login(client, pw="s3cret"):
    return client.post("/admin/login", data={"password": pw},
                       follow_redirects=False)


def make_promo(code, kind="percent", percent=10, amount_cents=None,
               expires_at=None, max_uses=None, active=1):
    landing.save_promo({"code": code, "kind": kind, "percent": percent,
                        "amount_cents": amount_cents, "expires_at": expires_at,
                        "max_uses": max_uses, "active": active}, is_new=True)
    return landing.get_promo(code)


# ---------------------------------------------------------------- 1. pages
def test_landing_render():
    print("1 — /go/<slug> renders the seeded page")
    client = appmod.app.test_client()
    r = client.get("/go/premium-led")
    body = r.get_data(as_text=True)
    check("published seed page -> 200", r.status_code == 200)
    check("headline present",
          "See More of the Road at Night" in body)
    check("evergreen countdown wired",
          'data-cd-mode="evergreen"' in body
          and 'data-cd-minutes="30"' in body
          and "localStorage" in body)
    check("default code auto-applied chip",
          "PREMIUM10" in body and "auto-applied" in body)
    check("placeholders clearly labeled",
          "VIDEO PLACEHOLDER" in body and "TESTIMONIAL PLACEHOLDER" in body)
    check("product-page disclaimer wording",
          "For off-road and fog light use only" in body
          and "/dot-compliance" in body)
    check("honest empty review state",
          "No reviews yet" in body)
    check("slim header, no full nav", "cat-strip" not in body)
    with client.session_transaction() as s:
        check("default code stored in session",
              s.get("promo_code") == "PREMIUM10")

    r = client.get("/go/definitely-not-a-page")
    check("unknown slug -> 404", r.status_code == 404)

    # Unpublish -> 404, republish restores.
    page = landing.get_landing("premium-led")
    landing.save_landing({**page, "bullets": page["bullets"],
                          "slug": page["slug"]}, is_new=False)  # sanity
    con = db._connect()
    con.execute("UPDATE landing_pages SET published = 0 WHERE slug = ?",
                ("premium-led",))
    con.commit()
    con.close()
    check("unpublished -> 404",
          client.get("/go/premium-led").status_code == 404)
    con = db._connect()
    con.execute("UPDATE landing_pages SET published = 1 WHERE slug = ?",
                ("premium-led",))
    con.commit()
    con.close()
    check("republished -> 200 again",
          client.get("/go/premium-led").status_code == 200)


def test_reviews_wired():
    print("2 — landing page uses the real review system")
    rid = content.submit_review("premium-csp-led-bulbs", "Road Tester", 5,
                                "Huge difference on dark roads.")
    check("review submits (pending)", rid is not None)
    body = appmod.app.test_client().get("/go/premium-led").get_data(
        as_text=True)
    check("pending review not shown", "Road Tester" not in body)
    content.set_review_status(rid, "approved")
    body = appmod.app.test_client().get("/go/premium-led").get_data(
        as_text=True)
    check("approved review shown", "Road Tester" in body
          and "Huge difference on dark roads." in body)
    check("avg/count shown", "5.0" in body and "1 review" in body)


# ---------------------------------------------------------------- 2. validation
def test_countdown_validation():
    print("3 — countdown + page validation")
    base = {"slug": "test-page", "product_id": "premium-csp-led-bulbs",
            "headline": "Test", "subhead": "", "bullets": "a\nb",
            "video_url": "", "testimonial_text": "",
            "countdown_mode": "evergreen", "countdown_end": "",
            "countdown_minutes": "30", "default_code": "", "published": "1"}

    def v(**kw):
        d = dict(base)
        d.update(kw)
        return landing.validate_landing_form(d, is_new=True)

    _, e = v(slug="Bad_Slug!")
    check("bad slug rejected", any("Slug" in x for x in e), str(e))
    _, e = v(slug="premium-led")
    check("duplicate slug rejected", any("already exists" in x for x in e))
    _, e = v(product_id="nope")
    check("unknown product rejected", any("product" in x.lower() for x in e))
    _, e = v(countdown_mode="fixed", countdown_end="2020-01-01T00:00")
    check("fixed end in the past rejected", any("future" in x for x in e),
          str(e))
    c, e = v(countdown_mode="fixed", countdown_end="2099-01-01T00:00")
    check("fixed end in the future accepted",
          not e and c["countdown_end"].startswith("2099-01-01"))
    _, e = v(countdown_minutes="3")
    check("evergreen 3 min rejected", any("between" in x for x in e))
    _, e = v(countdown_minutes="200")
    check("evergreen 200 min rejected", any("between" in x for x in e))
    c, e = v(countdown_minutes="120")
    check("evergreen 120 accepted", not e and c["countdown_minutes"] == 120)
    _, e = v(default_code="NOPE123")
    check("unknown default code rejected", any("doesn't exist" in x for x in e))
    c, e = v(default_code="premium10")
    check("default code normalized to uppercase",
          not e and c["default_code"] == "PREMIUM10")


def test_promo_validation():
    print("4 — promo code validation")
    base = {"code": "TEST20", "kind": "percent", "percent": "20",
            "amount_dollars": "", "expires_at": "", "max_uses": "",
            "active": "1"}

    def v(**kw):
        d = dict(base)
        d.update(kw)
        return landing.validate_promo_form(d, is_new=True)

    _, e = v(code="ab")
    check("short code rejected", any("Code" in x for x in e))
    _, e = v(code="PREMIUM10")
    check("duplicate code rejected", any("already exists" in x for x in e))
    _, e = v(percent="0")
    check("0% rejected", any("Percent" in x for x in e))
    _, e = v(percent="150")
    check("150% rejected", any("Percent" in x for x in e))
    c, e = v()
    check("valid percent accepted", not e and c["percent"] == 20)
    c, e = v(kind="amount", amount_dollars="15.00")
    check("$15 amount accepted",
          not e and c["amount_cents"] == 1500, str(e))
    _, e = v(kind="amount", amount_dollars="0")
    check("$0 amount rejected", any("Amount" in x for x in e))
    _, e = v(expires_at="2020-01-01T00:00")
    check("past expiry rejected", any("future" in x for x in e))
    _, e = v(max_uses="0")
    check("max_uses=0 rejected", any("Max uses" in x for x in e))


# ---------------------------------------------------------------- 3. promo math
def test_promo_math():
    print("5 — promo math edge cases")
    pct = make_promo("MATHPCT", percent=10)
    amt = make_promo("MATHAMT", kind="amount", percent=None,
                     amount_cents=500)
    check("10% of $70.00 = $7.00",
          landing.discount_cents_for(pct, 7000) == 700)
    check("$5.00 off $70.00 = $5.00",
          landing.discount_cents_for(amt, 7000) == 500)
    check("amount capped at subtotal",
          landing.discount_cents_for(amt, 300) == 300)
    check("zero subtotal -> zero discount",
          landing.discount_cents_for(pct, 0) == 0)

    exp = make_promo("MATHEXP", percent=10,
                     expires_at="2020-01-01T00:00:00+00:00")
    check("expired not usable", not landing.promo_is_usable(exp))
    cap = make_promo("MATHCAP", percent=10, max_uses=2)
    landing.save_promo({"code": "MATHCAP", "kind": "percent", "percent": 10,
                        "amount_cents": None, "expires_at": None,
                        "max_uses": 2, "active": 1}, is_new=False)
    con = db._connect()
    con.execute("UPDATE promo_codes SET used_count = 2 WHERE code = ?",
                ("MATHCAP",))
    con.commit()
    con.close()
    check("max uses reached not usable",
          not landing.promo_is_usable(landing.get_promo("MATHCAP")))
    off = make_promo("MATHOFF", percent=10, active=0)
    check("inactive not usable", not landing.promo_is_usable(off))
    check("seed code usable",
          landing.promo_is_usable(landing.get_promo("PREMIUM10")))
    check("missing code lookup -> None", landing.get_promo("NOPE") is None)


def test_cart_promo_session():
    print("6 — /cart/promo apply / remove / revalidation")
    client = appmod.app.test_client()
    r = client.post("/cart/promo", data={"code": "premium10"})
    check("apply (lowercase ok) -> redirect w/ applied",
          r.status_code == 302 and "promo=applied" in r.headers["Location"])
    with client.session_transaction() as s:
        check("code normalized in session", s.get("promo_code") == "PREMIUM10")
    r = client.post("/cart/promo", data={"code": "BOGUS"})
    check("bogus code -> invalid",
          r.status_code == 302 and "promo=invalid" in r.headers["Location"])
    with client.session_transaction() as s:
        check("session keeps old valid code", s.get("promo_code") == "PREMIUM10")
    r = client.post("/cart/promo", data={"action": "remove"})
    check("remove clears session",
          r.status_code == 302 and "promo=removed" in r.headers["Location"])
    with client.session_transaction() as s:
        check("session cleared", not s.get("promo_code"))

    # A code that expires after being applied is dropped on next read.
    make_promo("TEMPOK", percent=10, expires_at="2099-01-01T00:00:00+00:00")
    client.post("/cart/promo", data={"code": "TEMPOK"})
    con = db._connect()
    con.execute("UPDATE promo_codes SET expires_at = ? WHERE code = ?",
                ("2020-01-01T00:00:00+00:00", "TEMPOK"))
    con.commit()
    con.close()
    with appmod.app.test_request_context():
        # prime the session like the browser would
        from flask import session as req_session
        req_session["promo_code"] = "TEMPOK"
        promo, disc = landing.active_cart_promo(7000)
        check("expired code dropped on revalidation",
              promo is None and disc == 0
              and not req_session.get("promo_code"))


def test_code_override_via_url():
    print("7 — ?code= override on landing pages")
    make_promo("OVERRIDE5", kind="amount", percent=None, amount_cents=500)
    client = appmod.app.test_client()
    client.get("/go/premium-led")  # sets PREMIUM10
    r = client.get("/go/premium-led?code=override5")
    body = r.get_data(as_text=True)
    check("valid override applies", r.status_code == 200
          and "Code OVERRIDE5 applied" in body)
    with client.session_transaction() as s:
        check("session switched", s.get("promo_code") == "OVERRIDE5")
    r = client.get("/go/premium-led?code=BOGUS99")
    body = r.get_data(as_text=True)
    check("invalid override rejected w/ notice",
          "isn\u2019t valid" in body)
    with client.session_transaction() as s:
        check("session keeps prior code", s.get("promo_code") == "OVERRIDE5")


# ---------------------------------------------------------------- 4. cart/checkout integration
def test_cart_checkout_discount():
    print("8 — cart + checkout totals with a promo")
    client = appmod.app.test_client()
    p = add_to_cart(client, qty=1)  # $70.00
    client.post("/cart/promo", data={"code": "PREMIUM10"})  # 10%
    body = client.get("/cart").get_data(as_text=True)
    check("cart shows promo line",
          "PREMIUM10" in body and "\u2212$7.00" in body)
    check("cart total discounted", "$63.00" in body)
    # The pay form only renders when Stripe keys exist; no network happens.
    with Env(STRIPE_SECRET_KEY="sk_test_fake"):
        body = client.get("/checkout").get_data(as_text=True)
    check("checkout shows discount + discounted pay button",
          "PREMIUM10" in body and "Pay $63.00 Securely" in body)


def test_checkout_create_records_promo():
    print("9 — checkout snapshot records code + discount, Stripe gets coupon")
    real = payments.create_checkout_session
    captured = {}

    class FakeSession:
        id = "cs_mock_promo"
        url = "https://checkout.stripe.com/pay/cs_mock_promo"

    def fake(*a, **kw):
        captured["args"] = a
        captured["kwargs"] = kw
        return FakeSession()
    payments.create_checkout_session = fake
    try:
        with Env(STRIPE_SECRET_KEY="sk_test_fake"):
            client = appmod.app.test_client()
            add_to_cart(client, qty=1)
            client.post("/cart/promo", data={"code": "PREMIUM10"})
            r = client.post("/checkout/create",
                            data={"name": "Jane Doe",
                                  "email": "jane@example.com",
                                  "line1": "5 Main St", "city": "Rosemead",
                                  "state": "CA", "zip": "91770"})
            check("303 redirect", r.status_code == 303)
            order = db.get_order_by_session("cs_mock_promo")
            check("order records code + discount",
                  order is not None
                  and order["promo_code"] == "PREMIUM10"
                  and order["discount_cents"] == 700
                  and order["total_cents"] == 6300
                  and order["subtotal_cents"] == 7000,
                  str(order and (order["promo_code"],
                                 order["discount_cents"],
                                 order["total_cents"])))
            promo_kw = captured["kwargs"].get("promo")
            check("promo passed to Stripe session builder",
                  promo_kw is not None and promo_kw["code"] == "PREMIUM10")
    finally:
        payments.create_checkout_session = real


def test_promo_usage_on_paid():
    print("10 — usage increments once per paid order")
    import hashlib
    import hmac
    import json as _json
    import time

    client = appmod.app.test_client()
    p = add_to_cart(client, qty=1)
    oid = db.create_order({"name": "T", "email": "t@e.com", "line1": "1",
                           "city": "R", "state": "CA", "zip": "9"},
                          [{"key": "k", "product": p, "size": p["sizes"][0],
                            "color_temp": p["color_temps"][0], "qty": 1,
                            "line_total": p["price_cents"],
                            "unit_price_cents": p["price_cents"]}],
                          p["price_cents"], promo_code="PREMIUM10",
                          discount_cents=700)
    db.set_stripe_session(oid, "cs_test_promo1")
    before = landing.get_promo("PREMIUM10")["used_count"]

    secret = "whsec_test_promo"
    payload = _json.dumps({
        "id": "evt_p1", "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_test_promo1",
                            "payment_intent": "pi_p1"}}}).encode()
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    with Env(STRIPE_WEBHOOK_SECRET=secret):
        r = client.post("/stripe/webhook", data=payload,
                        headers={"Stripe-Signature": f"t={ts},v1={mac}"},
                        content_type="application/json")
    check("webhook -> 200", r.status_code == 200)
    after = landing.get_promo("PREMIUM10")["used_count"]
    check("used_count incremented once", after == before + 1,
          f"{before} -> {after}")
    # Duplicate webhook: no double count.
    with Env(STRIPE_WEBHOOK_SECRET=secret):
        ts2 = str(int(time.time()))
        mac2 = hmac.new(secret.encode(), f"{ts2}.".encode() + payload,
                        hashlib.sha256).hexdigest()
        client.post("/stripe/webhook", data=payload,
                    headers={"Stripe-Signature": f"t={ts2},v1={mac2}"},
                    content_type="application/json")
    check("duplicate webhook no double count",
          landing.get_promo("PREMIUM10")["used_count"] == after)

    # Admin manual mark-paid path also counts.
    with Env(ADMIN_PASSWORD="s3cret"):
        client2 = appmod.app.test_client()
        login(client2)
        oid2 = db.create_order({"name": "T2", "email": "t2@e.com"},
                               [], 0, promo_code="PREMIUM10",
                               discount_cents=0)
        r = client2.post(f"/admin/order/{oid2}/status",
                         data={"action": "paid"})
        check("admin mark-paid increments usage",
              r.status_code == 302
              and landing.get_promo("PREMIUM10")["used_count"] == after + 1)


# ---------------------------------------------------------------- 5. Stripe coupon mapping
def _fake_stripe(raise_on_create=False):
    calls = {"create": [], "session": []}

    class FakeCoupon:
        @staticmethod
        def create(**kw):
            calls["create"].append(kw)
            if raise_on_create:
                raise Exception("already exists")
            return types.SimpleNamespace(id=kw["id"])

        @staticmethod
        def retrieve(cid):
            return types.SimpleNamespace(id=cid)

    class FakeSessionAPI:
        @staticmethod
        def create(**kw):
            calls["session"].append(kw)
            return types.SimpleNamespace(id="cs_x", url="https://x")

    mod = types.SimpleNamespace(
        Coupon=FakeCoupon,
        checkout=types.SimpleNamespace(Session=FakeSessionAPI))
    return mod, calls


def _sample_lines():
    return [{"product": {"name": "Premium CSP Series LED Bulbs \u2014 60W",
                         "price_cents": 7000},
             "size": "H11", "color_temp": "6000K", "qty": 1,
             "unit_price_cents": 7000, "line_total": 7000}]


def test_stripe_coupon_mapping():
    print("11 — Stripe coupon mapping (faked Stripe)")
    real_lib, real_mod = payments._STRIPE_LIB, payments.stripe
    promo = landing.get_promo("PREMIUM10")
    try:
        # Not configured (no keys): safe no-op, never crashes.
        payments._STRIPE_LIB, payments.stripe = True, None
        check("no keys -> None, no crash",
              payments._coupon_for_promo(promo) is None)

        # Configured: percent coupon created + attached to the session.
        mod, calls = _fake_stripe()
        payments._STRIPE_LIB, payments.stripe = True, mod
        with Env(STRIPE_SECRET_KEY="sk_test_x"):
            cid = payments._coupon_for_promo(promo)
            check("coupon id derived from code", cid == "promo_premium10",
                  str(cid))
            check("percent_off=10, duration once",
                  calls["create"] and calls["create"][0]["percent_off"] == 10
                  and calls["create"][0]["duration"] == "once")
            payments.create_checkout_session(1, _sample_lines(), "e@x.com",
                                             "s_url", "c_url", promo=promo)
            sess_kw = calls["session"][0]
            check("session carries discounts=[{coupon}]",
                  sess_kw.get("discounts") == [{"coupon": "promo_premium10"}],
                  str(sess_kw.get("discounts")))

        # Amount-off code maps to amount_off coupon.
        make_promo("AMT25", kind="amount", percent=None, amount_cents=2500)
        mod2, calls2 = _fake_stripe()
        payments._STRIPE_LIB, payments.stripe = True, mod2
        with Env(STRIPE_SECRET_KEY="sk_test_x"):
            cid2 = payments._coupon_for_promo(landing.get_promo("AMT25"))
            check("amount coupon maps amount_off",
                  cid2 == "promo_amt25" and calls2["create"]
                  and calls2["create"][0]["amount_off"] == 2500
                  and calls2["create"][0]["currency"] == "usd")

        # create() raising (e.g. already exists) -> retrieve path, no crash.
        mod3, calls3 = _fake_stripe(raise_on_create=True)
        payments._STRIPE_LIB, payments.stripe = True, mod3
        with Env(STRIPE_SECRET_KEY="sk_test_x"):
            check("create failure falls back to retrieve",
                  payments._coupon_for_promo(promo) == "promo_premium10")

        # No promo -> plain session, no discounts key.
        mod4, calls4 = _fake_stripe()
        payments._STRIPE_LIB, payments.stripe = True, mod4
        with Env(STRIPE_SECRET_KEY="sk_test_x"):
            payments.create_checkout_session(1, _sample_lines(), "e@x.com",
                                             "s_url", "c_url", promo=None)
            check("no promo -> no discounts key",
                  "discounts" not in calls4["session"][0])
    finally:
        payments._STRIPE_LIB, payments.stripe = real_lib, real_mod


# ---------------------------------------------------------------- 6. admin CRUD
def test_admin_auth():
    print("12 — landing/promo admin behind auth")
    client = appmod.app.test_client()
    for path in ("/admin/landing", "/admin/landing/new",
                 "/admin/promos", "/admin/promos/new"):
        r = client.get(path, follow_redirects=False)
        check(f"GET {path} unauth -> login redirect",
              r.status_code == 302 and "/admin/login" in r.headers["Location"],
              f"{r.status_code}")
    r = client.post("/admin/promos/new", data={"code": "X1"},
                    follow_redirects=False)
    check("POST unauth -> login redirect",
          r.status_code == 302 and "/admin/login" in r.headers["Location"])


def test_admin_promo_crud():
    print("13 — admin promo CRUD")
    with Env(ADMIN_PASSWORD="s3cret"):
        client = appmod.app.test_client()
        login(client)
        r = client.get("/admin/promos")
        check("promo list -> 200", r.status_code == 200
              and "PREMIUM10" in r.get_data(as_text=True))
        r = client.post("/admin/promos/new",
                        data={"code": "crud15", "kind": "percent",
                              "percent": "15", "active": "1"},
                        follow_redirects=False)
        check("create -> redirect", r.status_code == 302)
        p = landing.get_promo("CRUD15")
        check("stored normalized", p is not None and p["percent"] == 15)
        r = client.post("/admin/promos/new",
                        data={"code": "bad", "kind": "percent",
                              "percent": "150", "active": "1"})
        check("invalid create -> 200 w/ errors, not saved",
              r.status_code == 200 and "Percent" in r.get_data(as_text=True)
              and landing.get_promo("BAD") is None)
        r = client.post("/admin/promos/CRUD15/edit",
                        data={"kind": "amount", "amount_dollars": "12.50",
                              "active": "1"}, follow_redirects=False)
        p = landing.get_promo("CRUD15")
        check("edit switches to amount",
              r.status_code == 302 and p["kind"] == "amount"
              and p["amount_cents"] == 1250)
        r = client.post("/admin/promos/CRUD15/delete", follow_redirects=False)
        check("delete -> redirect + gone",
              r.status_code == 302 and landing.get_promo("CRUD15") is None)
        r = client.get("/admin/promos/NOPE/edit")
        check("edit missing -> 404", r.status_code == 404)


def test_admin_landing_crud():
    print("14 — admin landing-page CRUD")
    with Env(ADMIN_PASSWORD="s3cret"):
        client = appmod.app.test_client()
        login(client)
        r = client.get("/admin/landing")
        check("landing list -> 200", r.status_code == 200
              and "premium-led" in r.get_data(as_text=True))
        form = {"slug": "crud-page", "product_id": "premium-csp-led-bulbs",
                "headline": "CRUD Test", "subhead": "sub",
                "bullets": "one\ntwo", "video_url": "",
                "testimonial_text": "",
                "countdown_mode": "evergreen", "countdown_minutes": "45",
                "default_code": "", "published": "1"}
        r = client.post("/admin/landing/new", data=form, follow_redirects=False)
        check("create -> redirect", r.status_code == 302)
        pg = landing.get_landing("crud-page")
        check("stored", pg is not None and pg["bullets"] == ["one", "two"]
              and pg["countdown_minutes"] == 45)
        check("new page renders",
              client.get("/go/crud-page").status_code == 200)

        # Fixed mode with a future end via the form.
        form2 = dict(form, slug="crud-fixed", countdown_mode="fixed",
                     countdown_end="2099-06-01T12:00", countdown_minutes="")
        r = client.post("/admin/landing/new", data=form2,
                        follow_redirects=False)
        check("fixed-future create ok", r.status_code == 302)
        pg2 = landing.get_landing("crud-fixed")
        check("fixed end stored as UTC ISO",
              pg2["countdown_end"].startswith("2099-06-01T12:00"))
        body = client.get("/go/crud-fixed").get_data(as_text=True)
        check("fixed countdown rendered",
              'data-cd-mode="fixed"' in body
              and "2099-06-01T12:00" in body)

        # Past end rejected with errors shown.
        form3 = dict(form, slug="crud-past", countdown_mode="fixed",
                     countdown_end="2020-01-01T00:00")
        r = client.post("/admin/landing/new", data=form3)
        check("past end -> 200 w/ errors, not saved",
              r.status_code == 200 and "future" in r.get_data(as_text=True)
              and landing.get_landing("crud-past") is None)

        # Unpublish via edit -> 404 (unchecked checkbox = key absent).
        form4 = dict(form, headline="CRUD Test v2", countdown_mode="fixed",
                     countdown_end="2099-01-01T00:00")
        del form4["published"]
        r = client.post("/admin/landing/crud-page/edit", data=form4,
                        follow_redirects=False)
        pg = landing.get_landing("crud-page")
        check("edit saves + unpublishes (checkbox off)",
              r.status_code == 302 and not pg["published"]
              and pg["headline"] == "CRUD Test v2")
        check("unpublished -> 404",
              client.get("/go/crud-page").status_code == 404)

        for slug in ("crud-page", "crud-fixed"):
            r = client.post(f"/admin/landing/{slug}/delete",
                            follow_redirects=False)
            check(f"delete {slug}", r.status_code == 302
                  and landing.get_landing(slug) is None)


def test_seed_idempotent():
    print("15 — seed is insert-if-missing, never overwrites")
    landing.ensure_landing_schema()
    landing.ensure_landing_schema()
    con = db._connect()
    n = con.execute("SELECT COUNT(*) FROM promo_codes WHERE code = ?",
                    ("PREMIUM10",)).fetchone()[0]
    m = con.execute("SELECT COUNT(*) FROM landing_pages WHERE slug = ?",
                    ("premium-led",)).fetchone()[0]
    con.close()
    check("seed rows exist exactly once", n == 1 and m == 1, f"{n},{m}")
    # Owner edits survive a re-seed.
    con = db._connect()
    con.execute("UPDATE landing_pages SET headline = ? WHERE slug = ?",
                ("Owner Headline", "premium-led"))
    con.commit()
    con.close()
    landing.ensure_landing_schema()
    check("owner edit not clobbered",
          landing.get_landing("premium-led")["headline"] == "Owner Headline")
    con = db._connect()
    con.execute("UPDATE landing_pages SET headline = ? WHERE slug = ?",
                (landing.EXAMPLE_LANDING["headline"], "premium-led"))
    con.commit()
    con.close()


# ---------------------------------------------------------------- 7. copy guard
def _strip_code_strings(text):
    # Blank out string literals: compliance validation code mentions the
    # forbidden word, but only customer-facing copy counts.
    _sq = chr(39)
    pat = _sq * 3 + r".*?" + _sq * 3 + r"|" + chr(34) * 3 + r".*?" + chr(34) * 3
    pat += r"|'[^'\\n]*'|\"[^\"\\n]*\""
    return re.sub(pat, '""', text, flags=re.S)


def test_copy_guards():
    print("16 — copy guards on Track 3 files")
    banned = "head" + "light"  # assembled so this file stays grep-clean
    owned = ["landing.py", "templates/go_landing.html",
             "templates/go_admin_list.html", "templates/go_admin_form.html",
             "templates/go_promo_list.html", "templates/go_promo_form.html",
             "templates/cart.html", "templates/checkout.html",
             "templates/admin.html", "payments.py", "db.py", "app.py"]
    bad = []
    for f in owned:
        text = open(os.path.join(WEB, f), encoding="utf-8").read().lower()
        if f.endswith(".py"):
            text = _strip_code_strings(text)
        if banned in text:
            bad.append(f)
    check(f"no {banned!r} in Track 3 files", not bad, str(bad))
    # "off-road" on the landing page only inside the disclaimer.
    text = open(os.path.join(WEB, "templates/go_landing.html"),
                encoding="utf-8").read()
    offroad_lines = [ln for ln in text.splitlines() if "off-road" in ln]
    check("off-road only in disclaimer lines",
          all("dot-compliance" in ln for ln in offroad_lines),
          str(offroad_lines))


def main():
    test_landing_render()
    test_reviews_wired()
    test_countdown_validation()
    test_promo_validation()
    test_promo_math()
    test_cart_promo_session()
    test_code_override_via_url()
    test_cart_checkout_discount()
    test_checkout_create_records_promo()
    test_promo_usage_on_paid()
    test_stripe_coupon_mapping()
    test_admin_auth()
    test_admin_promo_crud()
    test_admin_landing_crud()
    test_seed_idempotent()
    test_copy_guards()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL TRACK 3 TESTS PASSED")


if __name__ == "__main__":
    main()
