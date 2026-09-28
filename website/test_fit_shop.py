#!/usr/bin/env python3
"""Fitment page shop-by-position tests: series rows, AJAX add, kit builder.

- Vehicle page renders compact series rows per position (not full cards),
  each row carrying the exact series+size variation id.
- Options are filtered: every row under a position must offer that
  position's bulb size (via its variation's Size option).
- Default kit pick is Premium when present, else the first option.
- /api/cart/add puts the correct variant (right Size) in the cart.
- /api/cart/add-kit creates one line item per position with correct sizes,
  and the reported total matches server-side prices.
- Forbidden wording: no "headlight" in the fitment template/JS.

Run:  cd website && .venv/bin/python test_fit_shop.py
"""
import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="fitshop_"),
                                      "store.db")
os.environ["FITMENT_DIR"] = os.path.join(os.path.dirname(WEB), "fitment")
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL"):
    os.environ.pop(_v, None)

import app as appmod  # noqa: E402
import db  # noqa: E402
from fitment_loader import norm_size  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" -- {extra}" if extra and not cond else ""))


def client():
    return appmod.app.test_client()


def pos_block(html, label):
    """Raw HTML of the position block for a label like 'Low Beam'."""
    m = re.search(r'<div class="pos-block" data-position="%s">(.*?)(?=<div class="pos-block"|<div class="flash")'
                  % re.escape(label), html, re.S)
    return m.group(1) if m else ""


def rows_in(block):
    return re.findall(r'<div class="series-row(.*?)</div>\s*</div>', block, re.S)


def test_series_rows():
    c = client()
    r = c.get("/fit/2010/toyota/prius")
    html = r.get_data(as_text=True)
    check("fit page 200", r.status_code == 200)
    check("series rows rendered", 'class="series-row' in html)
    check("no old full product cards", 'class="pos-prod"' not in html)
    check("kit bar present", 'id="kitbar"' in html)
    check("add-one buttons present", 'class="btn btn-sm add-one"' in html)

    for label, size in (("Low Beam", "H11"), ("High Beam", "9005"),
                        ("Fog Light", "H11")):
        block = pos_block(html, label)
        rows = rows_in(block)
        check(f"{label}: has option rows", len(rows) >= 2,
              f"found {len(rows)}")
        sizes = set(re.findall(r'data-size="([^"]+)"', block))
        check(f"{label}: all rows offer {size}",
              sizes and all(norm_size(s) == norm_size(size) for s in sizes),
              f"sizes={sizes}")
        # every row's variation id really is that size in the DB
        bad = []
        for pid, vid in re.findall(r'data-pid="([^"]+)" data-vid="(\d+)"',
                                   block):
            p = db.get_product(pid)
            v = next((x for x in (p.get("variations") or [])
                      if str(x["id"]) == vid), None)
            vsz = (v.get("option_values") or {}).get("Size", "") if v else ""
            if norm_size(vsz) != norm_size(size):
                bad.append((pid, vid, vsz))
        check(f"{label}: variation ids resolve to {size} in DB", not bad,
              f"bad={bad}")
        # name links still carry ?size= preselect
        check(f"{label}: rows link with ?size=",
              f"?size={size}" in block.replace("%20", " "))


def test_default_pick():
    c = client()
    html = c.get("/fit/2010/toyota/prius").get_data(as_text=True)
    block = pos_block(html, "Low Beam")
    sel = re.findall(r'<div class="series-row sel".*?data-name="([^"]+)"',
                     block, re.S)
    check("low beam has exactly one default pick", len(sel) == 1,
          f"sel={sel}")
    has_premium = "premium" in block.lower()
    check("default is Premium when available",
          (not has_premium) or any("remium" in s for s in sel),
          f"sel={sel}")


def _first_row(block):
    m = re.search(r'data-pid="([^"]+)" data-vid="(\d+)"[^>]*data-price="(\d+)"'
                  r'[^>]*data-size="([^"]+)"', block)
    return m.groups() if m else (None, None, None, None)


