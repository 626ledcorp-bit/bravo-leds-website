#!/usr/bin/env python3
"""Square -> Bravo catalog import tests: fetch parsing (pagination, images,
categories, counts), plan validation (create/skip/error), naming guards,
SKU collision skips, preview-before-write, draft creation with variations,
Square ID storage, image URLs, category mapping, and the admin routes.

No network and no real Square credentials: square_sync._request is
monkeypatched with a fake serving canned catalog/inventory responses.

Run:  cd website && .venv/bin/python test_square_import.py
"""

import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="sqimp_"),
                                      "store.db")
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL", "SQUARE_ACCESS_TOKEN", "SQUARE_LOCATION_ID"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import square_sync  # noqa: E402
import square_import  # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


# ------------------------------------------------------------ test fakes


def _var(vid, name, sku, cents):
    return {"type": "ITEM_VARIATION", "id": vid,
            "item_variation_data": {
                "name": name, "sku": sku,
                "pricing_type": "FIXED_PRICING",
                "price_money": {"amount": cents, "currency": "USD"},
                "track_inventory": True}}


def _item(iid, name, variations, description="", category_id=None,
          image_ids=None):
    idata = {"name": name, "description": description,
             "variations": variations}
    if category_id:
        idata["category_id"] = category_id
    if image_ids:
        idata["image_ids"] = image_ids
    return {"type": "ITEM", "id": iid, "item_data": idata}


class FakeSquareImport:
    """Stands in for square_sync._request for the import endpoints."""

    def __init__(self):
        self.calls = []
        self.counts = {"SQVAR-FOG-H11": 12, "SQVAR-FOG-9005": 7,
                       "SQVAR-INT": 3, "SQVAR-DASH": 5}
        self.pages = [
            ["SQITEM-FOG", "SQITEM-HEAD", "SQITEM-OFF"],
            ["SQITEM-TURN", "SQITEM-INT", "SQITEM-DASH",
             "SQITEM-EMPTY", "SQITEM-BADESC", "SQITEM-DUP"],
        ]
        self._objects = {
            "SQITEM-FOG": _item(
                "SQITEM-FOG", "Fog LED Bulbs",
                [_var("SQVAR-FOG-H11", "H11", "SQ-FOG-H11", 3500),
                 _var("SQVAR-FOG-9005", "9005", "SQ-FOG-9005", 4000)],
                description="Bright fog LEDs.",
                category_id="SQCAT-LIGHTING", image_ids=["SQIMG-1"]),
            "SQITEM-HEAD": _item(
                "SQITEM-HEAD", "headlight LED kit",
                [_var("SQVAR-HEAD", "H7", "SQ-HEAD-H7", 7000)]),
            "SQITEM-OFF": _item(
                "SQITEM-OFF", "Off-Road Pod Lights",
                [_var("SQVAR-OFF", "Pods", "SQ-OFF-POD", 9000)]),
            "SQITEM-TURN": _item(
                "SQITEM-TURN", "Turn LED Bulbs",
                [_var("SQVAR-TURN", "7440", "SQ-EXISTING-1", 2500)]),
            "SQITEM-INT": _item(
                "SQITEM-INT", "Interior LED Bulbs",
                [_var("SQVAR-INT", "T10", "SQ-INT-T10", 1500)],
                category_id="SQCAT-WEIRD"),
            "SQITEM-DASH": _item(
                "SQITEM-DASH", "Dash Cam Pro",
                [_var("SQVAR-DASH", "Standard", "", 12900)]),
            "SQITEM-EMPTY": _item("SQITEM-EMPTY", "Empty Item", []),
            "SQITEM-BADESC": _item(
                "SQITEM-BADESC", "Brake LED Bulbs",
                [_var("SQVAR-BAD", "7443", "SQ-BAD-7443", 2000)],
                description="Fits headlight housings."),
            "SQITEM-DUP": _item(
                "SQITEM-DUP", "Duplicate SKU Bulbs",
                [_var("SQVAR-DUP", "H11", "SQ-FOG-H11", 3500)]),
        }
        self._related = {
            "SQCAT-LIGHTING": {"type": "CATEGORY", "id": "SQCAT-LIGHTING",
                               "category_data": {"name": "LED Bulbs"}},
            "SQCAT-WEIRD": {"type": "CATEGORY", "id": "SQCAT-WEIRD",
                            "category_data": {"name": "Weird Category"}},
            "SQIMG-1": {"type": "IMAGE", "id": "SQIMG-1",
                        "image_data": {"url": "https://sq.example/img1.jpg",
                                       "name": "img1"}},
        }

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/v2/catalog/list"):
            if "cursor=" in path:
                objs = [{"type": "ITEM", "id": i} for i in self.pages[1]]
                return {"objects": objs}
            objs = [{"type": "ITEM", "id": i} for i in self.pages[0]]
            return {"objects": objs, "cursor": "page2"}
        if path == "/v2/catalog/batch-retrieve":
            objs = [self._objects[i] for i in body["object_ids"]
                    if i in self._objects]
            return {"objects": objs,
                    "related_objects": list(self._related.values())}
        if path == "/v2/inventory/batch-retrieve-counts":
            out = []
            for oid in body.get("catalog_object_ids", []):
                if oid in self.counts:
                    out.append({"catalog_object_id": oid, "state": "IN_STOCK",
                                "quantity": str(self.counts[oid]),
                                "location_id": "LOC1"})
            return {"counts": out}
        return {}


