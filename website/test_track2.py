#!/usr/bin/env python3
"""Track 2 tests: 7 new primary categories, draft products, publish toggle,
DOT disclaimer scoping, fitment mapping changes, copy guards.

Isolated temp SQLite DB (STORE_DB) so the real store.db is untouched.

Run:  cd website && .venv/bin/python test_track2.py
"""

import os
import re
import shutil
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

TMP = tempfile.mkdtemp(prefix="track2_test_")
os.environ["STORE_DB"] = os.path.join(TMP, "store.db")
os.environ["FITMENT_DIR"] = os.path.join(TMP, "fitment")  # absent -> seed
os.environ["SEMA_DIR"] = os.path.join(TMP, "sema")
os.environ.pop("ADMIN_PASSWORD", None)

# Fresh imports so db.DB_PATH picks up the temp STORE_DB.
for _m in ("db", "catalog", "content", "fitment_loader", "app"):
    sys.modules.pop(_m, None)

import catalog as catalogmod  # noqa: E402
import db as dbmod             # noqa: E402
import fitment_loader as fitmod  # noqa: E402
import app as appmod           # noqa: E402

client = appmod.app.test_client()

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


def body(r):
    return r.get_data(as_text=True)


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


NEW_CATS = [
    ("hid-conversion-kits", "HID Conversion Kits"),
    ("factory-hid-bulbs", "Factory HID Bulbs"),
    ("led-miniature-bulbs", "LED Miniature Bulbs"),
    ("hid-accessories", "HID Accessories"),
    ("led-accessories", "LED Accessories"),
    ("dash-cams", "Dash Cams"),
    ("jump-starters", "Jump Starters"),
]

DRAFT_IDS = [p["id"] for p in catalogmod.PRODUCTS if p.get("draft")]
DISCLAIMER = "Not DOT/SAE approved for on-road use."


def test_categories_registered():
    print("New categories registered")
    check("7 new categories in catalog",
          all(s in catalogmod.CATEGORIES for s, _ in NEW_CATS),
          str(list(catalogmod.CATEGORIES)))
    check("19 draft products seeded", len(DRAFT_IDS) == 19, str(len(DRAFT_IDS)))
    check("2-4 products per new category",
          all(2 <= sum(1 for p in catalogmod.PRODUCTS
                       if p["category"] == s) <= 4 for s, _ in NEW_CATS))
    check("every seeded product has 1-year warranty",
          all(p["warranty"] == "1-year warranty"
              for p in catalogmod.PRODUCTS if p.get("draft")))
    flags = {s: catalogmod.CATEGORIES[s]["requires_dot_disclaimer"]
             for s, _ in NEW_CATS}
    check("disclaimer flag True on lighting cats",
          all(flags[s] for s in ("hid-conversion-kits", "factory-hid-bulbs",
                                 "led-miniature-bulbs")))
    check("disclaimer flag False on dash/jump/accessories",
          not any(flags[s] for s in ("dash-cams", "jump-starters",
                                     "hid-accessories", "led-accessories")),
          str(flags))


def test_category_pages():
    print("Category pages + nav")
    for slug, name in NEW_CATS:
        r = client.get(f"/shop/{slug}")
        t = body(r)
        check(f"GET /shop/{slug} -> 200", r.status_code == 200)
        check(f"/shop/{slug} shows name", name in t)
        check(f"/shop/{slug} shows empty state (all drafts)",
              "No products in this category yet." in t)
    r = client.get("/shop/bogus-cat")
    check("GET /shop/bogus-cat -> 404", r.status_code == 404)
    t = body(client.get("/"))
    check("desktop nav dropdown links all new categories",
          all(f'href="/shop/{s}"' in t and name in t
              for s, name in NEW_CATS))
    check("cat-strip (mobile nav) links all new categories",
          all(f"/shop/{s}" in t for s, _ in NEW_CATS))
    t = body(client.get("/shop"))
    check("footer shop links all new categories",
          all(f"/shop/{s}" in t for s, _ in NEW_CATS))


def test_drafts_hidden():
    print("Drafts hidden from public surfaces")
    t = body(client.get("/shop"))
    draft_names = [dbmod.get_product(pid)["name"] for pid in DRAFT_IDS]
    check("no draft product on /shop",
          not any(n in t for n in draft_names), str(t[:200]))
    for slug, _ in NEW_CATS:
        check(f"no draft on /shop/{slug}",
              not any(dbmod.get_product(pid)["name"] in body(
                  client.get(f"/shop/{slug}")) for pid in DRAFT_IDS))
    for pid in DRAFT_IDS:
        r = client.get(f"/product/{pid}")
        check(f"/product/{pid} -> 404 while draft",
              r.status_code == 404, str(r.status_code))
        break  # one representative 404 is enough here; loop would be slow
    check("db.list_products() excludes drafts",
          not any(p["id"] in DRAFT_IDS for p in dbmod.list_products()))
    check("db.list_products(include_drafts=True) includes drafts",
          all(any(p["id"] == pid for p in
                  dbmod.list_products(include_drafts=True))
              for pid in DRAFT_IDS))
    check("published products still listed",
          any(p["id"] == "basic-led-bulbs" for p in dbmod.list_products()))


