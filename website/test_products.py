#!/usr/bin/env python3
"""Product management tests: admin CRUD, validation guards, uploads,
variation pricing/inventory, delete refusal, and the internal-data leak
test (cost/supplier/SKU/inventory internals must never reach public
pages, cart output, or emails).

Run:  cd website && .venv/bin/python test_products.py
"""

import io
import json
import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="prod_"),
                                      "store.db")
os.environ["FITMENT_DIR"] = tempfile.mkdtemp(prefix="fit_")
os.environ["SEMA_DIR"] = tempfile.mkdtemp(prefix="sema_")
os.environ["ADMIN_PASSWORD"] = "test-admin-pw"
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import emails      # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


def admin_client():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/admin/login", data={"password": "test-admin-pw"},
               follow_redirects=True)
    assert r.status_code == 200, "admin login failed"
    return c


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


_CREATE_N = [0]


def base_form(**kw):
    _CREATE_N[0] += 1
    tag = f"N{_CREATE_N[0]}"
    d = {
        "name": "Test LED Bulbs", "status": "active", "category": "led-bulbs",
        "product_type": "LED Bulb Kit", "tier": "Test",
        "blurb": "A test blurb.",
        "description": "Great for fog and off-road use.",
        "features": "Bright\nEasy install",
        "badge": "", "warranty": "1-year warranty",
        "price": "29.99", "sale_price": "", "cost": "10.00",
        "sku": f"TPM-{tag}", "msku": f"M-TPM-{tag}",
        "barcode": f"1{_CREATE_N[0]:011d}",
        "taxable": "on",
        "weight_oz": "3.5", "length_in": "4", "width_in": "3",
        "height_in": "2", "country_of_origin": "CN", "hs_code": "8512.20",
        "seo_title": "", "seo_description": "",
        "tags": "test, led", "fitment_positions": "Low beam",
        "supplier_name": "Acme Supply", "supplier_sku": "ACME-1",
        "supplier_notes": "restock monthly",
        "notes": "internal admin note",
        "option_groups": "Size: H11, 9005\nColor temp: 6000K",
        "stock": "10", "low_threshold": "2",
        # 2 sizes x 1 temp = 2 variation rows
        "v0_posted": "1", "v0_sku": f"TPM-{tag}-H11", "v0_price": "34.99",
        "v0_compare": "", "v0_cost": "12.00", "v0_qty": "5",
        "v0_track": "on", "v0_low": "2",
        "v1_posted": "1", "v1_sku": f"TPM-{tag}-9005", "v1_price": "",
        "v1_qty": "7", "v1_track": "on", "v1_low": "2",
    }
    d.update(kw)
    return d


def create(c, **kw):
    d = base_form(**kw)
    photo = d.pop("_photo", ("tpm.png", PNG))
    if photo:
        d["photos"] = (io.BytesIO(photo[1]), photo[0])
    return c.post("/admin/products/new", data=d,
                  content_type="multipart/form-data")


def form_vids(c, pid):
    t = c.get(f"/admin/product/{pid}/edit").get_data(as_text=True)
    return dict(re.findall(r'name="v(\d+)_vid" value="(\d+)"', t))


# ---------------------------------------------------------------- 1. create
def test_create():
    print("1 — create product via admin")
    c = admin_client()
    r = create(c)
    check("POST /admin/products/new -> 302", r.status_code == 302,
          r.status_code)
    p = db.get_product("bravo-test-led-bulbs")
    check("product exists with unique slug id", p is not None)
    check("status active", p["status"] == "active")
    check("tags parsed", p["tags"] == ["test", "led"])
    check("fitment positions parsed", p["fitment_positions"] == ["Low beam"])
    check("features parsed", p["features"] == ["Bright", "Easy install"])
    check("2 variations created", len(p["variations"]) == 2)
    check("variation labels in group order",
          [v["label"] for v in p["variations"]] ==
          ["H11 / 6000K", "9005 / 6000K"])
    check("photo saved under static/img/products/<pid>/",
          len(p["images"]) == 1 and p["images"][0]["src"].startswith(
              "/static/img/products/bravo-test-led-bulbs/"))
    src = p["images"][0]["src"]
    check("uploaded file exists where the URL points",
          os.path.isfile(os.path.join(WEB, "static",
                                      src.split("/static/", 1)[1])),
          src)
    check("variation margin computed",
          p["variations"][0]["margin"] == {"dollars": 22.99, "pct": 65.7})
    check("seo falls back to name/blurb",
          p["seo_title"] == "Bravo Test LED Bulbs"
          and p["seo_description"] == "A test blurb.")
    check("active product on public product page",
          c.get("/product/bravo-test-led-bulbs").status_code == 200)
    return p


