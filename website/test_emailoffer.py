"""Tests for the 10%-off email popup (emailoffer.py)."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["STORE_DB"] = "/tmp/offer_test_db.sqlite"
if os.path.exists("/tmp/offer_test_db.sqlite"):
    os.remove("/tmp/offer_test_db.sqlite")

import db
import emailoffer
import landing
from app import app

db.init_db()
landing.ensure_landing_schema()
emailoffer.ensure_offer_schema()


def test_claim_issues_usable_single_use_code():
    c = app.test_client()
    r = c.get("/api/offer/active")
    assert r.get_json()["active"] is True
    # bad email rejected
    r = c.post("/api/offer/claim", json={"email": "not-an-email"})
    assert r.status_code == 400
    # good email -> real code, shown in the response as the SMTP fallback
    r = c.post("/api/offer/claim",
               json={"email": "Buyer@Example.com", "source": "/"})
    d = r.get_json()
    assert r.status_code == 200 and d["ok"], d
    assert d["already"] is False
    code = d["code"]
    assert re.match(r"^WELCOME10-[A-Z0-9]{6}$", code), code
    # it is a REAL promo code: 10% percent, single-use, usable right now
    promo = landing.get_promo(code)
    assert promo and promo["active"] and promo["kind"] == "percent"
    assert promo["percent"] == 10 and promo["max_uses"] == 1
    assert landing.promo_is_usable(promo)
    assert landing.discount_cents_for(promo, 10000) == 1000
    # signup row recorded (email normalized to lowercase)
    s = emailoffer.get_signup("buyer@example.com")
    assert s and s["code"] == code and s["source"] == "/"
    # and it applies at the cart like a shopper would use it
    r = c.post("/cart/promo", data={"code": code}, follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    with c.session_transaction() as sess:
        assert sess.get("promo_code") == code


def test_duplicate_email_resends_same_code():
    c = app.test_client()
    r1 = c.post("/api/offer/claim", json={"email": "dupe@example.com"})
    code1 = r1.get_json()["code"]
    r2 = c.post("/api/offer/claim", json={"email": "DUPE@example.com"})
    d2 = r2.get_json()
    assert d2["ok"] and d2["already"] is True
    assert d2["code"] == code1  # never a second code for one email
    con = db._connect()
    n = con.execute("SELECT COUNT(*) AS c FROM email_offer_signups"
                    " WHERE email = 'dupe@example.com'").fetchone()["c"]
    con.close()
    assert n == 1


def test_unsubscribe_token_flow():
    emailoffer_claim = app.test_client().post(
        "/api/offer/claim", json={"email": "optout@example.com"})
    assert emailoffer_claim.status_code == 200
    c = app.test_client()
    # bad token -> 400, still subscribed
    r = c.get("/offer/unsubscribe?e=optout@example.com&t=deadbeef")
    assert r.status_code == 400
    assert not emailoffer.email_is_unsubscribed("optout@example.com")
    # good token -> 200, flagged
    tok = emailoffer.unsubscribe_token("optout@example.com")
    r = c.get(f"/offer/unsubscribe?e=optout@example.com&t={tok}")
    assert r.status_code == 200
    assert emailoffer.email_is_unsubscribed("optout@example.com")


def test_admin_toggle_and_list():
    c = app.test_client()
    # unauthenticated -> login redirect
    r = c.get("/admin/email-offer")
    assert r.status_code in (301, 302, 303)
    with c.session_transaction() as s:
        s["admin_authed"] = True
    r = c.get("/admin/email-offer")
    assert r.status_code == 200
    assert b"buyer@example.com" in r.data
    # toggle off: /api/offer/active flips and claims are refused
    r = c.post("/admin/email-offer/toggle", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert app.test_client().get("/api/offer/active").get_json()["active"] \
        is False
    r = app.test_client().post("/api/offer/claim",
                               json={"email": "late@example.com"})
    assert r.status_code == 403
    # toggle back on for any later tests / cleanliness
    c.post("/admin/email-offer/toggle")
    assert app.test_client().get("/api/offer/active").get_json()["active"] \
        is True


def test_copy_rules():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in ("static/js/emailoffer.js",
                 "templates/offer_admin.html",
                 "templates/offer_unsubscribed.html",
                 "emailoffer.py"):
        text = open(os.path.join(here, path)).read()
        assert "headlight" not in text.lower(), path
        assert "Bravo LEDs" not in text, path


if __name__ == "__main__":
    test_claim_issues_usable_single_use_code()
    print("ok claim code")
    test_duplicate_email_resends_same_code()
    print("ok duplicate")
    test_unsubscribe_token_flow()
    print("ok unsubscribe")
    test_admin_toggle_and_list()
    print("ok admin")
    test_copy_rules()
    print("ok copy rules")
    print("ALL EMAIL OFFER TESTS PASSED")