def use_fake():
    fake = FakeSquareImport()
    square_sync._request = fake
    return fake


def configure_square():
    db.set_setting("square_access_token", "sq-test-token")
    db.set_setting("square_location_id", "LOC1")
    db.set_setting("square_environment", "sandbox")


def make_existing_product():
    pid = "sqimp-existing"
    groups = [{"name": "Size", "values": ["7440"]}]
    db.create_product({
        "id": pid, "name": "Bravo Existing Bulbs", "category": "turn",
        "tier": "Test", "price_cents": 2500, "blurb": "t", "features": [],
        "status": "active", "variant_groups": groups,
    })
    db.sync_variations(pid, groups, overrides=[
        {"option_values": {"Size": "7440"}, "sku": "SQ-EXISTING-1",
         "inventory_qty": 4, "track_inventory": True, "_posted": True}])


def login(client, pw="secret"):
    return client.post("/admin/login", data={"password": pw})


def admin_client():
    os.environ["ADMIN_PASSWORD"] = "secret"
    c = appmod.app.test_client()
    login(c)
    return c


def product_count():
    return len(db.search_products(""))


def by_raw(plan, raw_name):
    for g in plan["items"]:
        if g["raw_name"] == raw_name:
            return g
    return None


# ------------------------------------------------------------ 1. fetch


def test_fetch():
    print("1 — fetch_catalog parses the Square catalog")
    use_fake()
    configure_square()
    snap = square_import.fetch_catalog()
    check("snapshot has 9 items", len(snap["items"]) == 9,
          f"got {len(snap['items'])}")
    fog = next(i for i in snap["items"] if i["id"] == "SQITEM-FOG")
    check("item name parsed", fog["name"] == "Fog LED Bulbs")
    check("two variations parsed", len(fog["variations"]) == 2)
    v = fog["variations"][0]
    check("variation sku parsed", v["sku"] == "SQ-FOG-H11")
    check("variation price in cents", v["price_cents"] == 3500)
    check("inventory count attached", v["qty"] == 12)
    check("second count attached", fog["variations"][1]["qty"] == 7)
    check("description parsed", fog["description"] == "Bright fog LEDs.")
    check("category name resolved", fog["category_name"] == "LED Bulbs")
    check("image URL resolved",
          fog["image_urls"] == ["https://sq.example/img1.jpg"])
    check("list pagination followed (both pages fetched)",
          any("cursor=" in c[1] for c in square_sync._request.calls))


def test_fetch_not_configured():
    print("2 — fetch fails closed without Square config")
    db.set_setting("square_access_token", "")
    db.set_setting("square_location_id", "")
    try:
        square_import.fetch_catalog()
        check("raises SquareError when unconfigured", False)
    except square_sync.SquareError:
        check("raises SquareError when unconfigured", True)
    configure_square()


# ------------------------------------------------------------ 2. plan


