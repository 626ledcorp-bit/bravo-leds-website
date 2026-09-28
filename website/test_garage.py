#!/usr/bin/env python3
"""My Garage + /fit/<vehicle> landing page tests.

- Vehicle page: valid slugs -> 200 with "Fit for:" heading, breadcrumbs,
  per-position product cards; unknown slugs -> 404; never sets the garage
  from an invalid selection.
- Slug round-trip: multi-word makes (e.g. "American Motors") resolve.
- Garage API: add (valid/invalid), sync (filters invalid), main, remove,
  clear; session persists across requests.
- Old /fitment route still works and still refuses to set the pill on 404.
- Forbidden wording: no "headlight" in the new templates or JS.

Run:  cd website && /tmp/garagetest/bin/python test_garage.py
"""
import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="garage_"),
                                      "store.db")
os.environ["FITMENT_DIR"] = os.path.join(os.path.dirname(WEB), "fitment")
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL"):
    os.environ.pop(_v, None)

import app as appmod  # noqa: E402
from fitment_loader import fitment_db  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" -- {extra}" if extra and not cond else ""))


def client():
    return appmod.app.test_client()


def test_vehicle_page():
    c = client()
    r = c.get("/fit/2019/toyota/avalon")
    html = r.get_data(as_text=True)
    check("fit page 200", r.status_code == 200)
    check("fit page 'Fit for:' heading",
          "Fit for: 2019 Toyota Avalon" in html)
    check("fit page breadcrumbs", 'aria-label="Breadcrumb"' in html
          and "Fit for 2019 Toyota Avalon" in html)
    check("fit page has position blocks", "pos-block" in html)
    check("fit page all-caps brand", "BRAVO LEDS" in html)
    # invalid slugs -> 404, and nothing persisted
    for bad in ("/fit/2019/toyota/nosuchmodel",
                "/fit/2035/toyota/avalon",
                "/fit/2019/nosuchmake/avalon"):
        r = client().get(bad)
        check(f"404 for {bad}", r.status_code == 404)
    c2 = client()
    c2.get("/fit/2019/toyota/nosuchmodel")
    with c2.session_transaction() as s:
        check("invalid vehicle sets no garage",
              not s.get("garage") and not s.get("vehicle"))


def test_slug_roundtrip():
    # multi-word make -> slug -> canonical
    hit = appmod.resolve_vehicle_slug(1985, "american-motors", "eagle")
    check("slug round-trip American Motors/Eagle",
          hit == ("American Motors", "Eagle"), str(hit))
    r = client().get("/fit/1985/american-motors/eagle")
    check("slug URL 200", r.status_code == 200)


def test_garage_api():
    c = client()
    # add valid
    r = c.post("/api/garage/add",
               json={"year": 2019, "make": "Toyota", "model": "Avalon"})
    body = r.get_json()
    check("garage add valid 200", r.status_code == 200 and body["ok"])
    check("garage add returns fit url",
          body["url"] == "/fit/2019/toyota/avalon", body.get("url"))
    # add invalid -> 404, session untouched
    r = c.post("/api/garage/add",
               json={"year": 2019, "make": "Toyota", "model": "Fake"})
    check("garage add invalid 404", r.status_code == 404)
    with c.session_transaction() as s:
        check("garage has 1 vehicle", len(s.get("garage") or []) == 1)
        check("pill mirrors garage",
              (s.get("vehicle") or {}).get("model") == "Avalon")
    # duplicate add does not duplicate
    c.post("/api/garage/add",
           json={"year": 2019, "make": "Toyota", "model": "Avalon"})
    with c.session_transaction() as s:
        check("no duplicate vehicles", len(s.get("garage") or []) == 1)
    # sync filters invalid, only fills empty session
    c2 = client()
    r = c2.post("/api/garage/sync", json={"vehicles": [
        {"year": 2019, "make": "Toyota", "model": "Avalon"},
        {"year": 2019, "make": "Toyota", "model": "Fake"}]})
    check("sync keeps only valid",
          r.get_json()["vehicles"] == [
              {"year": 2019, "make": "Toyota", "model": "Avalon",
               "trim": None}])
    # main promote
    c.post("/api/garage/add",
           json={"year": 2019, "make": "Honda", "model": "Ridgeline"})
    r = c.post("/api/garage/main",
               json={"year": 2019, "make": "Toyota", "model": "Avalon"})
    check("main promote ok", r.status_code == 200
          and r.get_json()["vehicle"]["model"] == "Avalon")
    with c.session_transaction() as s:
        check("main is first in garage",
              (s.get("garage") or [{}])[0]["model"] == "Avalon")
    # remove
    r = c.post("/api/garage/remove",
               json={"year": 2019, "make": "Honda", "model": "Ridgeline"})
    check("remove drops vehicle",
          [v["model"] for v in r.get_json()["vehicles"]] == ["Avalon"])
    # clear
    r = c.post("/api/garage/clear")
    check("clear ok", r.status_code == 200)
    with c.session_transaction() as s:
        check("clear empties garage and pill",
              s.get("garage") == [] and s.get("vehicle") is None)
    # persistence across requests
    c.post("/api/garage/add",
           json={"year": 2019, "make": "Toyota", "model": "Avalon"})
    r = c.get("/")
    with c.session_transaction() as s:
        check("garage persists across requests",
              len(s.get("garage") or []) == 1)


def test_legacy_fitment_route():
    c = client()
    r = c.get("/fitment?year=2019&make=Toyota&model=Avalon")
    check("/fitment still 200", r.status_code == 200)
    check("/fitment still renders rows", "pos-block" in
          r.get_data(as_text=True))
    c2 = client()
    r = c2.get("/fitment?year=2019&make=Toyota&model=Fake")
    check("/fitment invalid 404", r.status_code == 404)
    with c2.session_transaction() as s:
        check("/fitment invalid sets no pill", not s.get("vehicle"))


def test_forbidden_word():
    bad = []
    for p in ("templates/fit_vehicle.html",
              "templates/_fitment_results.html",
              "templates/base.html",
              "static/js/garage.js"):
        text = open(os.path.join(WEB, p), encoding="utf-8").read()
        if re.search(r"headlight", text, re.IGNORECASE):
            bad.append(p)
    check("no 'headlight' in new garage content", not bad, ", ".join(bad))


def test_garage_modal_markup():
    html = client().get("/").get_data(as_text=True)
    check("garage modal in base", 'id="garage-modal"' in html)
    check("garage.js loaded", "garage.js" in html)
    check("pill opens modal", 'id="garage-open"' in html)


def test_size_preselect():
    c = client()
    html = c.get("/fit/2019/toyota/avalon").get_data(as_text=True)
    links = re.findall(r'href="(/product/[^"]+)"', html)
    check("vehicle product links carry ?size=",
          any("?size=9005" in l for l in links), str(links[:3]))
    r = c.get("/product/platinum-4070-led-bulbs?size=9005")
    h = r.get_data(as_text=True)
    check("product page 200 with ?size=", r.status_code == 200)
    check("preselect size emitted to picker",
          "PRESELECT_SIZE" in h and '"9005"' in h)
    r = c.get("/product/platinum-4070-led-bulbs?size=BOGUS99")
    check("unknown size ignored gracefully", r.status_code == 200)
    r = c.get("/product/platinum-4070-led-bulbs")
    check("no size param still 200", r.status_code == 200)


def main():
    test_vehicle_page()
    test_slug_roundtrip()
    test_garage_api()
    test_legacy_fitment_route()
    test_forbidden_word()
    test_garage_modal_markup()
    test_size_preselect()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
