#!/usr/bin/env python3
"""Rebrand tests: the website IS Bravo LEDs; 626 LEDs appears only as the
physical-store attribution ("Bravo LEDs by 626 LEDs: Automotive Lighting
Parts Store — Rosemead, CA" and the store address blocks). The header/footer
logo is the chevron logo image (static/img/logo-bravo-led.png); the favicon
is the chevron mark (static/img/favicon-chevron.png).

Run:  cd website && .venv/bin/python test_rebrand.py
"""

import os
import shutil
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

TMP = tempfile.mkdtemp(prefix="rebrand_test_")
os.environ["STORE_DB"] = os.path.join(TMP, "store.db")
os.environ["FITMENT_DIR"] = os.path.join(TMP, "fitment")  # absent -> seed
os.environ["SEMA_DIR"] = os.path.join(TMP, "sema")

for _m in ("db", "catalog", "content", "landing", "fitment_loader",
           "emails", "app"):
    sys.modules.pop(_m, None)

import catalog as catalogmod  # noqa: E402
import content as contentmod  # noqa: E402
import landing as landingmod  # noqa: E402
import emails as emailsmod    # noqa: E402
import app as appmod          # noqa: E402

contentmod.register_content_routes(appmod.app)
landingmod.register_landing_routes(appmod.app)
client = appmod.app.test_client()

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if not cond and extra:
        print(f"  FAIL DETAIL [{name}]: {extra}")


def test_brand_strings():
    html = client.get("/").data.decode()
    check("home mentions Bravo LEDs", "Bravo LEDs" in html)
    check("footer attribution present",
          "Bravo LEDs by 626 LEDs" in html, html[:500])
    check("header logo img present",
          '<img src="/static/img/logo-bravo-led.png" alt="Bravo LEDs"' in html)
    check("logo img links home",
          '<a class="logo" href="/">' in html)
    check("old text wordmark removed",
          '<span class="logo-name">' not in html)
    check("no 626 in header logo",
          "626" not in html.split('<a class="logo" href="/">')[1].split("</a>")[0])
    check("chevron favicon linked",
          '<link rel="icon" href="/static/img/favicon-chevron.png">' in html)
    check("copyright is Bravo LEDs", "© 2026 Bravo LEDs" in html)
    check("logo png exists on disk",
          os.path.isfile(os.path.join(WEB, "static", "img", "logo-bravo-led.png")))
    check("favicon png exists on disk",
          os.path.isfile(os.path.join(WEB, "static", "img", "favicon-chevron.png")))


def test_product_names():
    prods = catalogmod.PRODUCTS
    check("catalog non-empty", len(prods) > 0)
    bad_prefix = [p["id"] for p in prods
                  if not p["name"].startswith("Bravo ")]
    check("all product names start with 'Bravo '",
          not bad_prefix, str(bad_prefix[:5]))
    bad_626 = [p["id"] for p in prods if "626" in p["name"]]
    check("no '626' in any product name", not bad_626, str(bad_626[:5]))
    bad_offroad = [p["id"] for p in prods
                   if "off-road" in p["name"].lower()]
    check("no 'off-road' in product names",
          not bad_offroad, str(bad_offroad[:5]))
    cats = catalogmod.CATEGORIES
    bad_cat = [c for c in cats.values() if c["name"].startswith("Bravo ")]
    check("category names NOT Bravo-prefixed", not bad_cat)


def test_product_page_brand():
    html = client.get("/product/basic-led-bulbs").data.decode()
    check("product page 200", client.get("/product/basic-led-bulbs").status_code == 200)
    check("product page title has Bravo LEDs", "Bravo LEDs" in html)
    check("product page shows Bravo product name",
          "Bravo Basic LED Bulbs" in html)


def test_email_brand():
    order = {
        "id": 1, "customer_name": "Test", "customer_email": "t@example.com",
        "total_cents": 2500, "addr_line1": "1 Main St", "addr_line2": "",
        "addr_city": "Rosemead", "addr_state": "CA", "addr_zip": "91170",
        "stripe_session_id": "cs_test_1", "tracking_number": None,
        "line_items": [{"qty": 1, "name": "Bravo Basic LED Bulbs",
                        "size": "H11", "color_temp": "6000K",
                        "line_total_cents": 2500}],
    }
    captured = {}
    orig = emailsmod.send_email_channel
    emailsmod.send_email_channel = lambda to, subj, body: captured.update(
        to=to, subj=subj, body=body) or True
    try:
        emailsmod.notify_customer_order_paid(order)
        check("confirmation subject is Bravo LEDs",
              "Bravo LEDs" in captured["subj"], captured["subj"])
        check("confirmation body is Bravo LEDs",
              "Bravo LEDs" in captured["body"])
        check("confirmation body has no 626 LEDs brand",
              "626 LEDs" not in captured["body"], captured["body"][-200:])
        emailsmod.notify_customer_shipped(order)
        check("shipped subject is Bravo LEDs",
              "Bravo LEDs" in captured["subj"])
        emailsmod.notify_owner_new_order(order)
        check("owner subject is Bravo LEDs",
              "Bravo LEDs" in captured["subj"], captured["subj"])
    finally:
        emailsmod.send_email_channel = orig
    os.environ.pop("ORDER_NOTIFY_EMAIL", None)
    check("owner notify default still test address",
          emailsmod.order_notify_email() == "zoltar.works@gmail.com",
          emailsmod.order_notify_email())


def test_landing_brand():
    html = client.get("/go/premium-led").data.decode()
    check("landing page 200", client.get("/go/premium-led").status_code == 200)
    check("landing page branded Bravo LEDs", "Bravo LEDs" in html)


def test_standing_rules():
    home = client.get("/").data.decode().lower()
    prod = client.get("/product/basic-led-bulbs").data.decode().lower()
    check("no 'headlight' on homepage", "headlight" not in home)
    check("no 'headlight' on product page", "headlight" not in prod)
    dot = client.get("/dot-compliance").data.decode()
    check("dot page still explains FMVSS", "FMVSS" in dot)


def main():
    try:
        test_brand_strings()
        test_product_names()
        test_product_page_brand()
        test_email_brand()
        test_landing_brand()
        test_standing_rules()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
