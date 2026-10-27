"""Abandoned-cart tracking + recovery for the BRAVO LEDS storefront.

The session cart is invisible to the store until checkout, so this module
mirrors every cart change into a server-side snapshot keyed by a long-lived
visitor cookie (bc_vid, 1 year). A snapshot records the items, the vehicle
they were picked for, and the subtotal — enough for the owner to see what
shoppers almost bought, and (once an email is known) to send ONE recovery
email with a link that restores the exact cart.

Email capture points:
  - Checkout: the email field POSTs here on change/blur (checkout.html JS),
    so an address typed but never submitted is still captured.
  - Cart page: the "Email me my cart" box POSTs here and immediately
    emails the restore link (that send is visitor-requested, so it is NOT
    gated by the recovery toggle below).

Recovery sender:
  send_due_recoveries() emails snapshots that are open, have an email,
  have a subtotal > 0, and have been untouched for 3+ hours. It is gated
  by the db setting "cart_recovery_enabled", which DEFAULTS TO OFF — the
  owner approves the email wording before anything sends. There is no
  cron in this stack, so app.py calls maybe_send_recoveries() from a
  before_request hook, throttled to one pass per 10 minutes per process.

Privacy: full emails are only ever shown in admin. Every recovery email
carries an unsubscribe link (same HMAC scheme as emailoffer.py); opting
out in either system stops both.

app.py wires its cart helpers in via register_carttrack_routes(app,
hooks={"cart_detailed": ..., "add_item": ...}) so this module never
imports app (no import cycle).
"""
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, after_this_request, g, jsonify, redirect,
                   render_template, request, session, url_for)

import db
import emailoffer
import emails
from db import _connect  # reuse the store's SQLite connection helper

bp = Blueprint("carttrack", __name__)

VISITOR_COOKIE = "bc_vid"
RECOVERY_SETTING = "cart_recovery_enabled"
RECOVERY_DELAY_HOURS = 3
RECOVERY_THROTTLE_SEC = 600

_hooks = {}
_last_recovery_run = 0.0


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat(timespec="seconds")


def _money(cents):
    return f"${(cents or 0) / 100:,.2f}"


# ---------------------------------------------------------------- schema
def ensure_cart_schema():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS cart_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_key TEXT NOT NULL,
            restore_token TEXT NOT NULL UNIQUE,
            email TEXT,
            vehicle_label TEXT NOT NULL DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            subtotal_cents INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            recovery_sent_at TEXT,
            order_id INTEGER
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_cart_snap_visitor"
                " ON cart_snapshots (session_key)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_cart_snap_status"
                " ON cart_snapshots (status, updated_at)")
    con.commit()
    con.close()


# ---------------------------------------------------------------- visitor
def visitor_token():
    """The persistent visitor id. Mints + sets the bc_vid cookie on first
    use (via after_this_request, since helpers run mid-request). The
    minted value is cached on flask.g — without that, several cart
    mutations inside ONE request (the kit add loops) would each mint a
    different token, because the cookie only exists on the response."""
    tok = request.cookies.get(VISITOR_COOKIE)
    if tok:
        return tok
    tok = getattr(g, "_bc_vid", None)
    if tok:
        return tok
    tok = secrets.token_urlsafe(24)
    g._bc_vid = tok

    @after_this_request
    def _set_cookie(resp):
        resp.set_cookie(VISITOR_COOKIE, tok, max_age=365 * 24 * 3600,
                        httponly=True, samesite="Lax")
        return resp
    return tok


def _session_vehicle_label():
    v = session.get("vehicle") or {}
    if isinstance(v, dict):
        return " ".join(str(v.get(k, "")).strip()
                        for k in ("year", "make", "model")
                        if str(v.get(k, "")).strip())[:60]
    return str(v)[:60]