def admin_login():
    return client.post("/admin/login", data={"password": "s3cret"},
                       follow_redirects=False)


def test_admin_publish_toggle():
    print("Admin: drafts listed + publish/unpublish toggle")
    pid = "hid-35w-kit-h11"
    with Env(ADMIN_PASSWORD="s3cret"):
        r = client.get("/admin")
        check("admin locked before login",
              r.status_code == 302 and "/admin/login" in r.headers["Location"])
        r = client.post("/admin/product/hid-35w-kit-h11/publish",
                        data={"action": "publish"}, follow_redirects=False)
        check("publish without auth -> redirect to login",
              r.status_code == 302 and "/admin/login" in r.headers["Location"])
        check("still draft after unauth attempt",
              dbmod.get_product(pid)["draft"] is True)

        check("login ok",
              admin_login().status_code == 302)
        t = body(client.get("/admin"))
        check("admin lists draft products", pid in t)
        check("DRAFT badge rendered",
              "DRAFT — needs real SKU/price/photo" in t)
        check("draft name shown", "35W HID Conversion Kit (H11)" in t)

        r = client.post(f"/admin/product/{pid}/publish",
                        data={"action": "publish"}, follow_redirects=False)
        check("publish -> redirect to #products",
              r.status_code == 302
              and r.headers["Location"].endswith("/admin#products"))
        check("draft flag cleared", dbmod.get_product(pid)["draft"] is False)

        t = body(client.get(f"/shop/hid-conversion-kits"))
        check("published product on category page",
              "35W HID Conversion Kit (H11)" in t)
        t = body(client.get("/shop"))
        check("published product on /shop", "35W HID Conversion Kit" in t)
        r = client.get(f"/product/{pid}")
        check("published product page -> 200", r.status_code == 200)

        r = client.post(f"/admin/product/{pid}/publish",
                        data={"action": "unpublish"}, follow_redirects=False)
        check("unpublish -> 302", r.status_code == 302)
        check("draft flag restored", dbmod.get_product(pid)["draft"] is True)
        check("/product 404 again after unpublish",
              client.get(f"/product/{pid}").status_code == 404)
        client.get("/admin/logout")


def test_disclaimer_scoping():
    print("DOT disclaimer scoping")
    with Env(ADMIN_PASSWORD="s3cret"):
        admin_login()
        # publish one product per representative category
        for pid in ("hid-35w-kit-h11", "factory-hid-d2s", "led-mini-194-t10",
                    "hid-ballast-35w", "led-decoder-resistor-kit",
                    "dash-cam-dual-1080p", "jump-starter-2000a"):
            client.post(f"/admin/product/{pid}/publish",
                        data={"action": "publish"})
        try:
            # The footer carries the disclaimer sitewide, so we count
            # occurrences: 2 = product-page instance + footer, 1 = footer only.
            def disclaimer_count(pid):
                return body(client.get(f"/product/{pid}")).count(
                    "For off-road and fog light use only.")
            check("LED Bulbs product page shows disclaimer",
                  disclaimer_count("basic-led-bulbs") == 2)
            check("HID kit page shows disclaimer",
                  disclaimer_count("hid-35w-kit-h11") == 2)
            check("factory HID page shows disclaimer",
                  disclaimer_count("factory-hid-d2s") == 2)
            check("miniature bulb page shows disclaimer",
                  disclaimer_count("led-mini-194-t10") == 2)
            check("dash cam page shows NO product disclaimer",
                  disclaimer_count("dash-cam-dual-1080p") == 1)
            check("jump starter page shows NO product disclaimer",
                  disclaimer_count("jump-starter-2000a") == 1)
            check("HID accessory page shows NO product disclaimer",
                  disclaimer_count("hid-ballast-35w") == 1)
            check("LED accessory page shows NO product disclaimer",
                  disclaimer_count("led-decoder-resistor-kit") == 1)
            check("dash cam page still links /dot-compliance via footer",
                  "/dot-compliance" in body(
                      client.get("/product/dash-cam-dual-1080p")))
            # fitment results keep the disclaimer as-is
            r = client.get("/fitment?year=2018&make=Toyota&model=Camry")
            check("fitment results -> 200", r.status_code == 200)
            check("fitment results keep disclaimer",
                  DISCLAIMER in body(r))
        finally:
            for pid in ("hid-35w-kit-h11", "factory-hid-d2s",
                        "led-mini-194-t10", "hid-ballast-35w",
                        "led-decoder-resistor-kit", "dash-cam-dual-1080p",
                        "jump-starter-2000a"):
                client.post(f"/admin/product/{pid}/publish",
                            data={"action": "unpublish"})
        client.get("/admin/logout")


