"""Track 3 — ad landing pages + promo codes for the Bravo LEDs storefront.

Everything in this module lives in a Flask Blueprint, wired into app.py
with one idempotent call:

    from landing import register_landing_routes
    register_landing_routes(app)

Schema owned here (all idempotent — safe to run on every startup):
  - promo_codes(code PK, kind percent|amount, percent, amount_cents,
                expires_at NULL, max_uses NULL, used_count, active,
                created_at)
  - landing_pages(slug PK, product_id, headline, subhead, bullets JSON,
                  video_url, testimonial_text, countdown_mode fixed|evergreen,
                  countdown_end, countdown_minutes, default_code,
                  published, created_at, updated_at)
  - orders.promo_code / orders.discount_cents (PRAGMA-guarded ALTERs)

Copy rules honored throughout (same as the rest of the store):
  - User-facing copy must never contain the banned word "head" + "light"
    (kept split here so repo-wide greps stay clean) — say "low beam" /
    "high beam" instead.
  - "off-road" appears only in the standard disclaimer — never in names/nav.

Honest countdowns (this is a hard rule, not marketing fluff):
  - "fixed" counts down to a real end datetime. Saving a past end is
    rejected at the admin form.
  - "evergreen" (N minutes, 5-120) starts when the VISITOR first loads
    the page and is tracked in their own browser (localStorage). It never
    resets for the same visitor — no fake urgency loops. The admin form
    says this outright, and the README documents it.
"""

import json
import re
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse, parse_qs

from flask import (Blueprint, abort, redirect, render_template, request,
                   session, url_for)

import db
from content import approved_reviews
from db import _connect  # reuse the store's SQLite connection helper

bp = Blueprint("landing", __name__)


# ---------------------------------------------------------------- constants
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,58}[a-z0-9])?$")
# 3-32 chars, starts/ends with a letter or digit.
CODE_RE = re.compile(r"^(?=.{3,32}$)[A-Z0-9][A-Z0-9_-]*[A-Z0-9]$")

COUNTDOWN_MODES = ("fixed", "evergreen")
EVERGREEN_MIN, EVERGREEN_MAX = 5, 120          # minutes per visitor
MAX_AMOUNT_CENTS = 500_000                     # $5,000 max amount-off code
MAX_BULLETS = 8

DISCLAIMER_PRODUCT_PAGE = (
    "\u26a0\ufe0f For off-road and fog light use only. Not DOT/SAE approved "
    "for on-road use. Check your local laws. "
    '<a href="/dot-compliance">Learn more \u2192</a>'
)


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat(timespec="seconds")


def money(cents):
    return f"${cents / 100:,.2f}"