# ---------------------------------------------------------------- 2. edit
def test_edit(p):
    print("2 — edit product via admin")
    c = admin_client()
    d = base_form()
    d.pop("photos", None)
    t = c.get(f"/admin/product/{p['id']}/edit").get_data(as_text=True)
    for i, vid in form_vids(c, p["id"]).items():
        d[f"v{i}_vid"] = vid
    # Prefill variation SKUs from the rendered form, like a real edit.
    for m in re.finditer(r'name="v(\d+)_sku" value="([^"]*)"', t):
        d[f"v{m.group(1)}_sku"] = m.group(2)
    # keep image-manager rows so the photo survives
    for m in re.finditer(r'name="(imgsrc_\d+|imgpos_\d+)" value="([^"]*)"',
                         t):
        d[m.group(1)] = m.group(2)
    m = re.search(r'name="primary_image" value="([^"]*)" checked', t)
    if m:
        d["primary_image"] = m.group(1)
    d["price"] = "39.99"
    d["blurb"] = "Updated blurb."
    r = c.post(f"/admin/product/{p['id']}/edit", data=d,
               content_type="multipart/form-data")
    check("POST edit -> 302", r.status_code == 302, r.status_code)
    p2 = db.get_product(p["id"])
    check("price updated", p2["price_cents"] == 3999)
    check("blurb updated", p2["blurb"] == "Updated blurb.")
    check("photo kept across edit", len(p2["images"]) == 1)
    check("variation ids stable across edit",
          [v["id"] for v in p2["variations"]] ==
          [v["id"] for v in p["variations"]])
    check("variation skus kept", [v["sku"] for v in p2["variations"]] ==
          [v["sku"] for v in p["variations"]])
    return p2


# ---------------------------------------------------------------- 3/4/5. name guards
def test_name_guards():
    print("3/4/5 — name auto-prefix and forbidden words")
    c = admin_client()
    r = create(c, name="Fog LED Bulbs", sku="TPM-N1")
    p = db.get_product("bravo-fog-led-bulbs")
    check("name auto-prefixed with 'Bravo '",
          p is not None and p["name"] == "Bravo Fog LED Bulbs")
    r = create(c, name="Headlight LED Bulbs", sku="TPM-N2")
    check("name with 'headlight' rejected (400)",
          r.status_code == 400)
    check("rejected product not created",
          db.get_product("bravo-headlight-led-bulbs") is None)
    r = create(c, name="Off-Road LED Bulbs", sku="TPM-N3")
    check("name with 'off-road' rejected (400)",
          r.status_code == 400)
    r = create(c, name="Fog LED Bulbs", sku="TPM-N4", price="0")
    check("zero price rejected (400)", r.status_code == 400)
    r = create(c, name="Dup Sku One", sku="TPM-DUPX")
    check("first of duplicate pair created", r.status_code == 302,
          r.status_code)
    r = create(c, name="Dup Sku Two", sku="TPM-DUPX")
    check("duplicate product SKU rejected (400)",
          r.status_code == 400)
    r = create(c, name="Trail Test", sku="TPM-N5",
               description="fine for off-road use")
    p = db.get_product("bravo-trail-test")
    check("'off-road' allowed in description",
          p is not None and "off-road" in p["description"])
    db.set_status("bravo-trail-test", "archived")


# ------------------------------------------------- variation price + sku x-table
def test_variation_price_and_sku(p):
    print("5b — variation price > 0; SKUs unique across products+variations")
    c = admin_client()
    r = create(c, name="Zero Var Price", v0_price="0")
    check("zero variation override price rejected (400)",
          r.status_code == 400)
    check("rejected product not created",
          db.get_product("bravo-zero-var-price") is None)
    # Variation SKU colliding with an existing PRODUCT sku.
    r = create(c, name="Var Sku Clash", v0_sku=p["sku"])
    check("variation SKU matching a product SKU rejected (400)",
          r.status_code == 400)
    # Product SKU colliding with an existing VARIATION sku.
    vsku = p["variations"][0]["sku"]
    r = create(c, name="Prod Sku Clash", sku=vsku)
    check("product SKU matching a variation SKU rejected (400)",
          r.status_code == 400)
    # MSKU/barcode dupes across tables are allowed (separate namespaces).
    check("cross-table sku_taken sees both tables",
          db.sku_taken(p["variations"][0]["sku"])
          and db.variation_sku_taken(p["sku"]))


