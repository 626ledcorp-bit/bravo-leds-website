"""10%-off email popup for the BRAVO LEDS storefront.

Public flow (all JS-driven, loaded from static/js/emailoffer.js):
  GET  /api/offer/active                 -> {active: bool} (admin toggle)
  POST /api/offer/claim   {email, source} -> issues a real coupon code and
                                             emails it to the visitor

The prize is a REAL promo code: one unique single-use row in the existing
promo_codes table (kind percent, 10% off, max_uses=1, 30-day expiry), so it
flows through the normal cart/checkout/Stripe discount path with no
checkout changes — same trick spinpromo.py uses for wheel prizes.

"First purchase" enforcement is deliberately simple: one code per email
address, and the code itself is single-use. The code is NOT hard-bound to
the email at checkout (that adds friction and support tickets when someone
checks out with a different address); one-code-per-email + single-use keeps
abuse negligible at this scale.

Admin (under /admin, same auth as the rest of the site):
  /admin/email-offer            signup list + counts
  /admin/email-offer/toggle     POST flips the email_offer_enabled setting

Unsubscribe: every email carries /offer/unsubscribe?e=<email>&t=<token>,
token = HMAC-SHA256(secret_key, email) truncated to 32 hex chars. The same
token scheme is shared with carttrack.py's recovery emails, and opting out
in either place is honored by both senders.
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, current_app, jsonify, redirect,
                   render_template, request, session, url_for)

import db
import emails
from db import _connect  # reuse the store's SQLite connection helper

bp = Blueprint("emailoffer", __name__)

# ---------------------------------------------------------------- constants
PERCENT_OFF = 10
CODE_PREFIX = "WELCOME10"
CODE_DAYS_VALID = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no lookalikes
ENABLED_SETTING = "email_offer_enabled"


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- schema
def ensure_offer_schema():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS email_offer_signups (
            email TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            emailed_at TEXT,
            unsubscribed INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT '',
            vehicle TEXT NOT NULL DEFAULT ''
        )
    """)
    con.commit()
    con.close()


# ---------------------------------------------------------------- helpers
def offer_enabled():
    """Popup on/off, controlled from /admin/email-offer (default on)."""
    return db.get_setting(ENABLED_SETTING, "1") == "1"


def _secret():
    try:
        return current_app.secret_key or ""
    except RuntimeError:  # outside an app context (scripts/tests)
        return os.environ.get("SECRET_KEY", "626leds-dev-secret")