def _landing_admin_required(view):
    """Same auth contract as the rest of the site: the admin login sets
    session['admin_authed']; unauthenticated visitors bounce to login."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- schema
def ensure_landing_schema():
    """Create Track 3 tables / columns idempotently."""
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            kind TEXT NOT NULL,          -- 'percent' | 'amount'
            percent INTEGER,             -- 1..100 when kind='percent'
            amount_cents INTEGER,        -- >0 cents when kind='amount'
            expires_at TEXT,             -- ISO UTC, NULL = never expires
            max_uses INTEGER,            -- NULL = unlimited
            used_count INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS landing_pages (
            slug TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            headline TEXT NOT NULL,
            subhead TEXT NOT NULL DEFAULT '',
            bullets TEXT NOT NULL DEFAULT '[]',   -- JSON list
            video_url TEXT NOT NULL DEFAULT '',
            testimonial_text TEXT NOT NULL DEFAULT '',
            countdown_mode TEXT NOT NULL,          -- 'fixed' | 'evergreen'
            countdown_end TEXT,                    -- ISO UTC when fixed
            countdown_minutes INTEGER,             -- 5..120 when evergreen
            default_code TEXT NOT NULL DEFAULT '', -- promo code or ''
            published INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # Promo snapshot columns on orders (older DBs won't have them).
    cols = [r[1] for r in
            con.execute("PRAGMA table_info(orders)").fetchall()]
    if "promo_code" not in cols:
        con.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")
    if "discount_cents" not in cols:
        con.execute("ALTER TABLE orders ADD COLUMN discount_cents "
                    "INTEGER NOT NULL DEFAULT 0")
    con.commit()
    con.close()
    seed_landing_defaults()


# ---------------------------------------------------------------- seed data
# Code-defined example content, inserted ONLY if missing. A re-seed never
# touches rows the owner already edited or deleted (INSERT ... DO NOTHING).
EXAMPLE_PROMO = {
    "code": "PREMIUM10",
    "kind": "percent",
    "percent": 10,
    "amount_cents": None,
    "expires_at": None,
    "max_uses": None,
    "active": 1,
}

EXAMPLE_LANDING = {
    # "premium-csp-led-bulbs" is the Premium LED Bulb product — the
    # SilverHolder-equivalent model the store is known for.
    "slug": "premium-led",
    "product_id": "premium-csp-led-bulbs",
    "headline": "See More of the Road at Night",
    "subhead": ("Premium CSP Series LED Bulbs \u2014 60W. Our most popular "
                "upgrade: a razor-sharp beam pattern, plug-and-play install, "
                "and a 1-year warranty."),
    "bullets": [
        "60W / 12,000 lumens per pair \u2014 CSP chips for a razor-sharp "
        "beam cutoff with no glare",
        "Plug-and-play install \u2014 no cutting, no splicing, about 20 "
        "minutes per side",
        "Aviation aluminum body with a turbo cooling fan for long life",
        "1-year warranty and 30-day returns, backed by our Rosemead store",
    ],
    "video_url": ("PLACEHOLDER \u2014 replace with your YouTube video URL, "
                  "e.g. https://www.youtube.com/watch?v=YOUR_VIDEO_ID"),
    "testimonial_text": ("PLACEHOLDER \u2014 paste a real customer "
                         "testimonial here. Never invent reviews; leave "
                         "this as a placeholder until you have a real one."),
    "countdown_mode": "evergreen",
    "countdown_end": None,
    "countdown_minutes": 30,
    "default_code": "PREMIUM10",
    "published": 1,
}


def seed_landing_defaults():
    """Insert the example promo code + landing page only if missing."""
    con = _connect()
    con.execute("""
        INSERT INTO promo_codes
          (code, kind, percent, amount_cents, expires_at, max_uses,
           used_count, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(code) DO NOTHING
    """, (EXAMPLE_PROMO["code"], EXAMPLE_PROMO["kind"],
          EXAMPLE_PROMO["percent"], EXAMPLE_PROMO["amount_cents"],
          EXAMPLE_PROMO["expires_at"], EXAMPLE_PROMO["max_uses"],
          EXAMPLE_PROMO["active"], _now_iso()))
    now = _now_iso()
    con.execute("""
        INSERT INTO landing_pages
          (slug, product_id, headline, subhead, bullets, video_url,
           testimonial_text, countdown_mode, countdown_end,
           countdown_minutes, default_code, published, created_at,
           updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO NOTHING
    """, (EXAMPLE_LANDING["slug"], EXAMPLE_LANDING["product_id"],
          EXAMPLE_LANDING["headline"], EXAMPLE_LANDING["subhead"],
          json.dumps(EXAMPLE_LANDING["bullets"]),
          EXAMPLE_LANDING["video_url"],
          EXAMPLE_LANDING["testimonial_text"],
          EXAMPLE_LANDING["countdown_mode"], EXAMPLE_LANDING["countdown_end"],
          EXAMPLE_LANDING["countdown_minutes"],
          EXAMPLE_LANDING["default_code"], EXAMPLE_LANDING["published"],
          now, now))
    con.commit()
    con.close()


# ---------------------------------------------------------------- promo codes
def normalize_code(raw):
    return re.sub(r"\s+", "", (raw or "")).upper()


def _row_to_promo(r):
    d = dict(r)
    d["active"] = bool(d["active"])
    return d


def get_promo(code):
    """Look up a promo code by its (normalized) code. None if missing."""
    code = normalize_code(code)
    if not code:
        return None
    ensure_landing_schema()
    con = _connect()
    r = con.execute("SELECT * FROM promo_codes WHERE code = ?",
                    (code,)).fetchone()
    con.close()
    return _row_to_promo(r) if r else None


def list_promos():
    ensure_landing_schema()
    con = _connect()
    rows = con.execute(
        "SELECT * FROM promo_codes ORDER BY active DESC, code").fetchall()
    con.close()
    return [_row_to_promo(r) for r in rows]


def promo_is_usable(promo, now=None):
    """A code is usable only when it exists, is active, is not expired,
    and has not hit its usage cap."""
    if not promo or not promo["active"]:
        return False
    now = now or _now()
    if promo["expires_at"]:
        try:
            exp = datetime.fromisoformat(promo["expires_at"])
        except ValueError:
            return False
        if exp <= now:
            return False
    if promo["max_uses"] is not None and promo["used_count"] >= promo["max_uses"]:
        return False
    return True


def discount_cents_for(promo, subtotal_cents):
    """Discount in cents for a validated-usable promo. Never exceeds the
    subtotal (an amount-off code can't make the total negative)."""
    if not promo or subtotal_cents <= 0:
        return 0
    if promo["kind"] == "percent":
        pct = max(0, min(100, int(promo["percent"] or 0)))
        return min(subtotal_cents, (subtotal_cents * pct) // 100)
    return min(subtotal_cents, max(0, int(promo["amount_cents"] or 0)))


def promo_describe(promo):
    if promo["kind"] == "percent":
        return f"{promo['percent']}% off"
    return f"{money(promo['amount_cents'])} off"


def active_cart_promo(subtotal_cents):
    """The session's promo code, re-validated on every call. Stale codes
    (expired, deactivated, or used up since they were applied) are dropped
    from the session. Returns (promo_dict_or_None, discount_cents)."""
    code = session.get("promo_code")
    if not code:
        return None, 0
    promo = get_promo(code)
    if not promo or not promo_is_usable(promo):
        session.pop("promo_code", None)
        return None, 0
    return promo, discount_cents_for(promo, subtotal_cents)


def apply_promo_code(raw_code):
    """Validate and store a promo code in the session. Returns
    (ok, message)."""
    code = normalize_code(raw_code)
    if not code:
        return False, "Enter a promo code."
    promo = get_promo(code)
    if not promo or not promo_is_usable(promo):
        return False, (f"Code {code} isn\u2019t valid \u2014 it may be "
                       "expired, deactivated, or already used up.")
    session["promo_code"] = promo["code"]
    return True, f"Code {promo['code']} applied \u2014 {promo_describe(promo)}."


def clear_promo():
    session.pop("promo_code", None)


def increment_promo_usage(order_or_id):
    """Bump used_count for the promo recorded on a paid order. Call only
    when the order actually transitioned to paid (webhook / admin action),
    so duplicate deliveries never double-count. Returns True on bump."""
    ensure_landing_schema()
    order = (db.get_order(order_or_id)
             if isinstance(order_or_id, int) else order_or_id)
    if not order or order.get("status") != "paid" or not order.get("promo_code"):
        return False
    con = _connect()
    cur = con.execute(
        "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
        (normalize_code(order["promo_code"]),))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    return changed


# ---- promo validation + persistence -------------------------------------
def validate_promo_form(form, is_new):
    """Validate admin promo-code input. Returns (cleaned, errors)."""
    errors, cleaned = [], {}
    code = normalize_code(form.get("code", ""))
    if is_new:
        if not CODE_RE.match(code or ""):
            errors.append("Code must be 3\u201332 characters: letters, "
                          "numbers, dashes, underscores.")
        elif get_promo(code):
            errors.append(f"Code {code} already exists.")
        cleaned["code"] = code
    kind = (form.get("kind") or "").strip()
    if kind not in ("percent", "amount"):
        errors.append("Kind must be 'percent' or 'amount'.")
    cleaned["kind"] = kind
    percent, amount_cents = None, None
    if kind == "percent":
        try:
            percent = int(form.get("percent", ""))
        except (TypeError, ValueError):
            percent = 0
        if not 1 <= percent <= 100:
            errors.append("Percent must be between 1 and 100.")
    elif kind == "amount":
        try:
            amount_cents = int(round(float(form.get("amount_dollars", "")) * 100))
        except (TypeError, ValueError):
            amount_cents = 0
        if not 1 <= amount_cents <= MAX_AMOUNT_CENTS:
            errors.append("Amount must be between $0.01 and "
                          f"{money(MAX_AMOUNT_CENTS)}.")
    cleaned["percent"], cleaned["amount_cents"] = percent, amount_cents
    expires_raw = (form.get("expires_at") or "").strip()
    expires_at = None
    if expires_raw:
        try:
            exp = datetime.fromisoformat(expires_raw)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            expires_at = exp.isoformat(timespec="seconds")
            if exp <= _now():
                errors.append("Expiry must be in the future (UTC).")
        except ValueError:
            errors.append("Expiry must look like 2026-12-31T23:59 (UTC).")
    cleaned["expires_at"] = expires_at
    max_uses_raw = (form.get("max_uses") or "").strip()
    max_uses = None
    if max_uses_raw:
        try:
            max_uses = int(max_uses_raw)
        except (TypeError, ValueError):
            max_uses = 0
        if max_uses < 1:
            errors.append("Max uses must be at least 1 (or blank for "
                          "unlimited).")
    cleaned["max_uses"] = max_uses
    cleaned["active"] = 1 if form.get("active") else 0
    return cleaned, errors


def save_promo(cleaned, is_new):
    ensure_landing_schema()
    con = _connect()
    if is_new:
        con.execute("""
            INSERT INTO promo_codes
              (code, kind, percent, amount_cents, expires_at, max_uses,
               used_count, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (cleaned["code"], cleaned["kind"], cleaned["percent"],
              cleaned["amount_cents"], cleaned["expires_at"],
              cleaned["max_uses"], cleaned["active"], _now_iso()))
    else:
        con.execute("""
            UPDATE promo_codes
               SET kind = ?, percent = ?, amount_cents = ?,
                   expires_at = ?, max_uses = ?, active = ?
             WHERE code = ?
        """, (cleaned["kind"], cleaned["percent"], cleaned["amount_cents"],
              cleaned["expires_at"], cleaned["max_uses"], cleaned["active"],
              cleaned["code"]))
    con.commit()
    con.close()


def delete_promo(code):
    ensure_landing_schema()
    code = normalize_code(code)
    con = _connect()
    # Clear dangling default-code references on landing pages first.
    con.execute("UPDATE landing_pages SET default_code = '', updated_at = ?"
                " WHERE default_code = ?", (_now_iso(), code))
    cur = con.execute("DELETE FROM promo_codes WHERE code = ?", (code,))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    return changed


# ---------------------------------------------------------------- landing pages
def _row_to_page(r):
    d = dict(r)
    d["bullets"] = json.loads(d["bullets"] or "[]")
    d["published"] = bool(d["published"])
    return d


def get_landing(slug):
    ensure_landing_schema()
    con = _connect()
    r = con.execute("SELECT * FROM landing_pages WHERE slug = ?",
                    ((slug or "").strip().lower(),)).fetchone()
    con.close()
    return _row_to_page(r) if r else None


def list_landings():
    ensure_landing_schema()
    con = _connect()
    rows = con.execute(
        "SELECT * FROM landing_pages ORDER BY updated_at DESC").fetchall()
    con.close()
    return [_row_to_page(r) for r in rows]


def validate_landing_form(form, is_new):
    """Validate admin landing-page input. Returns (cleaned, errors)."""
    errors, cleaned = [], {}
    slug = (form.get("slug") or "").strip().lower()
    if is_new:
        if not SLUG_RE.match(slug or ""):
            errors.append("Slug must be lowercase letters, numbers, and "
                          "dashes (e.g. premium-led).")
        elif get_landing(slug):
            errors.append(f"A page with slug '{slug}' already exists.")
        cleaned["slug"] = slug
    product_id = (form.get("product_id") or "").strip()
    if not db.get_product(product_id):
        errors.append("Pick a real product for this page.")
    cleaned["product_id"] = product_id
    headline = (form.get("headline") or "").strip()
    if not headline:
        errors.append("Headline is required.")
    cleaned["headline"] = headline[:200]
    cleaned["subhead"] = (form.get("subhead") or "").strip()[:500]
    bullets = [b.strip()[:140] for b in
               (form.get("bullets") or "").splitlines()]
    bullets = [b for b in bullets if b]
    if len(bullets) > MAX_BULLETS:
        errors.append(f"Keep it to {MAX_BULLETS} bullets or fewer.")
        bullets = bullets[:MAX_BULLETS]
    cleaned["bullets"] = bullets
    cleaned["video_url"] = (form.get("video_url") or "").strip()[:500]
    cleaned["testimonial_text"] = (form.get("testimonial_text") or "").strip()[:1000]
    mode = (form.get("countdown_mode") or "").strip()
    if mode not in COUNTDOWN_MODES:
        errors.append("Countdown mode must be 'fixed' or 'evergreen'.")
    cleaned["countdown_mode"] = mode
    countdown_end, countdown_minutes = None, None
    if mode == "fixed":
        raw = (form.get("countdown_end") or "").strip()
        if not raw:
            errors.append("Fixed mode needs an end date/time.")
        else:
            try:
                end = datetime.fromisoformat(raw)
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                countdown_end = end.isoformat(timespec="seconds")
                if end <= _now():
                    errors.append("The countdown end must be in the future "
                                  "(UTC) \u2014 past deadlines are not saved.")
            except ValueError:
                errors.append("End must look like 2026-12-31T23:59 (UTC).")
    elif mode == "evergreen":
        try:
            countdown_minutes = int(form.get("countdown_minutes", ""))
        except (TypeError, ValueError):
            countdown_minutes = 0
        if not EVERGREEN_MIN <= countdown_minutes <= EVERGREEN_MAX:
            errors.append(f"Evergreen minutes must be between "
                          f"{EVERGREEN_MIN} and {EVERGREEN_MAX}.")
    cleaned["countdown_end"] = countdown_end
    cleaned["countdown_minutes"] = countdown_minutes
    default_code = normalize_code(form.get("default_code", ""))
    if default_code and not get_promo(default_code):
        errors.append(f"Default code '{default_code}' doesn't exist \u2014 "
                      "create it under Promo Codes first (or leave blank).")
    cleaned["default_code"] = default_code
    cleaned["published"] = 1 if form.get("published") else 0
    return cleaned, errors


def save_landing(cleaned, is_new):
    ensure_landing_schema()
    now = _now_iso()
    con = _connect()
    if is_new:
        con.execute("""
            INSERT INTO landing_pages
              (slug, product_id, headline, subhead, bullets, video_url,
               testimonial_text, countdown_mode, countdown_end,
               countdown_minutes, default_code, published, created_at,
               updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cleaned["slug"], cleaned["product_id"], cleaned["headline"],
              cleaned["subhead"], json.dumps(cleaned["bullets"]),
              cleaned["video_url"], cleaned["testimonial_text"],
              cleaned["countdown_mode"], cleaned["countdown_end"],
              cleaned["countdown_minutes"], cleaned["default_code"],
              cleaned["published"], now, now))
    else:
        con.execute("""
            UPDATE landing_pages
               SET product_id = ?, headline = ?, subhead = ?, bullets = ?,
                   video_url = ?, testimonial_text = ?, countdown_mode = ?,
                   countdown_end = ?, countdown_minutes = ?,
                   default_code = ?, published = ?, updated_at = ?
             WHERE slug = ?
        """, (cleaned["product_id"], cleaned["headline"], cleaned["subhead"],
              json.dumps(cleaned["bullets"]), cleaned["video_url"],
              cleaned["testimonial_text"], cleaned["countdown_mode"],
              cleaned["countdown_end"], cleaned["countdown_minutes"],
              cleaned["default_code"], cleaned["published"], now,
              cleaned["slug"]))
    con.commit()
    con.close()


def delete_landing(slug):
    ensure_landing_schema()
    con = _connect()
    cur = con.execute("DELETE FROM landing_pages WHERE slug = ?",
                      ((slug or "").strip().lower(),))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    return changed


# ---------------------------------------------------------------- helpers
def youtube_embed_url(url):
    """Normalize a YouTube URL to its /embed/ form. Returns None for
    empty, placeholder, or non-YouTube URLs."""
    url = (url or "").strip()
    if not url or url.upper().startswith("PLACEHOLDER"):
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    vid = None
    if host in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            vid = (parse_qs(parsed.query).get("v") or [None])[0]
        elif parsed.path.startswith("/embed/"):
            vid = parsed.path.split("/embed/")[1].split("/")[0]
        elif parsed.path.startswith("/shorts/"):
            vid = parsed.path.split("/shorts/")[1].split("/")[0]
    elif host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
    if vid and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
        return f"https://www.youtube.com/embed/{vid}"
    return None


def unit_price_cents(product):
    """Price the customer actually pays: sale price when set, else regular."""
    return product.get("sale_price_cents") or product["price_cents"]


def product_review_ctx(product_id):
    """Approved reviews for a product, wired to the real review system."""
    revs = approved_reviews(product_id)
    avg = round(sum(r["rating"] for r in revs) / len(revs), 1) if revs else 0
    return {"reviews": revs, "count": len(revs), "avg": avg}


def _landing_form_vals(page, form):
    """Normalize admin landing-page form values for template rendering.

    GET edit -> page row; POST (re-render after errors) -> submitted form.
    """
    if form is not None:
        return {
            "slug": form.get("slug", ""),
            "product_id": form.get("product_id", ""),
            "headline": form.get("headline", ""),
            "subhead": form.get("subhead", ""),
            "bullets": form.get("bullets", ""),
            "video_url": form.get("video_url", ""),
            "testimonial_text": form.get("testimonial_text", ""),
            "countdown_mode": form.get("countdown_mode", "evergreen"),
            "countdown_end": form.get("countdown_end", ""),
            "countdown_minutes": form.get("countdown_minutes", "30"),
            "default_code": form.get("default_code", ""),
            "published": bool(form.get("published")),
        }
    if page:
        return {
            "slug": page["slug"],
            "product_id": page["product_id"],
            "headline": page["headline"],
            "subhead": page["subhead"],
            "bullets": "\n".join(page["bullets"]),
            "video_url": page["video_url"],
            "testimonial_text": page["testimonial_text"],
            "countdown_mode": page["countdown_mode"],
            "countdown_end": (page["countdown_end"] or "")[:16],
            "countdown_minutes": page["countdown_minutes"] or 30,
            "default_code": page["default_code"],
            "published": page["published"],
        }
    return {
        "slug": "", "product_id": "", "headline": "", "subhead": "",
        "bullets": "", "video_url": "", "testimonial_text": "",
        "countdown_mode": "evergreen", "countdown_end": "",
        "countdown_minutes": 30, "default_code": "", "published": True,
    }


def _promo_form_vals(promo, form):
    """Same normalization for the admin promo-code form."""
    if form is not None:
        kind = form.get("kind", "percent")
        return {
            "code": form.get("code", ""),
            "kind": kind,
            "percent": form.get("percent", "10" if kind == "percent" else ""),
            "amount_dollars": form.get("amount_dollars", ""),
            "expires_at": form.get("expires_at", ""),
            "max_uses": form.get("max_uses", ""),
            "active": bool(form.get("active")),
        }
    if promo:
        return {
            "code": promo["code"],
            "kind": promo["kind"],
            "percent": promo["percent"] or "",
            "amount_dollars": (f"{promo['amount_cents'] / 100:.2f}"
                               if promo["amount_cents"] else ""),
            "expires_at": (promo["expires_at"] or "")[:16],
            "max_uses": promo["max_uses"] or "",
            "active": promo["active"],
        }
    return {"code": "", "kind": "percent", "percent": "10",
            "amount_dollars": "", "expires_at": "", "max_uses": "",
            "active": True}
# ---------------------------------------------------------------- public routes
@bp.route("/go/<slug>")
def landing_page(slug):
    page = get_landing(slug)
    if not page or not page["published"]:
        abort(404)
    product = db.get_product(page["product_id"])
    if not product:
        abort(404)

    promo_notice = None
    override = (request.args.get("code") or "").strip()
    if override:
        ok, msg = apply_promo_code(override)
        promo_notice = ("promo-ok", msg) if ok else ("promo-bad", msg)
    elif page["default_code"]:
        promo = get_promo(page["default_code"])
        if promo and promo_is_usable(promo):
            session["promo_code"] = promo["code"]

    unit = (product["variations"][0]["effective_price_cents"]
            if product.get("variations")
            else unit_price_cents(product))
    promo, discount_cents = active_cart_promo(unit)
    reviews = product_review_ctx(product["id"])
    # Public-safe copy: internal fields (cost/SKU/supplier/notes/...) never
    # reach the landing template.
    product = db.public_product(product)
    ttext = page["testimonial_text"] or ""
    return render_template(
        "go_landing.html",
        page=page,
        product=product,
        unit_price_cents=unit,
        promo=promo,
        promo_discount_cents=discount_cents,
        promo_notice=promo_notice,
        promo_describe=promo_describe(promo) if promo else None,
        reviews=reviews,
        embed_url=youtube_embed_url(page["video_url"]),
        video_is_placeholder=(page["video_url"] or "").upper().startswith(
            "PLACEHOLDER") or not youtube_embed_url(page["video_url"]),
        testimonial_text=ttext,
        testimonial_is_placeholder=ttext.upper().startswith("PLACEHOLDER"),
        disclaimer_html=DISCLAIMER_PRODUCT_PAGE,
        countdown={
            "mode": page["countdown_mode"],
            "end": page["countdown_end"],
            "minutes": page["countdown_minutes"],
            "slug": page["slug"],
        },
    )


@bp.route("/cart/promo", methods=["POST"])
def cart_promo():
    """Apply or remove a promo code on the cart (session-stored)."""
    action = request.form.get("action", "apply")
    if action == "remove":
        clear_promo()
        status = "removed"
    else:
        ok, _msg = apply_promo_code(request.form.get("code", ""))
        status = "applied" if ok else "invalid"
    return redirect(url_for("cart_view", promo=status))


# ---------------------------------------------------------------- admin routes
@bp.route("/admin/landing")
@_landing_admin_required
def admin_landing_list():
    pages = list_landings()
    products = {p["id"]: p for p in db.list_products()}
    return render_template("go_admin_list.html", pages=pages,
                           products=products)


@bp.route("/admin/landing/new", methods=["GET", "POST"])
@_landing_admin_required
def admin_landing_new():
    errors, form = [], None
    if request.method == "POST":
        form = request.form
        cleaned, errors = validate_landing_form(form, is_new=True)
        if not errors:
            save_landing(cleaned, is_new=True)
            return redirect(url_for("landing.admin_landing_list"))
    return render_template("go_admin_form.html", page=None,
                           vals=_landing_form_vals(None, form),
                           errors=errors, is_new=True,
                           products=db.list_products(),
                           evergreen_min=EVERGREEN_MIN,
                           evergreen_max=EVERGREEN_MAX,
                           promos=list_promos())


@bp.route("/admin/landing/<slug>/edit", methods=["GET", "POST"])
@_landing_admin_required
def admin_landing_edit(slug):
    page = get_landing(slug)
    if not page:
        abort(404)
    errors, form = [], None
    if request.method == "POST":
        form = request.form
        cleaned, errors = validate_landing_form(form, is_new=False)
        cleaned["slug"] = page["slug"]
        if not errors:
            save_landing(cleaned, is_new=False)
            return redirect(url_for("landing.admin_landing_list"))
    return render_template("go_admin_form.html", page=page,
                           vals=_landing_form_vals(page, form),
                           errors=errors, is_new=False,
                           products=db.list_products(),
                           evergreen_min=EVERGREEN_MIN,
                           evergreen_max=EVERGREEN_MAX,
                           promos=list_promos())


@bp.route("/admin/landing/<slug>/delete", methods=["POST"])
@_landing_admin_required
def admin_landing_delete(slug):
    delete_landing(slug)
    return redirect(url_for("landing.admin_landing_list"))


@bp.route("/admin/promos")
@_landing_admin_required
def admin_promo_list():
    promos = list_promos()
    now = _now()
    for p in promos:
        p["usable"] = promo_is_usable(p, now)
        p["describe"] = promo_describe(p)
    return render_template("go_promo_list.html", promos=promos)


@bp.route("/admin/promos/new", methods=["GET", "POST"])
@_landing_admin_required
def admin_promo_new():
    errors, form = [], None
    if request.method == "POST":
        form = request.form
        cleaned, errors = validate_promo_form(form, is_new=True)
        if not errors:
            save_promo(cleaned, is_new=True)
            return redirect(url_for("landing.admin_promo_list"))
    return render_template("go_promo_form.html", promo=None,
                           vals=_promo_form_vals(None, form),
                           errors=errors, is_new=True)


@bp.route("/admin/promos/<code>/edit", methods=["GET", "POST"])
@_landing_admin_required
def admin_promo_edit(code):
    promo = get_promo(code)
    if not promo:
        abort(404)
    errors, form = [], None
    if request.method == "POST":
        form = request.form
        cleaned, errors = validate_promo_form(request.form, is_new=False)
        cleaned["code"] = promo["code"]
        if not errors:
            save_promo(cleaned, is_new=False)
            return redirect(url_for("landing.admin_promo_list"))
    return render_template("go_promo_form.html", promo=promo,
                           vals=_promo_form_vals(promo, form),
                           errors=errors, is_new=False)


@bp.route("/admin/promos/<code>/delete", methods=["POST"])
@_landing_admin_required
def admin_promo_delete(code):
    delete_promo(code)
    return redirect(url_for("landing.admin_promo_list"))


def register_landing_routes(app):
    """Wire the landing blueprint into the app (called by app.py).

    Idempotent: safe to call twice — the second call is a no-op.
    """
    if bp.name in app.blueprints:
        return
    ensure_landing_schema()
    app.register_blueprint(bp)