# ---------------------------------------------------------------- 6/7. uploads
def test_upload_guards():
    print("6/7 — upload type and size rejection")
    c = admin_client()
    r = create(c, name="Exe Upload", sku="TPM-U1",
               _photo=("evil.exe", b"MZ\x90\x00"))
    check("non-image upload rejected (400)", r.status_code == 400)
    check("rejected upload created nothing",
          db.get_product("bravo-exe-upload") is None)
    big = b"\x00" * (5 * 1024 * 1024 + 1)
    r = create(c, name="Big Upload", sku="TPM-U2",
               _photo=("big.png", big))
    check("over-5MB upload rejected (400)", r.status_code == 400)
    r = create(c, name="Webp Upload", sku="TPM-U3",
               _photo=("ok.webp", b"RIFF....WEBP" + b"\x00" * 32))
    check("webp upload accepted",
          r.status_code == 302
          and db.get_product("bravo-webp-upload") is not None)
    db.set_status("bravo-webp-upload", "archived")


# ---------------------------------------------------------------- 8. delete refusal
def test_delete_refusal(p):
    print("8 — delete refused when an order references the product")
    c = admin_client()
    var = p["variations"][0]
    oid = db.create_order(
        {"name": "T", "email": "t@example.com", "line1": "1",
         "city": "R", "state": "CA", "zip": "9"},
        [{"product": p, "variation": var, "variation_id": var["id"],
          "variation_label": var["label"], "size": "", "color_temp": "",
          "qty": 1, "unit_price_cents": var["effective_price_cents"],
          "line_total": var["effective_price_cents"]}],
        var["effective_price_cents"])
    r = c.post(f"/admin/product/{p['id']}/delete", follow_redirects=True)
    body = r.get_data(as_text=True)
    check("delete route refuses (tells admin to archive)",
          "archive" in body.lower())
    check("product still exists", db.get_product(p["id"]) is not None)
    ok, msg = db.delete_product(p["id"])
    check("db.delete_product refuses", not ok and "archive" in msg.lower(),
          msg)
    return oid


# ---------------------------------------------------------------- 9/10. pricing
def test_cart_pricing(p):
    print("9/10 — cart uses DB price; variation-level server validation")
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    var = [v for v in p["variations"] if v["label"] == "H11 / 6000K"][0]
    # Client tries to smuggle a price — the server must ignore it.
    r = c.post("/cart/add", data={"product_id": p["id"],
                                  "variation_id": str(var["id"]),
                                  "qty": "1", "price": "0.01",
                                  "unit_price_cents": "1"})
    check("add -> redirect", r.status_code == 302)
    body = c.get("/cart").get_data(as_text=True)
    check("cart uses variation override price ($34.99)",
          "$34.99" in body and "$0.01" not in body)
    check("cart shows variation label", "H11 / 6000K" in body)
    # Bogus variation id falls back to a real variation, never a fake price.
    c2 = appmod.app.test_client()
    c2.post("/cart/add", data={"product_id": p["id"],
                               "variation_id": "999999", "qty": "1"})
    body = c2.get("/cart").get_data(as_text=True)
    check("bogus variation id falls back safely",
          p["name"] in body)
    # Edit the product price in the DB; the cart must follow it.
    admin = admin_client()
    d = base_form()
    d.pop("photos", None)
    for i, vid in form_vids(admin, p["id"]).items():
        d[f"v{i}_vid"] = vid
    d["price"] = "49.99"
    # drop the variation override so the product price applies
    d["v1_price"] = ""
    admin.post(f"/admin/product/{p['id']}/edit", data=d,
               content_type="multipart/form-data")
    p3 = db.get_product(p["id"])
    var2 = [v for v in p3["variations"] if v["label"] == "9005 / 6000K"][0]
    c3 = appmod.app.test_client()
    c3.post("/cart/add", data={"product_id": p["id"],
                               "variation_id": str(var2["id"]), "qty": "1"})
    body = c3.get("/cart").get_data(as_text=True)
    check("cart follows DB-edited product price ($49.99)",
          "$49.99" in body, body[-400:])


