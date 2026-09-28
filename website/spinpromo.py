"""Spin-to-win promo popups for the Bravo LEDs storefront.

Public flow (all JS-driven, lazy-loaded from static/js/spinwheel.js):
  GET  /api/spin/active?page=<home|shop|product|other>
  POST /api/spin/impression   {promo_id}
  POST /api/spin/spin         {promo_id}            -> weighted segment, server-side
  POST /api/spin/claim        {win_id, email}       -> issues real coupon code

Prizes are REAL promo codes: winners get unique single-use rows in the
existing promo_codes table (kind percent|amount, max_uses=1, 14-day expiry),
so they flow through the normal cart/checkout/Stripe discount path with no
checkout changes.

Admin (under /admin, same auth as the rest of the site):
  /admin/spin-promos, /new, /<id>/edit, /<id>/archive (POST), /<id>/stats

Design notes:
- Spin outcome is decided server-side with weighted probabilities; the
  client only renders the segment the server chose.
- Spin first, email second: the visitor spins, then an email is required to
  reveal/claim the code (gamification before the ask converts better).
- Popups show at most once per visitor per promo (cookie) and never to
  someone who already spun or joined the email list.
- Only one promo may be active at a time for a given page scope (enforced
  on save).
"""
import json
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, abort, jsonify, redirect, render_template,
                   request, session, url_for)

from db import _connect  # reuse the store's SQLite connection helper

bp = Blueprint("spinpromo", __name__)

