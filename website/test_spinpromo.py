"""Tests for the spin-to-win promo popup system (spinpromo.py)."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["STORE_DB"] = "/tmp/spin_test_db.sqlite"
if os.path.exists("/tmp/spin_test_db.sqlite"):
    os.remove("/tmp/spin_test_db.sqlite")

import db
import landing
import spinpromo
from app import app

db.init_db()
landing.ensure_landing_schema()
spinpromo.ensure_spin_schema()

SEGMENTS = [
    {"label": "25% OFF", "prize_type": "percent", "prize_value": 25,
     "weight": 5, "coupon_prefix": "SPIN", "total_win_cap": None, "sort": 0},
    {"label": "10% OFF", "prize_type": "percent", "prize_value": 10,
     "weight": 20, "coupon_prefix": "SPIN", "total_win_cap": None, "sort": 1},
    {"label": "$5 OFF", "prize_type": "amount", "prize_value": 500,
     "weight": 20, "coupon_prefix": "SPIN", "total_win_cap": None, "sort": 2},
    {"label": "TRY AGAIN", "prize_type": "none", "prize_value": None,
     "weight": 55, "coupon_prefix": "SPIN", "total_win_cap": None, "sort": 3},
]


def make_promo(**kw):
    cleaned = {
        "name": "Test Wheel", "type": "spin_wheel", "status": "active",
        "trigger_delay_sec": 9, "trigger_scroll_pct": 25, "pages": ["all"],
        "start_at": None, "end_at": None, "headline": "SPIN TO WIN",
        "subheadline": "Win up to 25% off!", "button_text": "SPIN NOW",
        "email_required": 1, "once_per_visitor": 1, "segments": SEGMENTS,
    }
    cleaned.update(kw)
    return spinpromo.save_spin_promo(cleaned)


def test_active_api_and_impression():
    pid = make_promo()
    c = app.test_client()
    r = c.get("/api/spin/active?page=home")
    assert r.status_code == 200, r.status_code
    data = r.get_json()
    assert data["promo"]["id"] == pid
    assert len(data["segments"]) == 4
    # weights shown as percentages
    pcts = [s["pct"] for s in data["segments"]]
    assert abs(sum(pcts) - 100.0) < 0.2, pcts
    r = c.post("/api/spin/impression", json={"promo_id": pid})
    assert r.get_json()["ok"] is True
    st = spinpromo.promo_stats(pid)
    assert st["impressions"] == 1, st


def test_spin_is_weighted_and_server_decided():
    pid = make_promo(name="Weighted")
    c = app.test_client()
    labels = []
    for _ in range(60):
        r = c.post("/api/spin/spin", json={"promo_id": pid})
        d = r.get_json()
        assert r.status_code == 200 and d["ok"], d
        labels.append(d["label"])
        assert 0 <= d["segment_index"] <= 3
    # TRY AGAIN (weight 55/100) should dominate; 25% OFF (5/100) should be rare
    assert labels.count("TRY AGAIN") > 20, labels.count("TRY AGAIN")
    assert labels.count("25% OFF") < 15, labels.count("25% OFF")
    st = spinpromo.promo_stats(pid)
    assert st["spins"] == 60, st


def test_claim_issues_real_usable_code():
    pid = make_promo(name="Claim")
    c = app.test_client()
    # force a win: spin until we win (or fake by spinning with a rigged promo)
    win = None
    for _ in range(40):
        d = c.post("/api/spin/spin", json={"promo_id": pid}).get_json()
        if d["won"]:
            win = d
            break
    assert win, "no win in 40 spins (odds say ~45% win rate)"
    # bad email rejected
    r = c.post("/api/spin/claim", json={"win_id": win["win_id"],
                                        "email": "not-an-email"})
    assert r.status_code == 400
    # good email -> real code
    r = c.post("/api/spin/claim", json={"win_id": win["win_id"],
                                        "email": "buyer@example.com"})
    d = r.get_json()
    assert r.status_code == 200 and d["ok"], d
    code = d["code"]
    assert re.match(r"^SPIN-[A-Z0-9]{6}$", code), code
    # double-claim rejected
    r = c.post("/api/spin/claim", json={"win_id": win["win_id"],
                                        "email": "other@example.com"})
    assert r.status_code == 409
    # the code is a REAL promo code the checkout accepts
    promo = landing.get_promo(code)
    assert promo and promo["active"] and promo["max_uses"] == 1
    assert landing.promo_is_usable(promo)
    # apply it on the cart like a shopper would
    with c.session_transaction() as s:
        s.clear()
    r = c.post("/cart/promo", data={"code": code}, follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    with c.session_transaction() as s:
        assert s.get("promo_code") == code
    st = spinpromo.promo_stats(pid)
    assert st["emails"] == 1 and st["codes_issued"] == 1, st


def test_win_cap_respected():
    segs = [
        dict(SEGMENTS[0], total_win_cap=2),  # 25% OFF capped at 2 wins
        dict(SEGMENTS[3]),                   # TRY AGAIN
    ]
    pid = make_promo(name="Capped", segments=segs)
    c = app.test_client()
    wins25 = 0
    for _ in range(30):
        d = c.post("/api/spin/spin", json={"promo_id": pid}).get_json()
        if d.get("won") and d["label"] == "25% OFF":
            wins25 += 1
    assert wins25 <= 2, wins25


def test_single_active_promo_enforcement():
    con = db._connect()
    for t in ("spin_wins", "spin_events", "spin_segments", "spin_promos"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.close()
    make_promo(name="First", pages=["home"])
    cleaned = {
        "name": "Second", "type": "spin_wheel", "status": "active",
        "trigger_delay_sec": 9, "trigger_scroll_pct": 25, "pages": ["home"],
        "start_at": None, "end_at": None, "headline": "H", "subheadline": "S",
        "button_text": "SPIN", "email_required": 1, "once_per_visitor": 1,
        "segments": SEGMENTS,
    }
    _, errors = spinpromo.validate_spin_promo_form(_fake_form(cleaned),
                                                   is_new=True)
    assert any("already covers" in e for e in errors), errors
    # non-overlapping scope is fine
    cleaned["pages"] = ["product"]
    _, errors = spinpromo.validate_spin_promo_form(_fake_form(cleaned),
                                                   is_new=True)
    assert not any("already covers" in e for e in errors), errors


def _fake_form(cleaned):
    class F(dict):
        def getlist(self, k):
            v = self.get(k)
            return v if isinstance(v, list) else ([v] if v else [])
    f = F()
    for k, v in cleaned.items():
        if k == "segments":
            for i, s in enumerate(v):
                f[f"seg_label_{i}"] = s["label"]
                f[f"seg_prize_type_{i}"] = s["prize_type"]
                f[f"seg_prize_value_{i}"] = (
                    "" if s["prize_value"] is None
                    else (str(s["prize_value"]) if s["prize_type"] == "percent"
                          else "%.2f" % (s["prize_value"] / 100)))
                f[f"seg_weight_{i}"] = str(s["weight"])
                f[f"seg_prefix_{i}"] = s["coupon_prefix"]
                f[f"seg_cap_{i}"] = ("" if s["total_win_cap"] is None
                                     else str(s["total_win_cap"]))
        elif k in ("email_required", "once_per_visitor"):
            f[k] = "on" if v else ""
        elif isinstance(v, list):
            f[k] = v
        elif v is None:
            f[k] = ""
        else:
            f[k] = str(v)
    return f


def test_admin_crud_and_stats_page():
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_authed"] = True
    r = c.get("/admin/spin-promos")
    assert r.status_code == 200
    assert b"Spin-to-Win Promos" in r.data
    # create via form post
    pid_holder = {}
    cleaned = {
        "name": "Admin Wheel", "type": "spin_wheel", "status": "draft",
        "trigger_delay_sec": "8", "trigger_scroll_pct": "25",
        "pages": ["home", "shop"], "start_at": "", "end_at": "",
        "headline": "SPIN TO WIN", "subheadline": "Win big!",
        "button_text": "SPIN NOW", "email_required": "on",
        "once_per_visitor": "on", "segments": SEGMENTS,
    }
    form = _fake_form(cleaned)
    post = {}
    for k, v in form.items():
        post[k] = v
    r = c.post("/admin/spin-promos/new", data=post, follow_redirects=False)
    assert r.status_code in (301, 302, 303), r.status_code
    promo = [p for p in spinpromo.list_promos() if p["name"] == "Admin Wheel"][0]
    pid_holder["id"] = promo["id"]
    assert promo["pages"] == ["home", "shop"]
    assert len(spinpromo.get_segments(promo["id"])) == 4
    # edit page loads with segments
    r = c.get(f"/admin/spin-promos/{promo['id']}/edit")
    assert r.status_code == 200 and b"25% OFF" in r.data
    # preview endpoint (admin only)
    r = c.get(f"/api/spin/preview/{promo['id']}")
    assert r.status_code == 200
    assert r.get_json()["promo"]["preview"] is True
    # archive
    r = c.post(f"/admin/spin-promos/{promo['id']}/archive",
               follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert spinpromo.get_promo(promo["id"])["status"] == "archived"


def test_no_headlight_word_and_caps():
    import glob
    # user-facing copy only: spinpromo.py legitimately contains the word in
    # its admin-side guard that *blocks* it from promo copy.
    for path in (["static/js/spinwheel.js",
                  "templates/spin_promo_list.html",
                  "templates/spin_promo_form.html"]):
        text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 path)).read()
        assert "headlight" not in text.lower(), path
        assert "Bravo LEDs" not in text or "BRAVO LEDS" in text, path
    # spinpromo.py: the word may only appear inside the blocking guard
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "spinpromo.py")).read()
    assert src.lower().count("headlight") == 2  # the guard + its message


if __name__ == "__main__":
    test_active_api_and_impression()
    print("ok active+impression")
    test_spin_is_weighted_and_server_decided()
    print("ok weighted spin")
    test_claim_issues_real_usable_code()
    print("ok claim+checkout")
    test_win_cap_respected()
    print("ok win cap")
    test_single_active_promo_enforcement()
    print("ok single-active")
    test_admin_crud_and_stats_page()
    print("ok admin crud")
    test_no_headlight_word_and_caps()
    print("ok copy rules")
    print("ALL SPIN PROMO TESTS PASSED")