# ---------------------------------------------------------------- 11/12. inventory + snapshot
def test_variation_inventory_and_snapshot(p):
    print("11/12 — variation inventory decrement + order snapshot")
    var = [v for v in p["variations"] if v["label"] == "H11 / 6000K"][0]
    db.set_variation_stock(var["id"], 8)
    prod = db.get_product(p["id"])
    v = next(x for x in prod["variations"] if x["id"] == var["id"])
    oid = db.create_order(
        {"name": "T", "email": "t@example.com", "line1": "1",
         "city": "R", "state": "CA", "zip": "9"},
        [{"product": prod, "variation": v, "variation_id": v["id"],
          "variation_label": v["label"], "size": "", "color_temp": "",
          "qty": 3, "unit_price_cents": v["effective_price_cents"],
          "line_total": v["effective_price_cents"] * 3}],
        v["effective_price_cents"] * 3)
    db.mark_order_paid(oid, "cs_test_x")
    before = db.get_variation(var["id"])["inventory_qty"]
    check("decrement applies", db.decrement_stock_for_order(oid) is True)
    after = db.get_variation(var["id"])["inventory_qty"]
    check("correct variation decremented by 3", after == before - 3,
          f"{before} -> {after}")
    check("decrement idempotent",
          db.decrement_stock_for_order(oid) is False)
    order = db.get_order(oid)
    item = order["line_items"][0]
    check("order snapshot records variation id + label",
          item.get("variation_id") == var["id"]
          and item.get("variation_label") == "H11 / 6000K",
          str(item))
    # Admin order view shows the variation.
    admin = admin_client()
    body = admin.get("/admin").get_data(as_text=True)
    check("admin orders show the purchased variation",
          "H11 / 6000K" in body)


# ---------------------------------------------------------------- 13. leak test
def test_no_internal_leaks(p):
    print("13 — internal data never leaks publicly")
    c = appmod.app.test_client()
    prod = db.get_product(p["id"])
    pub = db.public_product(prod)
    check("public_product strips all internal fields",
          not [k for k in pub if k in db.INTERNAL_FIELDS])
    pv = db.public_variation(prod["variations"][0])
    check("public_variation strips all internal fields",
          not [k for k in pv if k in db.INTERNAL_FIELDS])
    secrets = [x for x in
               [prod["sku"], prod["msku"], prod["barcode"],
                prod["supplier_name"], prod["supplier_sku"],
                prod["supplier_notes"], prod["notes"], prod["hs_code"],
                prod["variations"][0]["sku"], "$10.00"] if x]
    page = c.get(f"/product/{p['id']}").get_data(as_text=True)
    check("no internals in public product HTML",
          not any(s in page for s in secrets),
          str([s for s in secrets if s in page]))
    var = prod["variations"][0]
    c.post("/cart/add", data={"product_id": p["id"],
                              "variation_id": str(var["id"]), "qty": "1"})
    cart = c.get("/cart").get_data(as_text=True)
    check("no internals in cart HTML",
          not any(s in cart for s in secrets),
          str([s for s in secrets if s in cart]))
    order = db.get_order(db.create_order(
        {"name": "T", "email": "t@example.com", "line1": "1",
         "city": "R", "state": "CA", "zip": "9"},
        [{"product": prod, "variation": var, "variation_id": var["id"],
          "variation_label": var["label"], "size": "", "color_temp": "",
          "qty": 1, "unit_price_cents": var["effective_price_cents"],
          "line_total": var["effective_price_cents"]}],
        var["effective_price_cents"]))
    cust_txt = emails._items_text(order)
    check("customer email shows variation, not internals",
          "H11 / 6000K" in cust_txt
          and not any(s in cust_txt for s in secrets))
    shop = c.get("/shop").get_data(as_text=True)
    check("no internals in shop listing",
          not any(s in shop for s in secrets))
    # Drafts and archived products stay out of public surfaces.
    db.set_status(p["id"], "draft")
    check("draft hidden from public product page",
          c.get(f"/product/{p['id']}").status_code == 404)
    db.set_status(p["id"], "archived")
    check("archived hidden from public listing",
          p["name"] not in c.get("/shop").get_data(as_text=True))
    check("archived excluded from default admin product list",
          not any(x["id"] == p["id"]
                  for x in db.list_products(include_drafts=True)))
    check("archived retained for history",
          db.get_product(p["id"]) is not None)
    db.set_status(p["id"], "active")


def main():
    p = test_create()
    p = test_edit(p)
    test_name_guards()
    test_variation_price_and_sku(p)
    test_upload_guards()
    test_delete_refusal(p)
    test_cart_pricing(p)
    test_variation_inventory_and_snapshot(p)
    test_no_internal_leaks(p)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL PRODUCT TESTS PASSED")


if __name__ == "__main__":
    main()
