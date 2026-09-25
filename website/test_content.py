#!/usr/bin/env python3
"""Track B (content & merchandising) tests for the Bravo LEDs storefront.

Imports the app from app.py, wires the content blueprint in itself via
register_content_routes(app), and exercises every Track B surface against
an isolated temp SQLite DB (STORE_DB) so the real store.db is untouched.

Run:  cd website && .venv/bin/python test_content.py
"""

import os
import shutil
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

TMP = tempfile.mkdtemp(prefix="content_test_")
os.environ["STORE_DB"] = os.path.join(TMP, "store.db")
os.environ["FITMENT_DIR"] = os.path.join(TMP, "fitment")  # absent -> seed
os.environ["SEMA_DIR"] = os.path.join(TMP, "sema")

# Fresh imports so db.DB_PATH picks up the temp STORE_DB.
for _m in ("db", "catalog", "content", "fitment_loader", "app"):
    sys.modules.pop(_m, None)

import catalog as catalogmod  # noqa: E402
import content as contentmod   # noqa: E402
import db as dbmod             # noqa: E402
import app as appmod           # noqa: E402

contentmod.register_content_routes(appmod.app)
client = appmod.app.test_client()

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


def body(r):
    return r.get_data(as_text=True)


GUIDE_SLUGS = ["low-beam-bulb-swap", "high-beam-bulb-swap", "fog-light-bulbs",
               "turn-signal-bulbs", "brake-backup-bulbs", "interior-dome-bulbs"]


def test_pages():
    print("Content pages")
    r = client.get("/faq")
    t = body(r)
    check("GET /faq -> 200", r.status_code == 200)
    check("faq: 1-year warranty", "1-year warranty" in t)
    check("faq: shipping 1-2 business days", "1–2 business days" in t)
    check("faq: 30-day returns", "30-day" in t)
    check("faq: dot-compliance link", "/dot-compliance" in t)
    check("faq: no 'headlight'", "headlight" not in t.lower())

    r = client.get("/guides")
    check("GET /guides -> 200", r.status_code == 200)
    check("guides index links all guides",
          all(f"/guides/{s}" in body(r) for s in GUIDE_SLUGS))
    for s in GUIDE_SLUGS:
        r = client.get(f"/guides/{s}")
        t = body(r)
        check(f"GET /guides/{s} -> 200", r.status_code == 200)
        check(f"guide {s}: no 'headlight'", "headlight" not in t.lower())
    r = client.get("/guides/bogus")
    check("GET /guides/bogus -> 404", r.status_code == 404)

    r = client.get("/privacy")
    t = body(r)
    check("GET /privacy -> 200", r.status_code == 200)
    check("privacy mentions Stripe", "Stripe" in t)
    check("privacy has [STORE ADDRESS] placeholder",
          "[STORE ADDRESS]" in t)
    check("privacy has [CONTACT EMAIL] placeholder",
          "[CONTACT EMAIL]" in t)
    check("privacy: no 'headlight'", "headlight" not in t.lower())

    r = client.get("/terms")
    t = body(r)
    check("GET /terms -> 200", r.status_code == 200)
    check("terms mentions Stripe", "Stripe" in t)
    check("terms: 1-year warranty", "1-year warranty" in t)
    check("terms: 30-day returns", "30-day" in t)
    check("terms: no 'headlight'", "headlight" not in t.lower())

    r = client.get("/")
    check("homepage newsletter copy",
          r.status_code == 200
          and "Join the list for deals and new products." in body(r))
    check("footer has faq/guides/privacy/terms links",
          all(x in body(r) for x in
              ['/faq', '/guides', '/privacy', '/terms']))