def test_ajax_add():
    c = client()
    html = c.get("/fit/2010/toyota/prius").get_data(as_text=True)
    pid, vid, price, size = _first_row(pos_block(html, "Low Beam"))
    check("found a low-beam row to add", bool(pid and vid))
    r = c.post("/api/cart/add",
               json={"product_id": pid, "variation_id": vid, "qty": 1})
    j = r.get_json()
    check("api add 200 + ok", r.status_code == 200 and j.get("ok"),
          f"status={r.status_code} j={j}")
    check("api add returns size", j.get("size") == size, f"j={j}")
    check("api add cart_count", j.get("cart_count") == 1)
    with c.session_transaction() as s:
        cart = s.get("cart", {})
    key = f"{pid}::v{vid}"
    check("cart holds the line", key in cart and cart[key]["qty"] == 1)
    # verify size + price through the real server-side paths
    p = db.get_product(pid)
    var = next(v for v in p["variations"] if str(v["id"]) == str(vid))
    check("line size is the position size",
          (var["option_values"] or {}).get("Size") == size)
    check("unit price matches server price",
          j.get("unit_cents") == db.variation_sell_price(p, var))
    check("price matches row price", j.get("unit_cents") == int(price))


def test_kit_add():
    c = client()
    html = c.get("/fit/2010/toyota/prius").get_data(as_text=True)
    items, expect_total = [], 0
    for label in ("Low Beam", "High Beam", "Fog Light"):
        pid, vid, price, size = _first_row(pos_block(html, label))
        if not (pid and vid):
            continue
        items.append({"product_id": pid, "variation_id": vid, "qty": 1,
                      "size": size})
        expect_total += int(price)
    check("kit has 3 positions", len(items) == 3, f"n={len(items)}")
    r = c.post("/api/cart/add-kit", json={"items": items})
    j = r.get_json()
    check("kit add 200 + ok", r.status_code == 200 and j.get("ok"),
          f"status={r.status_code} j={j}")
    check("kit added N lines", j.get("added") == len(items), f"j={j}")
    check("kit total correct", j.get("total_cents") == expect_total,
          f"got={j.get('total_cents')} want={expect_total}")
    with c.session_transaction() as s:
        cart = s.get("cart", {})
    check("cart has one line per position", len(cart) == len(items),
          f"lines={len(cart)}")
    sizes_ok = True
    for it in items:
        key = f"{it['product_id']}::v{it['variation_id']}"
        if key not in cart:
            sizes_ok = False
            break
        p = db.get_product(it["product_id"])
        var = next(v for v in p["variations"]
                   if str(v["id"]) == str(it["variation_id"]))
        if norm_size((var["option_values"] or {}).get("Size", "")) != \
                norm_size(it["size"]):
            sizes_ok = False
            break
    check("each kit line is the right size variant", sizes_ok)


def test_api_rejects_bad():
    c = client()
    r = c.post("/api/cart/add",
               json={"product_id": "nope", "variation_id": "1"})
    check("bad product -> 400", r.status_code == 400)
    html = c.get("/fit/2010/toyota/prius").get_data(as_text=True)
    pid, vid, _, _ = _first_row(pos_block(html, "Low Beam"))
    r = c.post("/api/cart/add",
               json={"product_id": pid, "variation_id": "999999"})
    check("bad variation -> 400", r.status_code == 400)
    r = c.post("/api/cart/add-kit", json={"items": []})
    check("empty kit -> 400", r.status_code == 400)


def test_wording():
    src = open(os.path.join(WEB, "templates", "_fitment_results.html")).read()
    check("no 'headlight' in fitment template",
          "headlight" not in src.lower())
    check("BRAVO LEDS all caps on vehicle page",
          "BRAVO LEDS" in client().get("/fit/2010/toyota/prius")
          .get_data(as_text=True))


def main():
    print("fitment shop-by-position tests")
    test_series_rows()
    test_default_pick()
    test_ajax_add()
    test_kit_add()
    test_api_rejects_bad()
    test_wording()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