# ---------------------------------------------------------------- constants
PAGE_CHOICES = ("all", "home", "shop", "product")
STATUS_CHOICES = ("active", "draft", "archived")
PRIZE_CHOICES = ("percent", "amount", "none")
CODE_DAYS_VALID = 14
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no lookalikes


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- schema
def ensure_spin_schema():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS spin_promos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'spin_wheel',
            status TEXT NOT NULL DEFAULT 'draft',
            trigger_delay_sec INTEGER NOT NULL DEFAULT 9,
            trigger_scroll_pct INTEGER NOT NULL DEFAULT 25,
            pages TEXT NOT NULL DEFAULT '["all"]',
            start_at TEXT,
            end_at TEXT,
            headline TEXT NOT NULL DEFAULT '',
            subheadline TEXT NOT NULL DEFAULT '',
            button_text TEXT NOT NULL DEFAULT 'SPIN NOW',
            email_required INTEGER NOT NULL DEFAULT 1,
            once_per_visitor INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS spin_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_id INTEGER NOT NULL
                REFERENCES spin_promos(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            prize_type TEXT NOT NULL,
            prize_value INTEGER,
            weight INTEGER NOT NULL DEFAULT 1,
            coupon_prefix TEXT NOT NULL DEFAULT 'SPIN',
            total_win_cap INTEGER,
            sort INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS spin_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            code TEXT,
            email TEXT,
            created_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS spin_wins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_id INTEGER NOT NULL,
            segment_id INTEGER NOT NULL,
            code TEXT UNIQUE,
            email TEXT,
            prize_type TEXT NOT NULL,
            prize_value INTEGER,
            expires_at TEXT,
            claimed_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS spin_emails (
            email TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()
    _seed_spin_defaults()


def _seed_spin_defaults():
    """One draft starter wheel, inserted only if the owner has no promos.
    Draft = never shows until the owner activates it. A re-seed never
    touches rows the owner already edited or deleted."""
    con = _connect()
    if con.execute("SELECT 1 FROM spin_promos LIMIT 1").fetchone():
        con.close()
        return
    now = _now_iso()
    cur = con.execute(
        """INSERT INTO spin_promos
           (name, type, status, trigger_delay_sec, trigger_scroll_pct,
            pages, start_at, end_at, headline, subheadline, button_text,
            email_required, once_per_visitor, created_at, updated_at)
           VALUES (?, 'spin_wheel', 'draft', 9, 25, '["all"]', NULL, NULL,
                   'SPIN TO WIN', 'Spin for a chance at up to 15% off!',
                   'SPIN NOW', 1, 1, ?, ?)""",
        ("Welcome Spin", now, now))
    pid = cur.lastrowid
    for i, (label, ptype, pval, weight) in enumerate([
            ("15% OFF", "percent", 15, 8),
            ("10% OFF", "percent", 10, 22),
            ("$5 OFF", "amount", 500, 22),
            ("TRY AGAIN", "none", None, 48)]):
        con.execute(
            """INSERT INTO spin_segments
               (promo_id, label, prize_type, prize_value, weight,
                coupon_prefix, total_win_cap, sort)
               VALUES (?, ?, ?, ?, ?, 'SPIN', NULL, ?)""",
            (pid, label, ptype, pval, weight, i))
    con.commit()
    con.close()


# ---------------------------------------------------------------- helpers
def _row(d):
    return dict(d)


def _parse_pages(raw):
    try:
        pages = json.loads(raw or "[]")
    except (ValueError, TypeError):
        pages = []
    return [p for p in pages if p in PAGE_CHOICES] or ["all"]


def _promo_active_now(promo, now=None):
    now = now or _now()
    if promo["status"] != "active":
        return False
    try:
        if promo.get("start_at") and datetime.fromisoformat(promo["start_at"]) > now:
            return False
        if promo.get("end_at") and datetime.fromisoformat(promo["end_at"]) <= now:
            return False
    except ValueError:
        return False
    return True


def _pages_overlap(a, b):
    return "all" in a or "all" in b or bool(set(a) & set(b))


def get_promo(pid):
    ensure_spin_schema()
    con = _connect()
    r = con.execute("SELECT * FROM spin_promos WHERE id = ?", (pid,)).fetchone()
    con.close()
    if not r:
        return None
    d = _row(r)
    d["pages"] = _parse_pages(d["pages"])
    return d


def list_promos(include_archived=True):
    ensure_spin_schema()
    con = _connect()
    q = "SELECT * FROM spin_promos ORDER BY status, id DESC"
    if not include_archived:
        q = ("SELECT * FROM spin_promos WHERE status != 'archived' "
             "ORDER BY status, id DESC")
    rows = con.execute(q).fetchall()
    con.close()
    out = []
    for r in rows:
        d = _row(r)
        d["pages"] = _parse_pages(d["pages"])
        out.append(d)
    return out


def get_segments(pid):
    ensure_spin_schema()
    con = _connect()
    rows = con.execute(
        "SELECT * FROM spin_segments WHERE promo_id = ? ORDER BY sort, id",
        (pid,)).fetchall()
    con.close()
    return [_row(r) for r in rows]


def active_promo_for_page(page):
    """The one active promo eligible for this page scope, or None."""
    now = _now()
    for p in list_promos(include_archived=False):
        if not _promo_active_now(p, now):
            continue
        pages = p["pages"]
        if "all" in pages or page in pages:
            return p
    return None


def promo_stats(pid):
    ensure_spin_schema()
    con = _connect()
    counts = dict(con.execute(
        "SELECT event, COUNT(*) c FROM spin_events WHERE promo_id = ? "
        "GROUP BY event", (pid,)).fetchall())
    codes_issued = con.execute(
        "SELECT COUNT(*) FROM spin_wins WHERE promo_id = ? AND code IS NOT NULL",
        (pid,)).fetchone()[0]
    redeemed = con.execute(
        """SELECT COUNT(*) FROM spin_wins w
           JOIN promo_codes p ON p.code = w.code
           WHERE w.promo_id = ? AND p.used_count > 0""",
        (pid,)).fetchone()[0]
    con.close()
    return {
        "impressions": counts.get("impression", 0),
        "spins": counts.get("spin", 0),
        "emails": counts.get("email", 0),
        "codes_issued": codes_issued,
        "codes_redeemed": redeemed,
    }


def log_event(pid, event, code=None, email=None):
    ensure_spin_schema()
    con = _connect()
    con.execute(
        "INSERT INTO spin_events (promo_id, event, code, email, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, event, code, email, _now_iso()))
    con.commit()
    con.close()

# ---------------------------------------------------------------- spin engine
def _eligible_segments(pid):
    """Segments that can still be won (respects total_win_cap)."""
    ensure_spin_schema()
    con = _connect()
    segs = [_row(r) for r in con.execute(
        "SELECT * FROM spin_segments WHERE promo_id = ? ORDER BY sort, id",
        (pid,)).fetchall()]
    out = []
    for s in segs:
        if s["prize_type"] == "none":
            out.append(s)
            continue
        if s.get("total_win_cap") is not None:
            won = con.execute(
                "SELECT COUNT(*) FROM spin_wins WHERE segment_id = ?",
                (s["id"],)).fetchone()[0]
            if won >= s["total_win_cap"]:
                continue
        out.append(s)
    con.close()
    return out


def _weighted_pick(segments):
    total = sum(max(1, int(s.get("weight") or 1)) for s in segments)
    roll = random.uniform(0, total)
    acc = 0
    for s in segments:
        acc += max(1, int(s.get("weight") or 1))
        if roll < acc:
            return s
    return segments[-1]


def _unique_code(prefix):
    ensure_spin_schema()
    con = _connect()
    for _ in range(20):
        code = "%s-%s" % (prefix.upper(),
                          "".join(secrets.choice(CODE_ALPHABET)
                                  for _ in range(6)))
        if not con.execute("SELECT 1 FROM promo_codes WHERE code = ?",
                           (code,)).fetchone():
            con.close()
            return code
    con.close()
    raise RuntimeError("could not mint a unique spin code")


def issue_prize_code(promo_id, segment, email):
    """Create the real single-use promo code for a won segment.

    Returns (code, expires_at_iso). The row lands in the existing
    promo_codes table, so cart/checkout/Stripe apply it with zero
    checkout changes.
    """
    from landing import normalize_code  # local import: landing is standalone
    ensure_spin_schema()
    kind = segment["prize_type"]
    prefix = (segment.get("coupon_prefix") or "SPIN").strip() or "SPIN"
    code = _unique_code(prefix)
    code = normalize_code(code)
    expires_at = (_now() + timedelta(days=CODE_DAYS_VALID)
                  ).isoformat(timespec="seconds")
    percent = segment["prize_value"] if kind == "percent" else None
    amount_cents = segment["prize_value"] if kind == "amount" else None
    con = _connect()
    con.execute(
        """INSERT INTO promo_codes
           (code, kind, percent, amount_cents, expires_at,
            max_uses, used_count, active, created_at)
           VALUES (?, ?, ?, ?, ?, 1, 0, 1, ?)""",
        (code, kind, percent, amount_cents, expires_at, _now_iso()))
    con.commit()
    con.close()
    return code, expires_at


def prize_describe(segment):
    if segment["prize_type"] == "percent":
        return "%d%% off your order" % int(segment["prize_value"])
    if segment["prize_type"] == "amount":
        return "$%s off your order" % ("%.2f" % (segment["prize_value"] / 100))
    return "No prize this time"


# ---------------------------------------------------------------- public API
@bp.route("/api/spin/active")
def api_spin_active():
    """Config for the promo eligible on this page (or null)."""
    page = (request.args.get("page") or "other").strip().lower()
    if page not in ("home", "shop", "product"):
        page = "other"
    promo = active_promo_for_page(page)
    if not promo:
        return jsonify({"promo": None})
    segs = get_segments(promo["id"])
    total_w = sum(max(1, int(s.get("weight") or 1)) for s in segs) or 1
    return jsonify({
        "promo": {
            "id": promo["id"],
            "headline": promo["headline"],
            "subheadline": promo["subheadline"],
            "button_text": promo["button_text"],
            "trigger_delay_sec": promo["trigger_delay_sec"],
            "trigger_scroll_pct": promo["trigger_scroll_pct"],
            "email_required": bool(promo["email_required"]),
            "once_per_visitor": bool(promo["once_per_visitor"]),
        },
        "segments": [
            {"label": s["label"],
             "pct": round(100.0 * max(1, int(s.get("weight") or 1)) / total_w, 1)}
            for s in segs
        ],
    })


@bp.route("/api/spin/impression", methods=["POST"])
def api_spin_impression():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get("promo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    log_event(pid, "impression")
    return jsonify({"ok": True})


@bp.route("/api/spin/spin", methods=["POST"])
def api_spin_spin():
    data = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(data.get("promo_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad promo"}), 400
    promo = get_promo(pid)
    if not promo or not _promo_active_now(promo):
        return jsonify({"ok": False, "error": "promo not active"}), 410
    segs = _eligible_segments(pid)
    if not segs:
        return jsonify({"ok": False, "error": "no segments"}), 410
    # Segment index in the client's full ordered list (for wheel landing).
    full = get_segments(pid)
    chosen = _weighted_pick(segs)
    try:
        seg_index = [s["id"] for s in full].index(chosen["id"])
    except ValueError:
        seg_index = 0
    log_event(pid, "spin")

    if chosen["prize_type"] == "none":
        return jsonify({"ok": True, "won": False,
                        "segment_index": seg_index,
                        "label": chosen["label"]})

    ensure_spin_schema()
    con = _connect()
    cur = con.execute(
        """INSERT INTO spin_wins
           (promo_id, segment_id, prize_type, prize_value, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (pid, chosen["id"], chosen["prize_type"], chosen["prize_value"],
         _now_iso()))
    win_id = cur.lastrowid
    con.commit()
    con.close()

    if not promo["email_required"]:
        # No email gate: issue the code immediately.
        code, expires_at = issue_prize_code(pid, chosen, None)
        con = _connect()
        con.execute(
            "UPDATE spin_wins SET code = ?, claimed_at = ?, expires_at = ? "
            "WHERE id = ?", (code, _now_iso(), expires_at, win_id))
        con.commit()
        con.close()
        log_event(pid, "email", code=code)
        return jsonify({"ok": True, "won": True, "win_id": win_id,
                        "segment_index": seg_index, "label": chosen["label"],
                        "code": code,
                        "prize_desc": prize_describe(chosen),
                        "expires_at": expires_at})
    return jsonify({"ok": True, "won": True, "win_id": win_id,
                    "segment_index": seg_index, "label": chosen["label"],
                    "prize_desc": prize_describe(chosen)})


@bp.route("/api/spin/claim", methods=["POST"])
def api_spin_claim():
    data = request.get_json(force=True, silent=True) or {}
    try:
        win_id = int(data.get("win_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad win"}), 400
    email = (data.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"ok": False,
                        "error": "Enter a valid email address."}), 400
    ensure_spin_schema()
    con = _connect()
    win = con.execute("SELECT * FROM spin_wins WHERE id = ?",
                      (win_id,)).fetchone()
    con.close()
    if not win:
        return jsonify({"ok": False, "error": "win not found"}), 404
    win = _row(win)
    if win["code"]:
        return jsonify({"ok": False, "error": "already claimed"}), 409
    promo = get_promo(win["promo_id"])
    if not promo or not _promo_active_now(promo):
        return jsonify({"ok": False, "error": "promo ended"}), 410
    seg = _row([s for s in get_segments(win["promo_id"])
                if s["id"] == win["segment_id"]][0])
    code, expires_at = issue_prize_code(win["promo_id"], seg, email)
    con = _connect()
    con.execute("UPDATE spin_wins SET code = ?, email = ?, claimed_at = ?, "
                "expires_at = ? WHERE id = ?",
                (code, email, _now_iso(), expires_at, win_id))
    con.execute("INSERT OR IGNORE INTO spin_emails (email, created_at) "
                "VALUES (?, ?)", (email, _now_iso()))
    con.commit()
    con.close()
    log_event(win["promo_id"], "email", code=code, email=email)
    return jsonify({"ok": True, "code": code,
                    "prize_desc": prize_describe(seg),
                    "expires_at": expires_at})

# ---------------------------------------------------------------- admin
def _spin_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def _parse_int(form, key, default=None, lo=None, hi=None):
    try:
        v = int((form.get(key) or "").strip())
    except (TypeError, ValueError, AttributeError):
        return default
    if lo is not None and v < lo:
        return default
    if hi is not None and v > hi:
        return default
    return v


def _parse_segments(form):
    """Read the dynamic segment rows: seg_label_0, seg_prize_type_0, ..."""
    segs, errors = [], []
    for i in range(24):  # cap: 24 wheel slices max
        label = (form.get(f"seg_label_{i}") or "").strip()
        if not label and i > 0:
            # trailing empties are fine; a gap mid-list is not
            continue
        if not label:
            continue
        ptype = (form.get(f"seg_prize_type_{i}") or "").strip()
        if ptype not in PRIZE_CHOICES:
            errors.append(f"Row {i + 1}: prize type must be percent, amount, or none.")
            continue
        value = None
        if ptype == "percent":
            value = _parse_int(form, f"seg_prize_value_{i}", lo=1, hi=90)
            if value is None:
                errors.append(f"Row {i + 1}: percent must be 1–90.")
                continue
        elif ptype == "amount":
            try:
                dollars = float((form.get(f"seg_prize_value_{i}") or "").strip())
                value = int(round(dollars * 100))
            except (TypeError, ValueError):
                value = 0
            if not 1 <= value <= 50000:
                errors.append(f"Row {i + 1}: amount must be $0.01–$500.")
                continue
        weight = _parse_int(form, f"seg_weight_{i}", lo=1, hi=1000)
        if weight is None:
            errors.append(f"Row {i + 1}: weight must be 1–1000.")
            continue
        prefix = (form.get(f"seg_prefix_{i}") or "SPIN").strip().upper() or "SPIN"
        if not re.match(r"^[A-Z0-9]{2,8}$", prefix):
            errors.append(f"Row {i + 1}: code prefix must be 2–8 letters/digits.")
            continue
        cap_raw = (form.get(f"seg_cap_{i}") or "").strip()
        cap = None
        if cap_raw:
            cap = _parse_int(form, f"seg_cap_{i}", lo=1, hi=100000)
            if cap is None:
                errors.append(f"Row {i + 1}: win cap must be a positive number.")
                continue
        segs.append({"label": label[:40], "prize_type": ptype,
                     "prize_value": value, "weight": weight,
                     "coupon_prefix": prefix, "total_win_cap": cap,
                     "sort": len(segs)})
    return segs, errors


def validate_spin_promo_form(form, is_new):
    errors, cleaned = [], {}
    name = (form.get("name") or "").strip()
    if not name:
        errors.append("Name is required.")
    cleaned["name"] = name[:80]
    ptype = (form.get("type") or "spin_wheel").strip()
    if ptype not in ("spin_wheel", "modal"):
        errors.append("Type must be spin_wheel or modal.")
    cleaned["type"] = ptype
    status = (form.get("status") or "draft").strip()
    if status not in STATUS_CHOICES:
        errors.append("Status must be active, draft, or archived.")
    cleaned["status"] = status
    delay = _parse_int(form, "trigger_delay_sec", lo=0, hi=120)
    if delay is None:
        errors.append("Trigger delay must be 0–120 seconds.")
    cleaned["trigger_delay_sec"] = delay if delay is not None else 9
    scroll = _parse_int(form, "trigger_scroll_pct", lo=0, hi=100)
    if scroll is None:
        errors.append("Trigger scroll must be 0–100%.")
    cleaned["trigger_scroll_pct"] = scroll if scroll is not None else 25
    pages = [p for p in form.getlist("pages") if p in PAGE_CHOICES] or ["all"]
    cleaned["pages"] = pages
    for key in ("start_at", "end_at"):
        raw = (form.get(key) or "").strip()
        if raw:
            try:
                datetime.fromisoformat(raw)
            except ValueError:
                errors.append(f"{key} must be a valid date/time.")
                raw = ""
        cleaned[key] = raw or None
    if cleaned.get("start_at") and cleaned.get("end_at") \
            and cleaned["start_at"] >= cleaned["end_at"]:
        errors.append("Start must be before end.")
    for key in ("headline", "subheadline", "button_text"):
        val = (form.get(key) or "").strip()
        if "headlight" in val.lower():
            errors.append(f"{key}: the word 'headlight' is not allowed.")
        cleaned[key] = val[:120]
    if not cleaned["headline"]:
        errors.append("Headline is required.")
    cleaned["email_required"] = 1 if form.get("email_required") else 0
    cleaned["once_per_visitor"] = 1 if form.get("once_per_visitor") else 0
    segs, seg_errors = _parse_segments(form)
    errors.extend(seg_errors)
    if not segs:
        errors.append("Add at least one wheel segment.")
    if len(segs) < 2:
        errors.append("A wheel needs at least 2 segments.")
    cleaned["segments"] = segs
    # Single-active-promo enforcement per page scope.
    if cleaned["status"] == "active" and not errors:
        me = _parse_int(form, "promo_id") if not is_new else None
        for other in list_promos(include_archived=False):
            if me and other["id"] == me:
                continue
            if other["status"] == "active" and _pages_overlap(
                    other["pages"], cleaned["pages"]):
                errors.append(
                    "Another active promo ('%s') already covers %s. "
                    "Archive it or narrow the page scope first."
                    % (other["name"], ", ".join(other["pages"])))
                break
    return cleaned, errors


def save_spin_promo(cleaned, pid=None):
    ensure_spin_schema()
    con = _connect()
    now = _now_iso()
    if pid is None:
        cur = con.execute(
            """INSERT INTO spin_promos
               (name, type, status, trigger_delay_sec, trigger_scroll_pct,
                pages, start_at, end_at, headline, subheadline, button_text,
                email_required, once_per_visitor, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cleaned["name"], cleaned["type"], cleaned["status"],
             cleaned["trigger_delay_sec"], cleaned["trigger_scroll_pct"],
             json.dumps(cleaned["pages"]), cleaned["start_at"],
             cleaned["end_at"], cleaned["headline"], cleaned["subheadline"],
             cleaned["button_text"], cleaned["email_required"],
             cleaned["once_per_visitor"], now, now))
        pid = cur.lastrowid
    else:
        con.execute(
            """UPDATE spin_promos SET name=?, type=?, status=?,
               trigger_delay_sec=?, trigger_scroll_pct=?, pages=?,
               start_at=?, end_at=?, headline=?, subheadline=?,
               button_text=?, email_required=?, once_per_visitor=?,
               updated_at=? WHERE id=?""",
            (cleaned["name"], cleaned["type"], cleaned["status"],
             cleaned["trigger_delay_sec"], cleaned["trigger_scroll_pct"],
             json.dumps(cleaned["pages"]), cleaned["start_at"],
             cleaned["end_at"], cleaned["headline"], cleaned["subheadline"],
             cleaned["button_text"], cleaned["email_required"],
             cleaned["once_per_visitor"], now, pid))
        con.execute("DELETE FROM spin_segments WHERE promo_id = ?", (pid,))
    for s in cleaned["segments"]:
        con.execute(
            """INSERT INTO spin_segments
               (promo_id, label, prize_type, prize_value, weight,
                coupon_prefix, total_win_cap, sort)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, s["label"], s["prize_type"], s["prize_value"],
             s["weight"], s["coupon_prefix"], s["total_win_cap"], s["sort"]))
    con.commit()
    con.close()
    return pid


@bp.route("/admin/spin-promos")
@_spin_admin_required
def admin_spin_list():
    promos = list_promos()
    for p in promos:
        p["stats"] = promo_stats(p["id"])
        p["live_now"] = _promo_active_now(p)
    return render_template("spin_promo_list.html", promos=promos)


@bp.route("/admin/spin-promos/new", methods=["GET", "POST"])
@_spin_admin_required
def admin_spin_new():
    errors, form = [], None
    if request.method == "POST":
        form = request.form
        cleaned, errors = validate_spin_promo_form(form, is_new=True)
        if not errors:
            pid = save_spin_promo(cleaned)
            return redirect(url_for("spinpromo.admin_spin_list"))
    return render_template("spin_promo_form.html", promo=None, segs=[],
                           vals=_spin_form_vals(None, form),
                           errors=errors, is_new=True,
                           page_choices=PAGE_CHOICES)


@bp.route("/admin/spin-promos/<int:pid>/edit", methods=["GET", "POST"])
@_spin_admin_required
def admin_spin_edit(pid):
    promo = get_promo(pid)
    if not promo:
        abort(404)
    errors, form = [], None
    if request.method == "POST":
        form = request.form
        cleaned, errors = validate_spin_promo_form(form, is_new=False)
        if not errors:
            save_spin_promo(cleaned, pid)
            return redirect(url_for("spinpromo.admin_spin_list"))
    segs = get_segments(pid)
    return render_template("spin_promo_form.html", promo=promo, segs=segs,
                           vals=_spin_form_vals(promo, form),
                           errors=errors, is_new=False,
                           page_choices=PAGE_CHOICES)


@bp.route("/admin/spin-promos/<int:pid>/archive", methods=["POST"])
@_spin_admin_required
def admin_spin_archive(pid):
    promo = get_promo(pid)
    if not promo:
        abort(404)
    ensure_spin_schema()
    con = _connect()
    con.execute("UPDATE spin_promos SET status='archived', updated_at=? "
                "WHERE id=?", (_now_iso(), pid))
    con.commit()
    con.close()
    return redirect(url_for("spinpromo.admin_spin_list"))


@bp.route("/api/spin/preview/<int:pid>")
@_spin_admin_required
def api_spin_preview(pid):
    """Admin preview: config for any promo regardless of status/schedule."""
    promo = get_promo(pid)
    if not promo:
        abort(404)
    segs = get_segments(pid)
    total_w = sum(max(1, int(s.get("weight") or 1)) for s in segs) or 1
    return jsonify({
        "promo": {
            "id": promo["id"],
            "headline": promo["headline"],
            "subheadline": promo["subheadline"],
            "button_text": promo["button_text"],
            "trigger_delay_sec": 1,
            "trigger_scroll_pct": 0,
            "email_required": bool(promo["email_required"]),
            "once_per_visitor": False,
            "preview": True,
        },
        "segments": [
            {"label": s["label"],
             "pct": round(100.0 * max(1, int(s.get("weight") or 1)) / total_w, 1)}
            for s in segs
        ],
    })


def _spin_form_vals(promo, form):
    """Sticky form values: submitted form wins, else the stored promo."""
    def g(key, default=""):
        if form is not None:
            if key == "pages":
                return form.getlist("pages") or ["all"]
            return form.get(key, default)
        if promo is None:
            return default
        v = promo.get(key, default)
        if key == "pages":
            return v
        return v if v is not None else default
    return {
        "name": g("name"), "type": g("type", "spin_wheel"),
        "status": g("status", "draft"),
        "trigger_delay_sec": g("trigger_delay_sec", "9"),
        "trigger_scroll_pct": g("trigger_scroll_pct", "25"),
        "pages": g("pages"), "start_at": g("start_at"), "end_at": g("end_at"),
        "headline": g("headline", "SPIN TO WIN"),
        "subheadline": g("subheadline",
                         "Spin the wheel for a chance at up to 25% off!"),
        "button_text": g("button_text", "SPIN NOW"),
        "email_required": g("email_required", "on"),
        "once_per_visitor": g("once_per_visitor", "on"),
    }


def register_spin_routes(app):
    if bp.name in app.blueprints:
        return
    ensure_spin_schema()
    app.register_blueprint(bp)