def test_newsletter():
    print("Newsletter")
    r = client.post("/newsletter",
                    data={"email": "Fan@Example.com ", "next": "/"})
    check("signup -> 302 redirect", r.status_code == 302)
    check("redirect lands with nl=ok",
          "nl=ok" in r.headers.get("Location", ""))
    subs = contentmod.list_subscribers()
    check("email stored lowercased/stripped",
          [s["email"] for s in subs] == ["fan@example.com"], str(subs))

    # Duplicate is a silent no-op: same redirect, still one row.
    r = client.post("/newsletter",
                    data={"email": "fan@example.com", "next": "/"})
    check("duplicate signup -> 302 nl=ok",
          r.status_code == 302 and "nl=ok" in r.headers.get("Location", ""))
    check("duplicate not stored twice",
          len(contentmod.list_subscribers()) == 1)

    r = client.post("/newsletter", data={"email": "not-an-email", "next": "/"})
    check("invalid email -> nl=bad",
          r.status_code == 302 and "nl=bad" in r.headers.get("Location", ""))
    check("invalid email not stored",
          len(contentmod.list_subscribers()) == 1)

    r = client.get("/admin/newsletter.csv")
    check("CSV denied when not authed", r.status_code == 403)
    with client.session_transaction() as s:
        s["admin_authed"] = True
    r = client.get("/admin/newsletter.csv")
    t = body(r)
    check("CSV 200 when authed",
          r.status_code == 200 and "text/csv" in r.headers.get("Content-Type", ""))
    check("CSV has header + email",
          t.startswith("email,subscribed_at") and "fan@example.com" in t)
    with client.session_transaction() as s:
        s.pop("admin_authed", None)


def test_sale_pricing():
    print("Sale pricing")
    t = body(client.get("/shop"))
    check("no Sale badge by default (all NULL)", ">Sale<" not in t)

    pid = "plus-led-bulbs"
    contentmod.set_sale_price(pid, 2999)
    p = dbmod.get_product(pid)
    check("sale_price_cents round-trips",
          p.get("sale_price_cents") == 2999, str(p.get("sale_price_cents")))
    check("catalog.is_on_sale True", catalogmod.is_on_sale(p) is True)
    check("catalog.product_sale_price",
          catalogmod.product_sale_price(p) == 2999)

    t = body(client.get("/shop"))
    check("shop card shows Sale badge", ">Sale<" in t)
    check("shop card strikethrough original",
          "<s" in t and "$35.00" in t and "$29.99" in t)
    t = body(client.get(f"/product/{pid}"))
    check("product page shows sale price",
          "$29.99" in t and "$35.00" in t and ">Sale<" in t)

    contentmod.set_sale_price(pid, None)
    p = dbmod.get_product(pid)
    check("clearing sale -> NULL", p.get("sale_price_cents") is None)
    check("catalog.is_on_sale False", catalogmod.is_on_sale(p) is False)
    check("badge gone after clear",
          ">Sale<" not in body(client.get("/shop")))


def test_bundles():
    print("Bundles")
    check("no bundle section when empty",
          "Bundle &amp; Save" not in body(client.get("/shop")))
    check("list_bundles empty", contentmod.list_bundles() == [])

    contentmod.create_bundle(
        "low-high-kit", "Low + High Beam LED Kit", "low-high-beam-kit",
        ["basic-led-bulbs", "plus-led-bulbs"], 5500,
        "Match your low and high beams in one box.")
    b = contentmod.get_bundle("low-high-beam-kit")
    check("get_bundle resolves", b is not None and b["name"].startswith("Low +"))
    names = [p["name"] for p in b["products"]]
    check("bundle products resolved",
          names == ["Bravo Basic LED Bulbs", "Bravo Plus LED Bulbs"], str(names))
    check("regular total = 2500+3500",
          b["regular_total_cents"] == 6000, str(b["regular_total_cents"]))
    check("savings = 500", b["savings_cents"] == 500)
    check("catalog.bundle_regular_total",
          catalogmod.bundle_regular_total(
              {"product_ids": '["basic-led-bulbs","plus-led-bulbs"]',
               "price_cents": 5500}, dbmod.get_product) == 6000)
    check("catalog.bundle_savings",
          catalogmod.bundle_savings(
              {"product_ids": '["basic-led-bulbs","plus-led-bulbs"]',
               "price_cents": 5500}, dbmod.get_product) == 500)

    t = body(client.get("/shop"))
    check("bundle section renders on shop",
          "Bundle &amp; Save" in t and "Low + High Beam LED Kit" in t
          and "$55.00" in t)

    contentmod.delete_bundle("low-high-kit")
    check("bundle deleted", contentmod.get_bundle("low-high-beam-kit") is None)
    check("bundle section gone",
          "Bundle &amp; Save" not in body(client.get("/shop")))