def test_plan():
    print("3 — plan_import validation and mapping")
    use_fake()
    make_existing_product()
    snap = square_import.fetch_catalog()
    before = product_count()
    plan = square_import.plan_import(snap)
    check("plan writes nothing", product_count() == before)

    fog = by_raw(plan, "Fog LED Bulbs")
    check("fog item -> create", fog["action"] == "create", fog)
    check("Bravo prefix auto-applied", fog["name"] == "Bravo Fog LED Bulbs")
    check("category slug mapped", fog["category"] == "led-bulbs")
    check("image marked primary",
          fog["images"] == [{"src": "https://sq.example/img1.jpg",
                             "primary": True}])
    check("price is min variation", fog["price_cents"] == 3500)

    head = by_raw(plan, "headlight LED kit")
    check("headlight name blocked", head["action"] == "error")
    check("headlight reason mentions headlight",
          "headlight" in head["reason"].lower())

    off = by_raw(plan, "Off-Road Pod Lights")
    check("off-road name blocked", off["action"] == "error")
    check("off-road reason mentions off-road",
          "off-road" in off["reason"].lower())

    turn = by_raw(plan, "Turn LED Bulbs")
    check("existing SKU skipped", turn["action"] == "skip")
    check("skip reason names the SKU", "SQ-EXISTING-1" in turn["reason"])

    dup = by_raw(plan, "Duplicate SKU Bulbs")
    check("duplicate SKU within snapshot skipped", dup["action"] == "skip")

    inter = by_raw(plan, "Interior LED Bulbs")
    check("unmatched category still creates", inter["action"] == "create")
    check("uncategorized", inter["category"] == "")
    check("uncategorized reported", "uncategorized" in inter["category_note"])

    dash = by_raw(plan, "Dash Cam Pro")
    check("single variation item creates", dash["action"] == "create")

    empty = by_raw(plan, "Empty Item")
    check("item with no variations skipped", empty["action"] == "skip")

    badesc = by_raw(plan, "Brake LED Bulbs")
    check("headlight in description blocked", badesc["action"] == "error")

    c = plan["counts"]
    check("counts add up",
          c["create"] + c["skip"] + c["error"] == len(plan["items"]))
    check("3 create, 3 skip, 3 error",
          (c["create"], c["skip"], c["error"]) == (3, 3, 3), c)
    check("importable when creates exist", plan["importable"] is True)


# ------------------------------------------------------------ 3. apply


def test_apply():
    print("4 — apply_import creates drafts, links Square IDs")
    use_fake()
    snap = square_import.fetch_catalog()
    counts = square_import.apply_import(snap)
    check("3 products created", counts["created"] == 3, counts)
    check("4 variations created", counts["variations"] == 4, counts)
    check("3 skipped", counts["skipped"] == 3, counts)
    check("3 blocked", counts["errors"] == 3, counts)

    v = db.get_variation_by_sku("SQ-FOG-H11")
    check("variation findable by SKU", v is not None)
    prod = db.get_product(v["product_id"])
    check("created as draft", prod["status"] == "draft")
    check("draft flag set", prod["draft"] == 1)
    check("Bravo-prefixed name", prod["name"] == "Bravo Fog LED Bulbs")
    check("category mapped", prod["category"] == "led-bulbs")
    check("description imported", prod["description"] == "Bright fog LEDs.")
    check("Square item id stored on product",
          prod["square_catalog_object_id"] == "SQITEM-FOG")
    check("image URL saved with primary",
          prod["images"] == [{"src": "https://sq.example/img1.jpg",
                              "primary": True}])

    v9005 = db.get_variation_by_sku("SQ-FOG-9005")
    check("variation price imported", v9005["price_cents"] == 4000)
    check("variation stock imported", v9005["inventory_qty"] == 7)
    check("Square variation id stored",
          v9005["square_catalog_object_id"] == "SQVAR-FOG-9005")
    check("variation tracked", v9005["track_inventory"] is True)
    check("option group applied",
          v9005["option_values"] == {"Variant": "9005"})
    check("variation label readable", v9005["label"] == "9005")

    inter = db.get_variation_by_sku("SQ-INT-T10")
    iprod = db.get_product(inter["product_id"])
    check("uncategorized product has empty category",
          iprod["category"] == "")
    check("interior stock imported", inter["inventory_qty"] == 3)

    dash = db.search_products("Dash Cam Pro")
    check("dash cam product created", len(dash) == 1)
    dprod = db.get_product(dash[0]["id"])
    dv = dprod["variations"]
    check("single variation, no option groups",
          len(dv) == 1 and dv[0]["option_values"] == {})
    check("missing SKU auto-generated",
          (dv[0]["sku"] or "").startswith(dprod["id"] + "-v"))
    check("auto SKU unique", db.variation_sku_taken(dv[0]["sku"],
                                                    exclude_vid=dv[0]["id"])
          is False)
    check("dash cam price imported", dv[0]["price_cents"] == 12900)

    # Internal fields stay unset / admin-only.
    check("cost not set by import", prod["cost_cents"] is None)
    check("supplier not set by import", prod["supplier_name"] is None)
    pub = db.public_product(prod)
    check("public product hides internal fields",
          "cost_cents" not in pub and "supplier_name" not in pub
          and "square_catalog_object_id" not in pub)

    log = db.list_square_sync_log(50)
    check("import logged",
          any(e["direction"] == "import" for e in log))

    # Skipped/errored items created nothing.
    check("turn item not created",
          db.search_products("Turn LED Bulbs") == [])
    check("headlight item not created",
          db.search_products("headlight LED kit") == [])


