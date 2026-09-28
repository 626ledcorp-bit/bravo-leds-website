#!/usr/bin/env python3
"""Interior LED kit tests: data integrity, rendering, cart behavior, and
forbidden wording.

- Data: every kit has map+dome positions with quantities, bulb sizes,
  location descriptions, and estimated flags; kit ids are unique.
- Rendering: kit pages return 200 with contents; unknown kits 404; the
  fitment results page suggests the kit when one exists for the vehicle.
- Cart: add-to-cart works for kits at the server-side price; client
  tampering (bogus variation id) cannot change the price; the kit line
  survives into an order snapshot.
- Forbidden wording: the word "headlight" must not appear in any new
  kit content (data, templates, or kit copy).

Run:  cd website && .venv/bin/python test_interior_kits.py
"""
import json
import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="kit_"),
                                      "store.db")
os.environ["FITMENT_DIR"] = os.path.join(
    os.path.dirname(WEB), "fitment")
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import kits        # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


KIT = None  # a real kit from the data file


def load_data():
    global KIT
    path = os.path.join(WEB, "kit_data", "interior_kits.json")
    data = json.load(open(path))
    allkits = data.get("kits", [])
    check("interior_kits.json loads with kits", len(allkits) > 0)
    ids = [k.get("kit_id") for k in allkits]
    check("kit ids unique", len(ids) == len(set(ids)))
    ok = True
    for k in allkits:
        poss = {i["position"] for i in k.get("items", [])}
        if not ({"map_light", "dome_light"} <= poss):
            ok = False
        for i in k.get("items", []):
            if not (i.get("position") and i.get("bulb_size")
                    and i.get("quantity") and i.get("location_desc")):
                ok = False
            if i.get("estimated") is not True:
                ok = False
    check("every kit has map+dome with qty/size/location/estimated",
          ok)
    check("kit ids look like kit-<year>-<make>-<model>",
          all(re.match(r"^kit-\d{4}-[a-z0-9-]+$", i) for i in ids))
    KIT = next((k for k in allkits if k["kit_id"] == "kit-2019-ram-1500"),
               allkits[0])
    check("sample kit selected", KIT is not None)


def test_kit_page():
    print("kit page rendering")
    client = appmod.app.test_client()
    url = f"/interior-kit/{KIT['year']}/{kits.slugify(KIT['make'])}/" \
          f"{kits.slugify(KIT['model'])}"
    r = client.get(url)
    body = r.get_data(as_text=True)
    check("GET kit page -> 200", r.status_code == 200)
    check("kit name shown", KIT["make"] in body and KIT["model"] in body)
    check("price shown", "$34.99" in body)
    check("kit contents listed",
          all(i["bulb_size"] in body and i["location_desc"] in body
              for i in KIT["items"]))
    check("estimated note shown", "estimat" in body.lower())
    check("add-to-cart form posts kit id",
          f'value="{KIT["kit_id"]}"' in body)
    check("disclaimer present", "off-road" in body.lower())
    r = client.get("/interior-kit/1999/ford/pinto")
    check("unknown kit -> 404", r.status_code == 404)


def test_fitment_banner():
    print("fitment results kit suggestion")
    client = appmod.app.test_client()
    r = client.get(f"/fitment?year={KIT['year']}&make={KIT['make']}"
                   f"&model={KIT['model']}")
    body = r.get_data(as_text=True)
    if r.status_code == 200:
        url = kits.kit_url(KIT)
        check("kit banner links to kit page", url in body, url)
        check("banner mentions complete kit",
              "Complete Interior LED Kit" in body)
    else:
        check("fitment page reachable for kit vehicle",
              False, f"status {r.status_code}")
    # A vehicle with no kit must not show the banner.
    r = client.get("/fitment?year=2019&make=Honda&model=Ridgeline")
    body = r.get_data(as_text=True)
    if r.status_code == 200:
        check("no banner without a kit",
              "Complete Interior LED Kit for your" not in body)
    else:
        check("seed/demo fitment page reachable", False,
              f"status {r.status_code}")


def test_cart():
    print("kit cart behavior")
    client = appmod.app.test_client()
    pid = KIT["kit_id"]
    # normal add
    r = client.post("/cart/add", data={"product_id": pid,
                                       "variation_id": "kit-var",
                                       "qty": "2"})
    check("POST /cart/add -> redirect to cart",
          r.status_code in (301, 302, 303))
    r = client.get("/cart")
    body = r.get_data(as_text=True)
    check("kit line in cart", "Complete Interior LED Kit" in body)
    check("server-side price x2 = $69.98", "$69.98" in body, body[-500:])
    # tampered variation id must not change the price
    client2 = appmod.app.test_client()
    r = client2.post("/cart/add", data={"product_id": pid,
                                        "variation_id": "bogus",
                                        "qty": "1"})
    lines, subtotal = None, None
    with client2.session_transaction() as s:
        cart = s.get("cart", {})
    # resolve through the real pipeline
    import flask
    with appmod.app.test_request_context():
        flask.session["cart"] = cart
        lines, subtotal = appmod.cart_detailed()
    check("bogus variation still priced at kit price",
          subtotal == kits.KIT_PRICE_CENTS, f"subtotal={subtotal}")
    # unknown kit id 404s
    r = client.post("/cart/add", data={"product_id": "kit-1999-ford-pinto"})
    check("unknown kit add -> 404", r.status_code == 404)
    # order snapshot keeps the kit line
    oid = db.create_order({"name": "T", "email": "t@t.t", "line1": "a",
                           "city": "c", "state": "s", "zip": "z"},
                          lines, subtotal)
    order = db.get_order(oid)
    snap = order["line_items"][0]
    check("order snapshot has kit line",
          snap["product_id"] == pid
          and snap["price_cents"] == kits.KIT_PRICE_CENTS)


def test_forbidden_word():
    print("forbidden wording")
    paths = [
        os.path.join(WEB, "kit_data", "interior_kits.json"),
        os.path.join(WEB, "templates", "interior_kit.html"),
        os.path.join(WEB, "templates", "fitment.html"),
        os.path.join(WEB, "kits.py"),
    ]
    bad = []
    for p in paths:
        text = open(p, encoding="utf-8").read()
        if re.search(r"headlight", text, re.IGNORECASE):
            bad.append(p)
    check("no 'headlight' in kit content", not bad, ", ".join(bad))


def main():
    load_data()
    test_kit_page()
    test_fitment_banner()
    test_cart()
    test_forbidden_word()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
