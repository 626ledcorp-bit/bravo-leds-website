#!/usr/bin/env python3
"""Vehicle category page tests: hub + forward/rear/interior group pages.

- /fit/<vehicle> is a hub with three clickable category cards.
- /fit/<vehicle>/{forward,rear,interior} render 200 with only that
  group's positions.
- Unknown group -> 404; unknown vehicle -> 404 (hub and groups).
- Interior page shows the vehicle-specific kit card when one exists
  (2020 4Runner) and not otherwise (2010 Prius).
- Default pick per position is the cheapest option with upcharges shown.

Run:  cd website && .venv/bin/python test_fit_groups.py
"""
import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="fitgroups_"),
                                      "store.db")
os.environ["FITMENT_DIR"] = os.path.join(os.path.dirname(WEB), "fitment")
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL"):
    os.environ.setdefault(_v, "")

import app as appmod

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name} {extra}")


def client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


PRIUS = "/fit/2010/toyota/prius"
RUNNER = "/fit/2020/toyota/4runner"


def test_hub():
    c = client()
    r = c.get(PRIUS)
    html = r.get_data(as_text=True)
    check("hub 200", r.status_code == 200)
    check("hub title", "Fit for: 2010 Toyota Prius" in html)
    for g in ("forward", "rear", "interior"):
        check(f"hub links {g}", f'{PRIUS}/{g}' in html, "missing link")
    check("hub has category cards", 'class="fit-cat-card"' in html)
    check("hub has no series rows", 'class="series-row' not in html)
    check("hub has no kit bar", 'id="kitbar"' not in html)


def test_group_pages():
    c = client()
    for g, label in (("forward", "Forward Lighting"),
                     ("rear", "Exterior Rear Lighting"),
                     ("interior", "Interior Lighting")):
        r = c.get(f"{PRIUS}/{g}")
        html = r.get_data(as_text=True)
        check(f"{g} page 200", r.status_code == 200)
        check(f"{g} label shown", label in html)
        check(f"{g} breadcrumb back to hub",
              f'href="{PRIUS}"' in html)
        check(f"{g} has kit bar", 'id="kitbar"' in html)


def test_group_filtering():
    c = client()
    fwd = c.get(f"{PRIUS}/forward").get_data(as_text=True)
    rear = c.get(f"{PRIUS}/rear").get_data(as_text=True)
    intr = c.get(f"{PRIUS}/interior").get_data(as_text=True)
    check("forward has Low Beam", "Low Beam" in fwd)
    check("forward has Fog Light", "Fog Light" in fwd)
    check("forward lacks Dome Light", "Dome Light" not in fwd)
    check("rear has Brake Light", "Brake Light" in rear)
    check("rear has License Plate", "License Plate" in rear)
    check("rear lacks Low Beam", "Low Beam" not in rear)
    check("interior has Dome / Map Light", "Dome / Map Light" in intr)
    check("interior lacks Low Beam", "Low Beam" not in intr)


def test_bad_routes():
    c = client()
    check("bad group -> 404",
          c.get(f"{PRIUS}/sideways").status_code == 404)
    check("bad vehicle hub -> 404",
          c.get("/fit/2010/toyota/nosuchmodel").status_code == 404)
    check("bad vehicle group -> 404",
          c.get("/fit/2010/toyota/nosuchmodel/forward").status_code == 404)


def test_interior_kit_card():
    c = client()
    runner = c.get(f"{RUNNER}/interior").get_data(as_text=True)
    check("4runner interior 200", "Complete Interior LED Kit" in runner)
    check("4runner kit card links kit page",
          "/interior-kit/2020/toyota/4runner" in runner)
    check("4runner kit card has add button", 'id="kitcard-add"' in runner)
    prius = c.get(f"{PRIUS}/interior").get_data(as_text=True)
    check("prius has no kit card",
          "Complete Interior LED Kit" not in prius)


def test_cheapest_default():
    c = client()
    html = c.get(f"{PRIUS}/forward").get_data(as_text=True)
    m = re.search(r'data-position="Low Beam"(.*?)data-position="',
                  html, re.S)
    block = m.group(1) if m else html
    sel = re.findall(r'<div class="series-row sel".*?data-price="(\d+)"',
                     block, re.S)
    prices = [int(p) for p in re.findall(r'data-price="(\d+)"', block)]
    check("cheapest is default",
          bool(sel) and int(sel[0]) == min(prices),
          f"sel={sel} min={min(prices) if prices else None}")
    check("upcharge shown",
          re.search(r'class="upcharge">\+\$', html) is not None)


def main():
    print("vehicle category page tests")
    test_hub()
    test_group_pages()
    test_group_filtering()
    test_bad_routes()
    test_interior_kit_card()
    test_cheapest_default()
    print(f"{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