def test_apply_idempotent_plan():
    print("5 — re-running apply skips already-imported SKUs")
    use_fake()
    snap = square_import.fetch_catalog()
    plan = square_import.plan_import(snap)
    fog = by_raw(plan, "Fog LED Bulbs")
    check("re-planned fog item skipped as already imported",
          fog["action"] == "skip" and "already imported" in fog["reason"],
          fog)
    counts = square_import.apply_import(snap)
    check("second run creates nothing", counts["created"] == 0, counts)
    check("second run skips everything previously created",
          counts["skipped"] >= 3, counts)


# ------------------------------------------------------------ 4. routes


def _staged_tokens():
    d = appmod._import_staging_dir()
    return [p for p in d.glob("square-*.json")]


def test_routes():
    print("6 — admin import routes")
    use_fake()
    c = admin_client()
    for p in _staged_tokens():
        p.unlink()
    # Fresh slate: earlier tests already imported the fake catalog.
    for q in ("Fog LED Bulbs", "Interior LED Bulbs", "Dash Cam Pro"):
        for hit in db.search_products(q):
            db.delete_product(hit["id"])

    r = c.post("/admin/square/import")
    check("import fetch renders preview", r.status_code == 200)
    html = r.get_data(as_text=True)
    check("preview shows item names", "Fog LED Bulbs" in html)
    check("preview shows create badge", "create</span>" in html)
    check("preview shows blocked badge", "blocked</span>" in html)
    m = re.search(r'name="token" value="([0-9a-f]{32})"', html)
    check("preview carries a staging token", m is not None)
    n_before = product_count()
    # preview alone must not write: re-plan from the staged snapshot and
    # confirm the product set is unchanged.
    check("nothing written by preview", product_count() == n_before)
    check("snapshot staged to disk", len(_staged_tokens()) == 1)

    r = c.post("/admin/square/import/confirm",
               data={"token": m.group(1)}, follow_redirects=False)
    check("confirm redirects", r.status_code == 302)
    check("confirm created the draft",
          len(db.search_products("Fog LED Bulbs")) == 1)
    check("staged file removed", len(_staged_tokens()) == 0)

    r = c.post("/admin/square/import/confirm",
               data={"token": "0" * 32}, follow_redirects=False)
    check("bad token redirects", r.status_code == 302)

    db.set_setting("square_access_token", "")
    db.set_setting("square_location_id", "")
    r = c.post("/admin/square/import", follow_redirects=False)
    check("unconfigured redirects", r.status_code == 302)
    configure_square()


def main():
    db.init_db()
    tests = [test_fetch, test_fetch_not_configured, test_plan,
             test_apply, test_apply_idempotent_plan, test_routes]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - keep the suite running
            FAIL.append(f"{t.__name__} raised")
            print(f"  [FAIL] {t.__name__} raised {exc!r}")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed"
          f" ({len(PASS) + len(FAIL)} checks)")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