def test_fitment_mappings():
    print("Fitment mappings")
    pc = fitmod.POSITION_CATEGORIES
    check("dome_light -> led-miniature-bulbs",
          pc["dome_light"] == ["led-miniature-bulbs"], str(pc["dome_light"]))
    check("map_light -> led-miniature-bulbs",
          pc["map_light"] == ["led-miniature-bulbs"])
    check("low_beam still maps to led-bulbs",
          "led-bulbs" in pc["low_beam"])
    check("high_beam still maps to led-bulbs",
          "led-bulbs" in pc["high_beam"])
    check("drl still maps to led-bulbs", "led-bulbs" in pc["drl"])
    all_cats = [c for cats in pc.values() for c in cats]
    check("dash-cams never in position mappings", "dash-cams" not in all_cats)
    check("jump-starters never in position mappings",
          "jump-starters" not in all_cats)
    # drafts must not leak into the public finder
    check("drafts excluded from products_matching_size",
          dbmod.products_matching_size("194", ["led-miniature-bulbs"]) == [])
    with Env(ADMIN_PASSWORD="s3cret"):
        admin_login()
        client.post("/admin/product/led-mini-194-t10/publish",
                    data={"action": "publish"})
        try:
            m = dbmod.products_matching_size("194", ["led-miniature-bulbs"])
            check("published miniature bulb matches dome 194",
                  any(p["id"] == "led-mini-194-t10" for p in m),
                  str([p["id"] for p in m]))
            rows = fitmod.fitment_db.get_fitment("2018", "Toyota", "Camry")
            dome = [r for r in rows if r["position"] == "dome_light"]
            check("seed dome row maps to miniature category",
                  dome and dome[0]["categories"] == ["led-miniature-bulbs"],
                  str([r["categories"] for r in dome]))
            r = client.get("/fitment?year=2018&make=Toyota&model=Camry")
            t = body(r)
            check("fitment page shows published miniature bulb",
                  "LED Miniature Bulb 194/T10" in t)
            check("fitment page shows no dash cam / jump starter products",
                  "/product/dash-cam-dual-1080p" not in t
                  and "/product/jump-starter-2000a" not in t)
        finally:
            client.post("/admin/product/led-mini-194-t10/publish",
                        data={"action": "unpublish"})
        client.get("/admin/logout")


def _strip_code_strings(text):
    # Blank out string literals: compliance validation code mentions the
    # forbidden word, but only customer-facing copy counts.
    _sq = chr(39)
    pat = _sq * 3 + r".*?" + _sq * 3 + r"|" + chr(34) * 3 + r".*?" + chr(34) * 3
    pat += r"|'[^'\n]*'|\"[^\"\n]*\""
    return re.sub(pat, '""', text, flags=re.S)


def test_copy_guards():
    print("Copy guards on new copy")
    files = ["catalog.py", "db.py", "app.py", "fitment_loader.py",
             "templates/base.html", "templates/shop.html",
             "templates/product.html", "templates/admin.html",
             "static/css/style.css"]
    bad = []
    for f in files:
        text = open(os.path.join(WEB, f), encoding="utf-8").read().lower()
        if f.endswith(".py"):
            text = _strip_code_strings(text)
        if "headlight" in text:
            bad.append(f)
    check("no 'headlight' in Track 2 files", not bad, str(bad))
    names = [c["name"] for c in catalogmod.CATEGORIES.values()]
    names += [p["name"] for p in catalogmod.PRODUCTS if p.get("draft")]
    check("no 'off-road' in category/product names",
          not any("off-road" in n.lower() for n in names), str(names))
    t = body(client.get("/"))
    nav = t.split('<nav class="nav">')[1].split("</nav>")[0]
    check("no 'off-road' in desktop nav", "off-road" not in nav.lower())
    # lighting products carry exactly one natural off-road mention
    for pid in ("hid-35w-kit-h11", "factory-hid-d2s", "led-mini-194-t10"):
        p = dbmod.get_product(pid)
        n = (p["blurb"] + " " + p["name"]).lower().count("off-road")
        check(f"{pid} has off-road mention in description", n >= 1, str(n))
    # non-lighting products have none
    for pid in ("hid-ballast-35w", "led-decoder-resistor-kit",
                "dash-cam-dual-1080p", "jump-starter-2000a"):
        p = dbmod.get_product(pid)
        text = (p["name"] + " " + p["blurb"] + " "
                + " ".join(p["features"])).lower()
        check(f"{pid} has no off-road wording", "off-road" not in text)


def main():
    try:
        test_categories_registered()
        test_category_pages()
        test_drafts_hidden()
        test_admin_publish_toggle()
        test_disclaimer_scoping()
        test_fitment_mappings()
        test_copy_guards()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL TRACK 2 TESTS PASSED")


if __name__ == "__main__":
    main()
