"""Tests for the shortname display filter + retargeting tags."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["STORE_DB"] = "/tmp/display_test_db.sqlite"
if os.path.exists("/tmp/display_test_db.sqlite"):
    os.remove("/tmp/display_test_db.sqlite")

import db
import landing
from flask import render_template

from app import app, display_name

db.init_db()
landing.ensure_landing_schema()

db.create_product({
    "id": "dt-basic", "name": "Bravo Basic LED Bulbs",
    "status": "active", "category": "led-bulbs", "tier": "Basic",
    "price_cents": 2500,
    "variant_groups": [{"name": "Size", "values": ["H11"]}],
})
db.create_product({
    "id": "dt-other", "name": "House Brand Work Light",
    "status": "active", "category": "pods", "tier": "",
    "price_cents": 4000,
    "variant_groups": [{"name": "Size", "values": ["4in"]}],
})


def test_display_name_unit():
    assert display_name("Bravo Basic LED Bulbs") == "Basic LED Bulbs"
    assert display_name("Bravo Premium CSP Series LED Bulbs — 60W") == \
        "Premium CSP Series LED Bulbs — 60W"
    # no exact prefix -> untouched (case-sensitive, single prefix only)
    assert display_name("Basic LED Bulbs") == "Basic LED Bulbs"
    assert display_name("bravo Basic LED Bulbs") == "bravo Basic LED Bulbs"
    assert display_name("Bravo") == "Bravo"
    assert display_name("Bravo Bravo LEDs") == "Bravo LEDs"
    assert display_name("") == ""
    assert display_name(None) == ""


def test_fitment_results_use_short_name():
    row = {
        "label": "Low Beam", "bulb_size_raw": "H11", "note": "",
        "sealed": False, "default_idx": 0,
        "options": [{
            "product": {"id": "dt-basic", "name": "Bravo Basic LED Bulbs",
                        "tier": "Basic"},
            "variation_id": 1, "price_cents": 2500, "size": "H11",
            "spec": "Our entry LED conversion",
        }, {
            "product": {"id": "dt-other", "name": "House Brand Work Light",
                        "tier": ""},
            "variation_id": 2, "price_cents": 4000, "size": "H11",
            "spec": "",
        }],
    }
    with app.test_request_context("/"):
        html = render_template("_fitment_results.html",
                               vehicle_label="2018 Honda Accord",
                               rows=[row], setup="halogen",
                               interior_kit=None,
                               year=2018, make="Honda", model="Accord")
    # visible name + toast data-name carry the SHORT name
    assert 'data-name="Basic LED Bulbs"' in html
    assert ">Basic LED Bulbs</a>" in html
    assert "Bravo Basic" not in html
    # a name without the prefix renders unchanged
    assert 'data-name="House Brand Work Light"' in html
    # prices + Add buttons intact
    assert "$25.00" in html and 'class="btn btn-sm add-one"' in html
    # the guarded Meta AddToCart hook is wired into the add-one success path
    assert "typeof fbq === 'function'" in html


def test_shop_cards_short_but_product_page_full():
    c = app.test_client()
    html = c.get("/shop").data.decode()
    assert "Basic LED Bulbs" in html
    assert "Bravo Basic LED Bulbs" not in html
    # product detail page keeps the FULL stored name (H1 + title)
    html = c.get("/product/dt-basic").data.decode()
    assert "<h1>Bravo Basic LED Bulbs</h1>" in html


def test_retargeting_tags_follow_settings():
    c = app.test_client()
    html = c.get("/").data.decode()
    assert "fbq(" not in html and "googletagmanager" not in html
    db.set_setting("meta_pixel_id", "123456789012345")
    db.set_setting("ga_measurement_id", "G-TEST12345")
    try:
        html = c.get("/").data.decode()
        assert "fbq('init', '123456789012345')" in html
        assert "fbq('track', 'PageView')" in html
        assert "gtag/js?id=G-TEST12345" in html
        assert "gtag('config', 'G-TEST12345')" in html
    finally:
        db.set_setting("meta_pixel_id", "")
        db.set_setting("ga_measurement_id", "")
    html = c.get("/").data.decode()
    assert "fbq(" not in html and "googletagmanager" not in html


if __name__ == "__main__":
    test_display_name_unit()
    print("ok display_name")
    test_fitment_results_use_short_name()
    print("ok fitment shortname")
    test_shop_cards_short_but_product_page_full()
    print("ok cards vs product page")
    test_retargeting_tags_follow_settings()
    print("ok tags")
    print("ALL DISPLAY/TAG TESTS PASSED")