def test_reviews():
    print("Reviews")
    pid = "basic-led-bulbs"
    t = body(client.get(f"/product/{pid}"))
    check("empty state 'Be the first to review.'",
          "Be the first to review." in t)

    r = client.post(f"/product/{pid}/review",
                    data={"name": "Rosa", "rating": "5",
                          "body": "Bright and easy to install."},
                    follow_redirects=True)
    t = body(r)
    check("submit -> thanks flash",
          r.status_code == 200 and "awaiting moderation" in t)
    check("pending review NOT shown yet",
          "Bright and easy to install." not in t)

    revs = contentmod.list_all_reviews()
    check("review stored as pending",
          len(revs) == 1 and revs[0]["status"] == "pending"
          and revs[0]["rating"] == 5, str(revs))
    rid = revs[0]["id"]

    r = client.get("/admin/reviews")
    check("moderation denied when not authed", r.status_code == 403)
    with client.session_transaction() as s:
        s["admin_authed"] = True
    r = client.get("/admin/reviews")
    check("moderation 200 when authed",
          r.status_code == 200 and "Rosa" in body(r))
    r = client.post(f"/admin/reviews/{rid}/approve", follow_redirects=True)
    check("approve -> 200", r.status_code == 200)

    t = body(client.get(f"/product/{pid}"))
    check("approved review visible",
          "Bright and easy to install." in t and "Rosa" in t)
    check("star rating rendered", "★★★★★" in t)

    # Invalid submissions are rejected, not stored.
    n0 = len(contentmod.list_all_reviews())
    client.post(f"/product/{pid}/review",
                data={"name": "", "rating": "5", "body": "x"})
    client.post(f"/product/{pid}/review",
                data={"name": "Bo", "rating": "9", "body": "x"})
    client.post(f"/product/bogus-id/review",
                data={"name": "Bo", "rating": "5", "body": "x"})
    check("invalid reviews rejected",
          len(contentmod.list_all_reviews()) == n0)

    r = client.post(f"/admin/reviews/{rid}/delete", follow_redirects=True)
    check("delete -> 200", r.status_code == 200)
    check("deleted review gone",
          "Bright and easy to install."
          not in body(client.get(f"/product/{pid}")))
    with client.session_transaction() as s:
        s.pop("admin_authed", None)


def test_guide_images():
    print("Guide step illustrations")
    import re
    for s in GUIDE_SLUGS:
        guide = contentmod.GUIDES[s]
        # Empty images map is valid: illustrations were removed and will be
        # re-created from the user's own photos later. The map, template
        # <figure> block, and CSS stay so future photos plug straight in.
        check(f"guide {s}: images map is a dict (empty is valid)",
              isinstance(guide.get("images"), dict))
        images = guide.get("images") or {}
        r = client.get(f"/guides/{s}")
        t = body(r)
        srcs = re.findall(r'<img[^>]+src="(/static/img/guides/[^"]+)"', t)
        check(f"guide {s}: rendered page references only existing images",
              all(os.path.isfile(os.path.join(WEB, src.lstrip("/")))
                  for src in srcs),
              extra=str(srcs))
        for step_no, im in images.items():
            fpath = os.path.join(WEB, "static", im["src"])
            check(f"guide {s} step {step_no}: image file exists",
                  os.path.isfile(fpath), extra=fpath)
            check(f"guide {s} step {step_no}: image file non-empty",
                  os.path.isfile(fpath) and os.path.getsize(fpath) > 0)
            check(f"guide {s} step {step_no}: step number valid",
                  1 <= step_no <= len(guide["steps"]))
            check(f"guide {s} step {step_no}: alt text present, no 'headlight'",
                  bool(im.get("alt")) and "headlight" not in im["alt"].lower())
            check(f"guide {s} step {step_no}: image rendered on page",
                  f'/static/{im["src"]}' in t)


def test_schema_idempotent():
    print("Schema idempotency")
    contentmod.ensure_content_schema()
    contentmod.ensure_content_schema()
    check("schema creation is idempotent", True)


def main():
    try:
        test_pages()
        test_newsletter()
        test_sale_pricing()
        test_bundles()
        test_reviews()
        test_guide_images()
        test_schema_idempotent()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