# ---------------------------------------------------------------- snapshots
def get_snapshot(snap_id):
    ensure_cart_schema()
    con = _connect()
    r = con.execute("SELECT * FROM cart_snapshots WHERE id = ?",
                    (snap_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def _open_snapshot_for(tok):
    con = _connect()
    r = con.execute(
        "SELECT * FROM cart_snapshots WHERE session_key = ?"
        " AND status IN ('open', 'recovered_sent')"
        " ORDER BY id DESC LIMIT 1", (tok,)).fetchone()
    con.close()
    return dict(r) if r else None


def save_cart_snapshot(lines, subtotal_cents):
    """Mirror the current cart into a snapshot. Called by app.py at the
    end of every cart-mutating route with cart_detailed() output. An
    emptied cart clears its still-open snapshot (nothing left to recover);
    snapshots that already got a recovery email or became an order are
    kept as history."""
    ensure_cart_schema()
    tok = visitor_token()
    row = _open_snapshot_for(tok)
    if not lines:
        if row and row["status"] == "open":
            con = _connect()
            con.execute("DELETE FROM cart_snapshots WHERE id = ?",
                        (row["id"],))
            con.commit()
            con.close()
        return None
    vehicle_label = ""
    for l in lines:
        if l.get("vehicle"):
            vehicle_label = l["vehicle"]
            break
    if not vehicle_label:
        vehicle_label = _session_vehicle_label()
    items = [{
        "name": (l.get("product") or {}).get("name", ""),
        "size": l.get("size", ""),
        "product_id": (l.get("product") or {}).get("id", ""),
        "variation_id": l.get("variation_id"),
        "qty": l.get("qty", 1),
        "unit_price_cents": l.get("unit_price_cents", 0),
        "vehicle": l.get("vehicle", "") or "",
    } for l in lines]
    now = _now_iso()
    con = _connect()
    if row:
        con.execute(
            "UPDATE cart_snapshots SET vehicle_label = ?, items_json = ?,"
            " subtotal_cents = ?, updated_at = ? WHERE id = ?",
            (vehicle_label, json.dumps(items), subtotal_cents, now,
             row["id"]))
        snap_id = row["id"]
    else:
        cur = con.execute(
            "INSERT INTO cart_snapshots"
            " (session_key, restore_token, email, vehicle_label,"
            "  items_json, subtotal_cents, created_at, updated_at, status)"
            " VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'open')",
            (tok, secrets.token_urlsafe(24), vehicle_label,
             json.dumps(items), subtotal_cents, now, now))
        snap_id = cur.lastrowid
    con.commit()
    con.close()
    return snap_id


def mark_ordered(order_id=None, email="", visitor=None):
    """Flag this visitor's open snapshots as ordered once checkout
    creates an order. Matches the visitor cookie first, then the email."""
    ensure_cart_schema()
    now = _now_iso()
    con = _connect()
    changed = 0
    if visitor:
        cur = con.execute(
            "UPDATE cart_snapshots SET status = 'ordered',"
            " order_id = COALESCE(?, order_id), updated_at = ?"
            " WHERE session_key = ? AND status IN ('open', 'recovered_sent')",
            (order_id, now, visitor))
        changed = cur.rowcount
    if not changed and email:
        con.execute(
            "UPDATE cart_snapshots SET status = 'ordered',"
            " order_id = COALESCE(?, order_id), updated_at = ?"
            " WHERE email = ? AND status IN ('open', 'recovered_sent')",
            (order_id, now, (email or "").strip().lower()))
    con.commit()
    con.close()


# ---------------------------------------------------------------- email in
def _items_text(snap):
    try:
        items = json.loads(snap.get("items_json") or "[]")
    except ValueError:
        items = []
    out = []
    for it in items:
        size = f" (Size {it['size']})" if it.get("size") else ""
        out.append(f"- {it.get('name', '')}{size} x {it.get('qty', 1)}"
                   f" — {_money(it.get('unit_price_cents', 0))}")
    return "\n".join(out)


def _restore_url(snap):
    base = db.get_setting("site_base_url",
                          "https://bravo-leds-store.onrender.com").rstrip("/")
    return f"{base}/cart/restore/{snap['restore_token']}"


def _send_cart_link_email(snap):
    """The visitor explicitly asked for their cart link from the cart
    page — send it right away (not gated by the recovery toggle)."""
    body = (
        "Here is the cart you saved at BRAVO LEDS:\n"
        "\n"
        + _items_text(snap) + "\n"
        "\n"
        f"Cart subtotal: {_money(snap['subtotal_cents'])}\n"
        "\n"
        "Pick up right where you left off — this link restores these "
        "exact items to your cart:\n"
        + _restore_url(snap) + "\n"
        "\n"
        "Prices are confirmed at checkout.\n"
        "\n"
        "BRAVO LEDS by 626 LEDs — Rosemead, CA\n"
    )
    return emails.send_email_channel(
        snap["email"], "Your saved cart at BRAVO LEDS", body)


def attach_email(email, source=""):
    """Record an email on the visitor's open snapshot (checkout capture
    or the cart-page box). With source='cart' the restore link is emailed
    immediately, as the box promises. Returns the snapshot or None."""
    email = (email or "").strip().lower()
    if not emailoffer.EMAIL_RE.match(email) or len(email) > 254:
        return None
    ensure_cart_schema()
    row = _open_snapshot_for(visitor_token())
    if not row:
        return None
    status = ("unsubscribed" if emailoffer.email_is_unsubscribed(email)
              else row["status"])
    con = _connect()
    con.execute("UPDATE cart_snapshots SET email = ?, status = ?,"
                " updated_at = ? WHERE id = ?",
                (email, status, _now_iso(), row["id"]))
    con.commit()
    con.close()
    snap = get_snapshot(row["id"])
    if (source == "cart" and snap and snap["subtotal_cents"] > 0
            and status != "unsubscribed"):
        _send_cart_link_email(snap)
    return snap


@bp.route("/api/carttrack/email", methods=["POST"])
def api_carttrack_email():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip()
    if not emailoffer.EMAIL_RE.match(email.lower()) or len(email) > 254:
        return jsonify({"ok": False,
                        "error": "Enter a valid email address."}), 400
    snap = attach_email(email, source=str(data.get("source") or "")[:20])
    if not snap:
        return jsonify({"ok": False,
                        "error": "Nothing to save — your cart is empty."}
                       ), 400
    return jsonify({"ok": True})


# ---------------------------------------------------------------- restore
@bp.route("/cart/restore/<token>")
def cart_restore(token):
    ensure_cart_schema()
    con = _connect()
    r = con.execute("SELECT * FROM cart_snapshots WHERE restore_token = ?",
                    (token,)).fetchone()
    con.close()
    if not r:
        return redirect("/cart")
    snap = dict(r)
    try:
        items = json.loads(snap.get("items_json") or "[]")
    except ValueError:
        items = []
    add_item = _hooks.get("add_item")
    if add_item:
        session["cart"] = {}
        for it in items:
            try:
                add_item(it.get("product_id", ""),
                         it.get("variation_id", ""),
                         it.get("qty", 1), it.get("vehicle", ""))
            except Exception:  # noqa: BLE001 - skip lines that fail
                continue
        detailed = _hooks.get("cart_detailed")
        if detailed:
            lines, subtotal = detailed()
            save_cart_snapshot(lines, subtotal)
    return redirect("/cart")


@bp.route("/cart/unsubscribe")
def cart_unsubscribe():
    import hmac as _hmac
    email = (request.args.get("e") or "").strip().lower()
    token = (request.args.get("t") or "").strip()
    ok = bool(email and token and _hmac.compare_digest(
        token, emailoffer.unsubscribe_token(email)))
    if ok:
        ensure_cart_schema()
        con = _connect()
        con.execute(
            "UPDATE cart_snapshots SET status = 'unsubscribed'"
            " WHERE email = ? AND status IN ('open', 'recovered_sent')",
            (email,))
        con.commit()
        con.close()
        emailoffer.mark_unsubscribed(email)  # one opt-out stops both
    return render_template("offer_unsubscribed.html", ok=ok), (200 if ok
                                                               else 400)


# ---------------------------------------------------------------- recovery
def _send_recovery_email(snap):
    vehicle_line = (f"For your {snap['vehicle_label']}:\n"
                    if snap.get("vehicle_label") else "")
    body = (
        "Hi there,\n"
        "\n"
        "You left a few things in your cart at BRAVO LEDS:\n"
        "\n"
        + vehicle_line
        + _items_text(snap) + "\n"
        "\n"
        f"Cart subtotal: {_money(snap['subtotal_cents'])}\n"
        "\n"
        "Pick up right where you left off — your cart is saved here:\n"
        + _restore_url(snap) + "\n"
        "\n"
        "That link restores these exact items to your cart. Prices are "
        "confirmed at checkout.\n"
        "\n"
        "BRAVO LEDS by 626 LEDs — Rosemead, CA\n"
        "\n"
        "Don't want cart reminders? Unsubscribe: "
        + emailoffer.unsubscribe_url(snap["email"],
                                     path="/cart/unsubscribe") + "\n"
    )
    return emails.send_email_channel(
        snap["email"], "You left something in your cart at BRAVO LEDS",
        body)


def send_due_recoveries():
    """Send ONE recovery email per due snapshot. Hard-gated by the
    cart_recovery_enabled setting (default OFF — wording not yet
    owner-approved). A snapshot is only marked sent when SMTP actually
    accepted the message, so an unconfigured mailer never burns the one
    send. Returns how many emails went out."""
    if db.get_setting(RECOVERY_SETTING, "0") != "1":
        return 0
    ensure_cart_schema()
    cutoff = (_now() - timedelta(hours=RECOVERY_DELAY_HOURS)
              ).isoformat(timespec="seconds")
    con = _connect()
    rows = con.execute(
        "SELECT * FROM cart_snapshots WHERE status = 'open'"
        " AND email IS NOT NULL AND email != '' AND subtotal_cents > 0"
        " AND recovery_sent_at IS NULL AND updated_at <= ?",
        (cutoff,)).fetchall()
    con.close()
    sent = 0
    for r in rows:
        snap = dict(r)
        if emailoffer.email_is_unsubscribed(snap["email"]):
            con = _connect()
            con.execute("UPDATE cart_snapshots SET status = 'unsubscribed'"
                        " WHERE id = ?", (snap["id"],))
            con.commit()
            con.close()
            continue
        if _send_recovery_email(snap):
            con = _connect()
            con.execute(
                "UPDATE cart_snapshots SET status = 'recovered_sent',"
                " recovery_sent_at = ? WHERE id = ?",
                (_now_iso(), snap["id"]))
            con.commit()
            con.close()
            sent += 1
    return sent


def maybe_send_recoveries():
    """Opportunistic scheduler: at most one recovery pass per
    RECOVERY_THROTTLE_SEC per process. Never raises into a request."""
    global _last_recovery_run
    now = time.monotonic()
    if now - _last_recovery_run < RECOVERY_THROTTLE_SEC:
        return
    _last_recovery_run = now
    try:
        send_due_recoveries()
    except Exception:  # noqa: BLE001 - recovery must never break shopping
        pass


# ---------------------------------------------------------------- admin
def _carttrack_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def _items_summary(items_json):
    try:
        items = json.loads(items_json or "[]")
    except ValueError:
        return ""
    parts = [f"{it.get('qty', 1)} × {it.get('name', '')}" for it in items[:4]]
    s = ", ".join(parts)
    if len(items) > 4:
        s += f" +{len(items) - 4} more"
    return s


@bp.route("/admin/abandoned-carts")
@_carttrack_admin_required
def admin_abandoned_carts():
    ensure_cart_schema()
    con = _connect()
    rows = con.execute(
        "SELECT * FROM cart_snapshots ORDER BY updated_at DESC LIMIT 100"
    ).fetchall()
    con.close()
    snaps = []
    for r in rows:
        d = dict(r)
        d["items_summary"] = _items_summary(d["items_json"])
        d["subtotal_str"] = _money(d["subtotal_cents"])
        snaps.append(d)
    return render_template(
        "abandoned_admin.html", snapshots=snaps,
        recovery_enabled=db.get_setting(RECOVERY_SETTING, "0") == "1")


@bp.route("/admin/abandoned-carts/toggle", methods=["POST"])
@_carttrack_admin_required
def admin_abandoned_carts_toggle():
    on = db.get_setting(RECOVERY_SETTING, "0") == "1"
    db.set_setting(RECOVERY_SETTING, "0" if on else "1")
    return redirect(url_for("carttrack.admin_abandoned_carts"))


def register_carttrack_routes(app, hooks=None):
    if bp.name in app.blueprints:
        return
    _hooks.update(hooks or {})
    ensure_cart_schema()
    app.register_blueprint(bp)