def unsubscribe_token(email):
    """Shared opt-out token (also used by carttrack recovery emails)."""
    return hmac.new(_secret().encode(), (email or "").strip().lower().encode(),
                    hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(email, path="/offer/unsubscribe"):
    base = db.get_setting("site_base_url",
                          "https://bravo-leds-store.onrender.com").rstrip("/")
    return (f"{base}{path}?e={(email or '').strip().lower()}"
            f"&t={unsubscribe_token(email)}")


def get_signup(email):
    ensure_offer_schema()
    con = _connect()
    r = con.execute("SELECT * FROM email_offer_signups WHERE email = ?",
                    ((email or "").strip().lower(),)).fetchone()
    con.close()
    return dict(r) if r else None


def email_is_unsubscribed(email):
    """True when this email opted out of the popup-offer list. Cart
    recovery checks this too — one opt-out is honored everywhere."""
    s = get_signup(email)
    return bool(s and s["unsubscribed"])


def mark_unsubscribed(email):
    ensure_offer_schema()
    con = _connect()
    con.execute("UPDATE email_offer_signups SET unsubscribed = 1"
                " WHERE email = ?", ((email or "").strip().lower(),))
    con.commit()
    con.close()


def _unique_code(prefix):
    """Mint a collision-free PREFIX-XXXXXX code (spinpromo's approach)."""
    from landing import ensure_landing_schema
    ensure_landing_schema()
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
    raise RuntimeError("could not mint a unique offer code")


def _issue_code(email):
    """Create the single-use 10% promo_codes row for a new signup.

    Returns (code, expires_at_iso)."""
    from landing import normalize_code  # local import: landing is standalone
    code = normalize_code(_unique_code(CODE_PREFIX))
    expires_at = (_now() + timedelta(days=CODE_DAYS_VALID)
                  ).isoformat(timespec="seconds")
    con = _connect()
    con.execute(
        """INSERT INTO promo_codes
           (code, kind, percent, amount_cents, expires_at,
            max_uses, used_count, active, created_at)
           VALUES (?, 'percent', ?, NULL, ?, 1, 0, 1, ?)""",
        (code, PERCENT_OFF, expires_at, _now_iso()))
    con.commit()
    con.close()
    return code, expires_at


def _session_vehicle_label():
    v = session.get("vehicle") or {}
    if isinstance(v, dict):
        return " ".join(str(v.get(k, "")).strip()
                        for k in ("year", "make", "model")
                        if str(v.get(k, "")).strip())[:60]
    return str(v)[:60]


def send_offer_email(email, code, expires_at):
    """Email the code. Returns True only when SMTP actually sent it —
    send_email_channel skips silently when SMTP is unconfigured, and the
    caller still shows the code on screen as a fallback."""
    exp_date = (expires_at or "")[:10]
    body = (
        "Thanks for joining the BRAVO LEDS list!\n"
        "\n"
        "Here is your 10% off code for your first purchase:\n"
        "\n"
        f"    {code}\n"
        "\n"
        "- 10% off, one use only, just for you\n"
        f"- Expires {exp_date} (30 days from today)\n"
        "- Enter the code in the promo code box in your cart before "
        "checkout\n"
        "\n"
        "Start here: "
        + db.get_setting("site_base_url",
                         "https://bravo-leds-store.onrender.com").rstrip("/")
        + "/shop\n"
        "\n"
        "BRAVO LEDS by 626 LEDs — Rosemead, CA\n"
        "\n"
        "Don't want emails from us? Unsubscribe: "
        + unsubscribe_url(email) + "\n"
    )
    return emails.send_email_channel(
        email, "Your 10% off code for BRAVO LEDS", body)


# ---------------------------------------------------------------- public API
@bp.route("/api/offer/active")
def api_offer_active():
    return jsonify({"active": offer_enabled()})


@bp.route("/api/offer/claim", methods=["POST"])
def api_offer_claim():
    if not offer_enabled():
        return jsonify({"ok": False, "error": "offer unavailable"}), 403
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({"ok": False,
                        "error": "Enter a valid email address."}), 400
    source = str(data.get("source") or "")[:120]
    ensure_offer_schema()
    existing = get_signup(email)
    if existing:
        # One code per email: re-send the SAME code, never mint a second.
        # Re-claiming also counts as re-subscribing after an unsubscribe.
        if existing["unsubscribed"]:
            con = _connect()
            con.execute("UPDATE email_offer_signups SET unsubscribed = 0"
                        " WHERE email = ?", (email,))
            con.commit()
            con.close()
        code = existing["code"]
        promo = None
        try:
            import landing
            promo = landing.get_promo(code)
        except Exception:  # noqa: BLE001 - expiry is cosmetic in the reply
            promo = None
        expires_at = promo["expires_at"] if promo else None
        sent = send_offer_email(email, code, expires_at)
        if sent:
            con = _connect()
            con.execute("UPDATE email_offer_signups SET emailed_at = ?"
                        " WHERE email = ?", (_now_iso(), email))
            con.commit()
            con.close()
        return jsonify({"ok": True, "code": code, "already": True,
                        "emailed": sent, "expires_at": expires_at})
    code, expires_at = _issue_code(email)
    con = _connect()
    con.execute(
        "INSERT INTO email_offer_signups"
        " (email, code, created_at, emailed_at, unsubscribed, source,"
        "  vehicle) VALUES (?, ?, ?, NULL, 0, ?, ?)",
        (email, code, _now_iso(), source, _session_vehicle_label()))
    con.commit()
    con.close()
    sent = send_offer_email(email, code, expires_at)
    if sent:
        con = _connect()
        con.execute("UPDATE email_offer_signups SET emailed_at = ?"
                    " WHERE email = ?", (_now_iso(), email))
        con.commit()
        con.close()
    return jsonify({"ok": True, "code": code, "already": False,
                    "emailed": sent, "expires_at": expires_at})


@bp.route("/offer/unsubscribe")
def offer_unsubscribe():
    email = (request.args.get("e") or "").strip().lower()
    token = (request.args.get("t") or "").strip()
    ok = bool(email and token
              and hmac.compare_digest(token, unsubscribe_token(email)))
    if ok:
        mark_unsubscribed(email)
    return render_template("offer_unsubscribed.html", ok=ok), (200 if ok
                                                               else 400)


# ---------------------------------------------------------------- admin
def _offer_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


@bp.route("/admin/email-offer")
@_offer_admin_required
def admin_email_offer():
    ensure_offer_schema()
    con = _connect()
    rows = con.execute(
        "SELECT s.email, s.code, s.created_at, s.emailed_at,"
        "       s.unsubscribed, s.source, s.vehicle,"
        "       p.used_count AS code_used, p.expires_at AS code_expires"
        " FROM email_offer_signups s"
        " LEFT JOIN promo_codes p ON p.code = s.code"
        " ORDER BY s.created_at DESC").fetchall()
    con.close()
    signups = [dict(r) for r in rows]
    counts = {
        "total": len(signups),
        "emailed": sum(1 for s in signups if s["emailed_at"]),
        "redeemed": sum(1 for s in signups if s["code_used"]),
        "unsubscribed": sum(1 for s in signups if s["unsubscribed"]),
    }
    return render_template("offer_admin.html", signups=signups,
                           counts=counts, enabled=offer_enabled())


@bp.route("/admin/email-offer/toggle", methods=["POST"])
@_offer_admin_required
def admin_email_offer_toggle():
    db.set_setting(ENABLED_SETTING, "0" if offer_enabled() else "1")
    return redirect(url_for("emailoffer.admin_email_offer"))


def register_emailoffer_routes(app):
    if bp.name in app.blueprints:
        return
    ensure_offer_schema()
    app.register_blueprint(bp)
