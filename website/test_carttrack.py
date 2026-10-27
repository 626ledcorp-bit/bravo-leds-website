"""Tests for abandoned-cart tracking + recovery (carttrack.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["STORE_DB"] = "/tmp/carttrack_test_db.sqlite"
if os.path.exists("/tmp/carttrack_test_db.sqlite"):
    os.remove("/tmp/carttrack_test_db.sqlite")

import carttrack
import db
import emailoffer
import emails
import landing
from app import app

db.init_db()
landing.ensure_landing_schema()
carttrack.ensure_cart_schema()
emailoffer.ensure_offer_schema()

db.create_product({
    "id": "ct-basic", "name": "Bravo Basic LED Bulbs",
    "status": "active", "category": "led-bulbs", "tier": "Basic",
    "price_cents": 2500,
    "variant_groups": [{"name": "Size", "values": ["H11"]}],
})


def _only_snapshot():
    con = db._connect()
    rows = con.execute("SELECT * FROM cart_snapshots").fetchall()
    con.close()
    assert len(rows) == 1, rows
    return dict(rows[0])


def _clear_snapshots():
    con = db._connect()
    con.execute("DELETE FROM cart_snapshots")
    con.commit()
    con.close()


def _age_snapshot(snap_id, iso="2020-01-01T00:00:00+00:00"):
    con = db._connect()
    con.execute("UPDATE cart_snapshots SET updated_at = ? WHERE id = ?",
                (iso, snap_id))
    con.commit()
    con.close()


def test_snapshot_written_on_cart_add_and_cleared_on_empty():
    c = app.test_client()
    r = c.post("/cart/add", data={"product_id": "ct-basic",
                                  "vehicle": "2019 Honda Accord"})
    assert r.status_code in (301, 302, 303)
    snap = _only_snapshot()
    assert snap["vehicle_label"] == "2019 Honda Accord"
    assert snap["subtotal_cents"] == 2500
    assert snap["status"] == "open" and snap["email"] is None
    assert "Bravo Basic LED Bulbs" in snap["items_json"]
    assert snap["restore_token"]
    # qty update re-prices the snapshot
    with c.session_transaction() as s:
        key = list(s["cart"])[0]
    c.post("/cart/update", data={"key": key, "qty": "3"})
    snap = _only_snapshot()
    assert snap["subtotal_cents"] == 7500
    # removing the last line clears the open snapshot
    c.post("/cart/remove", data={"key": key})
    con = db._connect()
    n = con.execute("SELECT COUNT(*) AS c FROM cart_snapshots"
                    ).fetchone()["c"]
    con.close()
    assert n == 0


def test_email_capture_and_restore():
    c = app.test_client()
    c.post("/cart/add", data={"product_id": "ct-basic",
                              "vehicle": "2019 Honda Accord"})
    # bad email rejected; good email attaches (checkout-style capture)
    r = c.post("/api/carttrack/email", json={"email": "nope"})
    assert r.status_code == 400
    r = c.post("/api/carttrack/email",
               json={"email": "buyer@example.com", "source": "checkout"})
    assert r.get_json()["ok"] is True
    snap = _only_snapshot()
    assert snap["email"] == "buyer@example.com"
    # the restore link rebuilds the cart for a fresh visitor
    c2 = app.test_client()
    r = c2.get(f"/cart/restore/{snap['restore_token']}")
    assert r.status_code in (301, 302, 303)
    with c2.session_transaction() as s:
        cart = s.get("cart") or {}
    assert len(cart) == 1
    line = list(cart.values())[0]
    assert line["product_id"] == "ct-basic"
    assert line["vehicle"] == "2019 Honda Accord"
    # unknown token just lands on the cart
    r = c2.get("/cart/restore/does-not-exist")
    assert r.status_code in (301, 302, 303)


def test_recovery_sender_respects_toggle_and_smtp():
    _clear_snapshots()
    c = app.test_client()
    c.post("/cart/add", data={"product_id": "ct-basic"})
    c.post("/api/carttrack/email",
           json={"email": "due@example.com", "source": "checkout"})
    snap = _only_snapshot()
    _age_snapshot(snap["id"])  # untouched for way more than 3 hours
    # Master toggle OFF (the default): nothing sends, nothing is marked.
    assert db.get_setting("cart_recovery_enabled", "0") == "0"
    assert carttrack.send_due_recoveries() == 0
    snap = carttrack.get_snapshot(snap["id"])
    assert snap["status"] == "open" and snap["recovery_sent_at"] is None
    # Toggle ON but SMTP unconfigured: send_email_channel returns False,
    # so the one send is NOT burned — status stays open.
    db.set_setting("cart_recovery_enabled", "1")
    assert not emails.smtp_configured()
    assert carttrack.send_due_recoveries() == 0
    snap = carttrack.get_snapshot(snap["id"])
    assert snap["status"] == "open" and snap["recovery_sent_at"] is None
    # With a working mailer the email goes out exactly once.
    sent = []
    orig = emails.send_email_channel
    emails.send_email_channel = \
        lambda to, subj, body: sent.append((to, subj, body)) or True
    try:
        assert carttrack.send_due_recoveries() == 1
        assert carttrack.send_due_recoveries() == 0  # never twice
    finally:
        emails.send_email_channel = orig
    assert len(sent) == 1
    to, subj, body = sent[0]
    assert to == "due@example.com"
    assert "/cart/restore/" in body and "Unsubscribe" in body
    snap = carttrack.get_snapshot(snap["id"])
    assert snap["status"] == "recovered_sent"
    assert snap["recovery_sent_at"] is not None
    db.set_setting("cart_recovery_enabled", "0")


def test_recovery_skips_offer_unsubscribed():
    _clear_snapshots()
    c = app.test_client()
    c.post("/cart/add", data={"product_id": "ct-basic"})
    # shopper is on the offer list and opted out there
    app.test_client().post("/api/offer/claim",
                           json={"email": "gone@example.com"})
    tok = emailoffer.unsubscribe_token("gone@example.com")
    app.test_client().get(f"/offer/unsubscribe?e=gone@example.com&t={tok}")
    assert emailoffer.email_is_unsubscribed("gone@example.com")
    r = c.post("/api/carttrack/email",
               json={"email": "gone@example.com", "source": "checkout"})
    assert r.get_json()["ok"] is True
    snap = _only_snapshot()
    assert snap["status"] == "unsubscribed"  # opted out at attach time
    _age_snapshot(snap["id"])
    db.set_setting("cart_recovery_enabled", "1")
    sent = []
    orig = emails.send_email_channel
    emails.send_email_channel = \
        lambda to, subj, body: sent.append(to) or True
    try:
        assert carttrack.send_due_recoveries() == 0
    finally:
        emails.send_email_channel = orig
    assert sent == []
    db.set_setting("cart_recovery_enabled", "0")


def test_mark_ordered():
    _clear_snapshots()
    c = app.test_client()
    c.post("/cart/add", data={"product_id": "ct-basic"})
    snap = _only_snapshot()
    # the snapshot's session_key IS the visitor token from the bc_vid cookie
    carttrack.mark_ordered(order_id=99, visitor=snap["session_key"])
    snap = carttrack.get_snapshot(snap["id"])
    assert snap["status"] == "ordered" and snap["order_id"] == 99


def test_admin_abandoned_carts_page():
    c = app.test_client()
    r = c.get("/admin/abandoned-carts")
    assert r.status_code in (301, 302, 303)  # login redirect
    with c.session_transaction() as s:
        s["admin_authed"] = True
    r = c.get("/admin/abandoned-carts")
    assert r.status_code == 200
    assert b"Abandoned Carts" in r.data
    assert b"ct-basic" not in r.data  # product ids are not shown raw
    assert b"Bravo Basic LED Bulbs" in r.data  # item names are


if __name__ == "__main__":
    test_snapshot_written_on_cart_add_and_cleared_on_empty()
    print("ok snapshot lifecycle")
    test_email_capture_and_restore()
    print("ok capture + restore")
    test_recovery_sender_respects_toggle_and_smtp()
    print("ok recovery gating")
    test_recovery_skips_offer_unsubscribed()
    print("ok cross-unsubscribe")
    test_mark_ordered()
    print("ok mark ordered")
    test_admin_abandoned_carts_page()
    print("ok admin page")
    print("ALL CARTTRACK TESTS PASSED")
