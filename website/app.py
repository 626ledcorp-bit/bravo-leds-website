"""Bravo LEDs — Phase 1 storefront.

Flask + SQLite + server-rendered Jinja templates. Session cart, no payments
yet: /checkout is a "coming soon" page wired for Stripe Checkout in Phase 2.
"""

import os
import json
import re
import hmac
import secrets
import shutil
import time
import uuid
from functools import wraps
from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, Response, session, url_for)
from werkzeug.utils import secure_filename

import pyotp

import db
import emails
import landing
import payments
import shipping as shiputil
import square_sync
import square_import
from catalog import CATEGORIES
from content import register_content_routes
from fitment_loader import fitment_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "626leds-dev-secret")
db.init_db()
register_content_routes(app)
landing.register_landing_routes(app)

FREE_SHIP_THRESHOLD_CENTS = 9900  # free shipping over $99


# ---------------------------------------------------------------- helpers
def money(cents):
    return f"${cents / 100:,.2f}"


def get_cart():
    return session.get("cart", {})


def unit_price_cents(product):
    """Price the customer actually pays: sale price when set, else regular."""
    return product.get("sale_price_cents") or product["price_cents"]


app.jinja_env.globals["primary_image"] = db.primary_image_src
app.jinja_env.globals["variation_price_range"] = db.variation_price_range


def cart_detailed():
    """Cart lines with server-side product/price lookup. Prices always come
    from the DB (variation override, else product sale/regular) — the client
    can never set its own price."""
    lines, subtotal = [], 0
    for key, item in get_cart().items():
        p = db.get_product(item["product_id"])
        if not p:
            continue
        var = db.resolve_cart_variation(p, item)
        if var is None:
            continue
        qty = max(1, min(99, int(item.get("qty", 1))))
        unit = db.variation_sell_price(p, var)
        line_total = unit * qty
        subtotal += line_total
        ov = var["option_values"]
        lines.append({
            "key": key,
            # Public-safe copies: cost/SKU/inventory internals never reach
            # templates, JSON, or emails. Server-side pricing above already
            # used the full internal records.
            "product": db.public_product(p),
            "variation": db.public_variation(var),
            "variation_id": var["id"],
            "variation_label": var["label"],
            "size": ov.get("Size", ""),
            "color_temp": ov.get("Color temp", ""),
            "qty": qty,
            "unit_price_cents": unit,
            "line_total": line_total,
            "line_total_str": money(line_total),
        })
    return lines, subtotal


@app.context_processor
def inject_globals():
    lines, subtotal = cart_detailed()
    promo, discount_cents = landing.active_cart_promo(subtotal)
    return {
        "categories": CATEGORIES,
        "cart_count": sum(l["qty"] for l in lines),
        "cart_subtotal_str": money(subtotal),
        "cart_promo": promo,                       # Track 3: active promo
        "cart_discount_cents": discount_cents,
        "cart_discount_str": money(discount_cents),
        "cart_total_cents": subtotal - discount_cents,
        "cart_total_str": money(subtotal - discount_cents),
        "fitment_source": fitment_db.source_info(),
        "money": money,
    }


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    featured = [db.public_product(p) for p in db.list_products()
                if p.get("badge") in ("Best Seller", "Most Popular",
                                      "Flagship")]
    return render_template("index.html", featured=featured,
                           years=fitment_db.get_years())


@app.route("/shop")
def shop_all():
    products = [db.public_product(p) for p in db.list_products()]
    return render_template("shop.html", products=products,
                           active_cat=None, title="Shop All LEDs")


@app.route("/shop/<category>")
def shop_category(category):
    if category not in CATEGORIES:
        abort(404)
    meta = CATEGORIES[category]
    products = [db.public_product(p)
                for p in db.list_products(category)]
    return render_template("shop.html", products=products,
                           active_cat=category, title=meta["name"],
                           tagline=meta["tagline"])


@app.route("/product/<pid>")
def product_detail(pid):
    p = db.get_product(pid)
    if not p or p.get("status") != "active":
        abort(404)
    related = [db.public_product(r)
               for r in db.list_products(p["category"]) if r["id"] != pid][:4]
    cat = CATEGORIES[p["category"]]
    # Sanitized variation data for the option selector (no cost/SKU/
    # inventory internals ever reach the browser).
    variation_json = json.dumps(
        [db.public_variation(v) for v in p.get("variations", [])])
    # Track 2: the DOT disclaimer renders only on flagged categories.
    return render_template("product.html", p=db.public_product(p),
                           related=related,
                           cat_name=cat["name"],
                           variation_json=variation_json,
                           disclaimer_required=cat.get(
                               "requires_dot_disclaimer", True))


# ---------------------------------------------------------------- fitment
@app.route("/api/fitment/years")
def api_years():
    return jsonify(fitment_db.get_years())


@app.route("/api/fitment/makes")
def api_makes():
    return jsonify(fitment_db.get_makes(request.args.get("year", "")))


@app.route("/api/fitment/models")
def api_models():
    return jsonify(fitment_db.get_models(request.args.get("year", ""),
                                         request.args.get("make", "")))


@app.route("/api/fitment/trims")
def api_trims():
    return jsonify(fitment_db.get_trims(request.args.get("year", ""),
                                        request.args.get("make", ""),
                                        request.args.get("model", "")))


@app.route("/fitment")
def fitment_results():
    year = request.args.get("year", "")
    make = request.args.get("make", "")
    model = request.args.get("model", "")
    trim = request.args.get("trim") or None
    if not (year and make and model):
        return redirect(url_for("home"))
    rows = fitment_db.get_fitment(year, make, model, trim)
    if not rows:
        abort(404)
    # attach matching products per position (public-safe copies only)
    enriched = []
    for r in rows:
        enriched.append({
            **r,
            "products": [db.public_product(p)
                         for p in db.products_matching_size(r["bulb_size"],
                                                            r["categories"])],
        })
    vehicle_label = f"{year} {make} {model}" + (f" {trim}" if trim else "")
    return render_template("fitment.html", vehicle_label=vehicle_label,
                           rows=enriched, year=year, make=make, model=model,
                           trim=trim)


# ---------------------------------------------------------------- cart
@app.route("/cart")
def cart_view():
    lines, subtotal = cart_detailed()
    promo, discount_cents = landing.active_cart_promo(subtotal)
    return render_template("cart.html", lines=lines, subtotal=subtotal,
                           subtotal_str=money(subtotal),
                           free_ship=money(FREE_SHIP_THRESHOLD_CENTS),
                           promo=promo, discount_cents=discount_cents,
                           discount_str=money(discount_cents),
                           total_cents=subtotal - discount_cents,
                           total_str=money(subtotal - discount_cents))


@app.route("/cart/add", methods=["POST"])
def cart_add():
    pid = request.form.get("product_id", "")
    p = db.get_product(pid)
    if not p or p["status"] != "active":
        abort(404)
    variations = p.get("variations") or []
    # The product page posts the selected variation_id; validate it belongs
    # to this product. Fall back to matching the posted option pills, then
    # to the default variation. The price is always re-derived server-side.
    var = None
    vid = request.form.get("variation_id", "")
    if vid:
        var = next((v for v in variations if str(v["id"]) == str(vid)), None)
    if var is None and variations:
        opts = {}
        for i, g in enumerate(p["variant_groups"]):
            v = request.form.get(f"variant_{i}", "")
            if v in g["values"]:
                opts[g["name"]] = v
        if opts:
            key = db._variation_key(opts)
            var = next((v for v in variations
                        if db._variation_key(v["option_values"]) == key),
                       None)
    if var is None:
        var = variations[0] if variations else None
    if var is None:
        abort(404)
    try:
        qty = max(1, min(99, int(request.form.get("qty", 1))))
    except (TypeError, ValueError):
        qty = 1
    key = f"{pid}::v{var['id']}"
    cart = get_cart()
    if key in cart:
        cart[key]["qty"] = min(99, cart[key]["qty"] + qty)
    else:
        cart[key] = {"product_id": pid, "variation_id": var["id"],
                     "qty": qty}
    session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/cart/update", methods=["POST"])
def cart_update():
    key = request.form.get("key", "")
    cart = get_cart()
    if key in cart:
        try:
            qty = int(request.form.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            del cart[key]
        else:
            cart[key]["qty"] = min(99, qty)
        session["cart"] = cart
    return redirect(url_for("cart_view"))


@app.route("/cart/remove", methods=["POST"])
def cart_remove():
    key = request.form.get("key", "")
    cart = get_cart()
    cart.pop(key, None)
    session["cart"] = cart
    return redirect(url_for("cart_view"))


# ---------------------------------------------------------------- checkout (Stripe)
@app.route("/checkout")
def checkout():
    lines, subtotal = cart_detailed()
    if not lines:
        return redirect(url_for("cart_view"))
    promo, discount_cents = landing.active_cart_promo(subtotal)
    return render_template("checkout.html", lines=lines, subtotal=subtotal,
                           subtotal_str=money(subtotal),
                           payments_ready=payments.stripe_configured(),
                           promo=promo, discount_cents=discount_cents,
                           discount_str=money(discount_cents),
                           total_cents=subtotal - discount_cents,
                           total_str=money(subtotal - discount_cents))


def _checkout_ctx(lines, subtotal):
    """Shared template context for the checkout page + its error states."""
    promo, discount_cents = landing.active_cart_promo(subtotal)
    return {
        "lines": lines, "subtotal": subtotal,
        "subtotal_str": money(subtotal),
        "payments_ready": payments.stripe_configured(),
        "promo": promo, "discount_cents": discount_cents,
        "discount_str": money(discount_cents),
        "total_cents": subtotal - discount_cents,
        "total_str": money(subtotal - discount_cents),
    }


@app.route("/checkout/create", methods=["POST"])
def checkout_create():
    """Validate cart + customer fields, create the Stripe Checkout Session,
    and redirect the buyer to Stripe's hosted payment page."""
    lines, subtotal = cart_detailed()
    if not lines:
        return redirect(url_for("cart_view"))
    promo, discount_cents = landing.active_cart_promo(subtotal)
    if not payments.stripe_configured():
        return render_template("checkout.html", **_checkout_ctx(lines, subtotal),
                               error="Online payments are not configured yet."
                                     " Please contact us to order by phone."), 200
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    customer = {
        "name": name,
        "email": email,
        "line1": request.form.get("line1", "").strip(),
        "line2": request.form.get("line2", "").strip(),
        "city": request.form.get("city", "").strip(),
        "state": request.form.get("state", "").strip(),
        "zip": request.form.get("zip", "").strip(),
    }
    if not name or not email or "@" not in email:
        return render_template("checkout.html", **_checkout_ctx(lines, subtotal),
                               error="Please enter your name and a valid email."), 200
    # Snapshot the order first (status 'new'); the webhook marks it 'paid'.
    # The promo code + discount are snapshotted so they survive expiry.
    oid = db.create_order(customer, lines, subtotal,
                          promo_code=promo["code"] if promo else None,
                          discount_cents=discount_cents)
    try:
        success_url = url_for("checkout_success", _external=True) + \
            "?session_id={CHECKOUT_SESSION_ID}"
        cancel_url = url_for("checkout_cancel", _external=True)
        stripe_session = payments.create_checkout_session(
            oid, lines, email, success_url, cancel_url, promo=promo)
    except Exception as exc:  # Stripe API/network failure: don't strand buyer
        app.logger.warning("stripe session creation failed: %s", exc)
        db.transition_order(oid, "cancelled")
        return render_template("checkout.html", **_checkout_ctx(lines, subtotal),
                               error="Payment setup failed — please try again"
                                     " or contact us to order by phone."), 502
    db.set_stripe_session(oid, stripe_session.id)
    session.pop("cart", None)  # cart snapshot lives in the order now
    return redirect(stripe_session.url, code=303)


@app.route("/checkout/success")
def checkout_success():
    sid = request.args.get("session_id", "")
    order = db.get_order_by_session(sid) if sid else None
    return render_template("checkout_success.html", order=order)


@app.route("/checkout/cancel")
def checkout_cancel():
    return render_template("checkout_cancel.html")


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Stripe event receiver. Verifies the signature, then marks the order
    paid (idempotent), decrements inventory once, and sends emails."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        event = payments.verify_webhook(payload, sig_header)
    except Exception as exc:
        app.logger.warning("stripe webhook rejected: %s", exc)
        return "invalid signature", 400

    if event.get("type") == "checkout.session.completed":
        sess = event["data"]["object"]
        sid = sess.get("id")
        order = db.get_order_by_session(sid)
        if order is None:
            # Session created before the order row existed (shouldn't happen,
            # but never lose a paid order): log loudly for manual follow-up.
            app.logger.error("webhook for unknown stripe session %s", sid)
            return "ok", 200
        if order["status"] != "new":
            return "ok", 200  # duplicate/redelivered event — idempotent
        paid = db.mark_order_paid(order["id"],
                                  payment_intent_id=sess.get("payment_intent"))
        if paid:
            # Promo usage counts once per paid order (mark_order_paid is
            # idempotent, so redelivered webhooks never double-count).
            landing.increment_promo_usage(order["id"])
        low_before = db.low_stock_keys()
        decremented = db.decrement_stock_for_order(order["id"])  # idempotent via flag
        if decremented:
            # Two-way Square POS sync: push the post-sale counts to Square
            # so in-store stock matches the website. Never breaks checkout.
            try:
                square_sync.sync_order_to_square(order["id"])
            except Exception as exc:  # noqa: BLE001 - sync is best-effort
                app.logger.warning("square auto-push failed: %s", exc)
        newly_low = db.low_stock_keys() - low_before
        if newly_low:
            emails.notify_owner_low_stock(db.low_stock_details(newly_low))
        paid_order = db.get_order(order["id"])
        emails.notify_customer_order_paid(paid_order)
        emails.notify_owner_new_order(paid_order)
    elif event.get("type") in ("payment_intent.payment_failed",
                              "checkout.session.expired"):
        obj = event["data"]["object"]
        order = None
        md = obj.get("metadata") or {}
        if md.get("order_id"):
            try:
                order = db.get_order(int(md["order_id"]))
            except (TypeError, ValueError):
                order = None
        if order is None:
            order = db.get_order_by_session(obj.get("id"))
        if order is not None:
            emails.notify_customer_payment_failed(order)
    return "ok", 200


# ---------------------------------------------------------------- admin
# ------------------------------------------------- login brute-force guard
# Simple in-memory throttle: 6 failures from one IP inside 10 minutes locks
# that IP out of the login forms for the rest of the window. Approximate
# across gunicorn workers, but enough to make guessing infeasible.
_LOGIN_ATTEMPTS = {}
_LOGIN_MAX_ATTEMPTS = 6
_LOGIN_WINDOW_SECS = 600


def _login_throttled(ip):
    rec = _LOGIN_ATTEMPTS.get(ip)
    if not rec:
        return False
    if time.time() - rec["first"] > _LOGIN_WINDOW_SECS:
        _LOGIN_ATTEMPTS.pop(ip, None)
        return False
    return rec["count"] >= _LOGIN_MAX_ATTEMPTS


def _record_login_fail(ip):
    now = time.time()
    rec = _LOGIN_ATTEMPTS.get(ip)
    if not rec or now - rec["first"] > _LOGIN_WINDOW_SECS:
        _LOGIN_ATTEMPTS[ip] = {"count": 1, "first": now}
    else:
        rec["count"] += 1


def _clear_login_fails(ip):
    _LOGIN_ATTEMPTS.pop(ip, None)


# ------------------------------------------------------------------ 2FA
def _totp_secret():
    return (os.environ.get("ADMIN_TOTP_SECRET") or "").strip()


def _totp_required():
    return bool(_totp_secret())


def _verify_totp(code):
    secret = _totp_secret()
    if not secret:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify((code or "").strip(),
                                              valid_window=1))
    except Exception:
        return False


_BACKUP_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_backup_codes(n=10):
    codes = []
    for _ in range(n):
        raw = "".join(secrets.choice(_BACKUP_CODE_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def _hash_backup_code(code):
    import hashlib
    return hashlib.sha256((code or "").strip().encode()).hexdigest()


def _safe_next(default):
    nxt = request.args.get("next") or default
    # Only allow relative paths, so a crafted ?next= can't bounce the admin
    # to a lookalike site after login.
    if not nxt.startswith("/") or nxt.startswith("//"):
        return default
    return nxt


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_authed"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    pw = os.environ.get("ADMIN_PASSWORD")
    if not pw:
        # Fail closed: no password configured -> no admin access at all.
        return render_template("admin_login.html", disabled=True), 403
    ip = request.remote_addr or "?"
    if request.method == "POST":
        if _login_throttled(ip):
            return render_template(
                "admin_login.html",
                error="Too many attempts. Wait a few minutes and try again."
            ), 429
        given = request.form.get("password", "")
        if hmac.compare_digest(given, pw):
            _clear_login_fails(ip)
            nxt = _safe_next(url_for("admin_dashboard"))
            if _totp_required():
                # Step 2: the 6-digit authenticator code.
                session["admin_pre_2fa"] = True
                session["admin_pre_2fa_next"] = nxt
                return redirect(url_for("admin_login_2fa"))
            session["admin_authed"] = True
            return redirect(nxt)
        _record_login_fail(ip)
        return render_template("admin_login.html",
                               error="Wrong password."), 401
    return render_template("admin_login.html")


@app.route("/admin/login/2fa", methods=["GET", "POST"])
def admin_login_2fa():
    if not session.get("admin_pre_2fa"):
        return redirect(url_for("admin_login"))
    if not _totp_required():
        # 2FA was disabled mid-flow: complete the login.
        session["admin_authed"] = True
        nxt = session.pop("admin_pre_2fa_next", None) \
            or url_for("admin_dashboard")
        session.pop("admin_pre_2fa", None)
        return redirect(nxt)
    ip = request.remote_addr or "?"
    error = None
    status = 200
    if request.method == "POST":
        if _login_throttled(ip):
            error = "Too many attempts. Wait a few minutes and try again."
            status = 429
        else:
            code = (request.form.get("code") or "").strip()
            if _verify_totp(code) or db.burn_totp_backup_code(code):
                _clear_login_fails(ip)
                session["admin_authed"] = True
                nxt = session.pop("admin_pre_2fa_next", None) \
                    or url_for("admin_dashboard")
                session.pop("admin_pre_2fa", None)
                return redirect(nxt)
            _record_login_fail(ip)
            error = "Wrong code. Check your authenticator app and try again."
    return render_template("admin_login_2fa.html", error=error), status


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_authed", None)
    return redirect(url_for("home"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    stats = db.order_stats()
    q = (request.args.get("q") or "").strip()
    if q:
        products = db.search_products(q)
        archived = []
    else:
        products = db.list_products(include_drafts=True)
        archived = [p for p in db.list_products(
            include_drafts=True, include_archived=True)
            if p["status"] == "archived"]
    return render_template("admin.html", orders=db.list_orders(),
                           stats=stats, inventory=db.list_inventory(),
                           products=products, archived=archived,
                           search_q=q, money=money,
                           low_stock_threshold=db.PLACEHOLDER_LOW_THRESHOLD)


@app.route("/admin/product/<pid>/publish", methods=["POST"])
@admin_required
def admin_product_publish(pid):
    """Track 2: publish/unpublish toggle for draft products. Drafts are
    hidden from the public shop until published here."""
    p = db.get_product(pid)
    if p:
        action = request.form.get("action", "")
        db.set_draft(pid, action != "publish")
    return redirect(url_for("admin_dashboard") + "#products")


# ------------------------------------------------- admin: product manager
PRODUCT_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # per-file upload limit
PRODUCTS_IMG_DIR = os.path.join(app.static_folder, "img", "products")
# Backstop for the whole request body; the friendly per-file check below
# runs first for anything under this.
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


def _parse_dollars(raw, field, errors, required=True):
    s = (raw or "").strip().replace("$", "").replace(",", "")
    if not s:
        if required:
            errors.append(f"{field} is required.")
        return None
    try:
        v = float(s)
    except ValueError:
        errors.append(f"{field} must be a number.")
        return None
    if v < 0:
        errors.append(f"{field} cannot be negative.")
        return None
    return int(round(v * 100))


def _parse_optional_float(raw, field, errors):
    s = (raw or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        errors.append(f"{field} must be a number.")
        return None
    if v < 0:
        errors.append(f"{field} cannot be negative.")
        return None
    return v


def _parse_optional_int(raw, field, errors, default=0):
    s = (raw or "").strip()
    if not s:
        return default
    try:
        v = int(s)
    except ValueError:
        errors.append(f"{field} must be a whole number.")
        return default
    if v < 0:
        errors.append(f"{field} cannot be negative.")
        return default
    return v


def _clean_product_name(raw, errors):
    # Shared guard lives in db.py so the CSV importer reuses it verbatim.
    return db.clean_product_name(raw, errors)


def _guard_public_copy(data, errors):
    # Shared guard lives in db.py so the CSV importer reuses it verbatim.
    return db.guard_public_copy(data, errors)


def _parse_option_groups(raw, errors):
    """Textarea format, one group per line:  Size: H11, 9005, 9006"""
    groups, seen = [], set()
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            errors.append(f"Option line needs 'Group: value, value' format: "
                          f"{line[:40]}")
            continue
        gname, vals = line.split(":", 1)
        gname = gname.strip()
        values = [v.strip() for v in vals.split(",") if v.strip()]
        if not gname or not values:
            errors.append(f"Option group '{line[:40]}' needs a name and at "
                          f"least one value.")
            continue
        if gname.lower() in seen:
            errors.append(f"Duplicate option group '{gname}'.")
            continue
        seen.add(gname.lower())
        groups.append({"name": gname, "values": values})
    if not groups and not errors:
        groups = [{"name": "Size", "values": ["Universal"]}]
    return groups


def _product_form_data(form, pid=None):
    """Validate the product form. Returns (data, errors); data holds cleaned
    values for re-rendering plus DB-ready fields."""
    errors = []
    data = {}
    data["name"] = _clean_product_name(form.get("name"), errors)
    for field in ("blurb", "description", "seo_title", "seo_description"):
        data[field] = (form.get(field) or "").strip()
    data["features"] = [f.strip() for f in
                        (form.get("features") or "").splitlines()
                        if f.strip()]
    _guard_public_copy(data, errors)
    data["category"] = (form.get("category") or "").strip()
    if data["category"] not in CATEGORIES:
        errors.append("Pick a valid category.")
    data["status"] = (form.get("status") or "draft").strip()
    if data["status"] not in db.PRODUCT_STATUSES:
        errors.append("Invalid status.")
    data["product_type"] = (form.get("product_type") or "").strip()
    data["tier"] = (form.get("tier") or "").strip()
    data["tags"] = [t.strip() for t in
                    (form.get("tags") or "").split(",") if t.strip()]
    data["vendor"] = (form.get("vendor") or "").strip()
    data["price_cents"] = _parse_dollars(form.get("price"), "Price", errors)
    if data["price_cents"] is not None and data["price_cents"] <= 0:
        errors.append("Price must be greater than zero.")
    data["sale_price_cents"] = _parse_dollars(
        form.get("sale_price"), "Sale price", errors, required=False)
    if (data["sale_price_cents"] is not None and data["price_cents"]
            and data["sale_price_cents"] >= data["price_cents"]):
        errors.append("Sale price must be below the regular price.")
    data["cost_cents"] = _parse_dollars(
        form.get("cost"), "Cost", errors, required=False)
    data["sku"] = (form.get("sku") or "").strip() or None
    if data["sku"] and db.sku_taken(data["sku"], exclude_pid=pid):
        errors.append(f"SKU '{data['sku']}' is already used by another "
                      f"product.")
    data["msku"] = (form.get("msku") or "").strip() or None
    data["barcode"] = (form.get("barcode") or "").strip() or None
    data["taxable"] = "taxable" in form
    data["weight_oz"] = _parse_optional_float(
        form.get("weight_oz"), "Weight", errors)
    data["length_in"] = _parse_optional_float(
        form.get("length_in"), "Length", errors)
    data["width_in"] = _parse_optional_float(
        form.get("width_in"), "Width", errors)
    data["height_in"] = _parse_optional_float(
        form.get("height_in"), "Height", errors)
    data["country_of_origin"] = (form.get("country_of_origin") or "").strip()
    data["hs_code"] = (form.get("hs_code") or "").strip()
    data["warranty"] = (form.get("warranty") or "1-year warranty").strip()
    data["badge"] = (form.get("badge") or "").strip()
    data["fitment_positions"] = [
        x.strip() for x in (form.get("fitment_positions") or "").split(",")
        if x.strip()]
    data["supplier_name"] = (form.get("supplier_name") or "").strip()
    data["supplier_sku"] = (form.get("supplier_sku") or "").strip()
    data["supplier_notes"] = (form.get("supplier_notes") or "").strip()
    data["notes"] = (form.get("notes") or "").strip()
    data["variant_groups"] = _parse_option_groups(
        form.get("option_groups"), errors)
    data["stock"] = _parse_optional_int(form.get("stock"), "Quantity",
                                        errors, default=0)
    data["low_threshold"] = _parse_optional_int(
        form.get("low_threshold"), "Low-stock threshold", errors, default=5)
    # Raw echoes for re-rendering the form after an error.
    data["_raw"] = {k: form.get(k, "") for k in form.keys()}
    data["_groups_text"] = form.get("option_groups", "")
    return data, errors


def _variation_overrides_from_form(form, groups, errors):
    """Read the per-variation rows. The form renders one row per matrix
    entry (index i); each row carries hidden v{i}_posted and v{i}_vid."""
    overrides = []
    matrix = db._variation_matrix(groups)
    for i, opts in enumerate(matrix):
        if f"v{i}_posted" not in form:
            continue
        vid_raw = (form.get(f"v{i}_vid") or "").strip()
        vid = int(vid_raw) if vid_raw.isdigit() else None
        sku = (form.get(f"v{i}_sku") or "").strip() or None
        if sku and db.variation_sku_taken(sku, exclude_vid=vid):
            errors.append(f"Variation SKU '{sku}' is already used by "
                          f"another variation.")
        price = _parse_dollars(form.get(f"v{i}_price"),
                               f"Variation {i + 1} price", errors,
                               required=False)
        if price is not None and price <= 0:
            errors.append(f"Variation {i + 1} price must be greater than "
                          f"zero (leave blank to use the product price).")
        compare = _parse_dollars(form.get(f"v{i}_compare"),
                                 f"Variation {i + 1} compare-at", errors,
                                 required=False)
        cost = _parse_dollars(form.get(f"v{i}_cost"),
                              f"Variation {i + 1} cost", errors,
                              required=False)
        if (compare is not None and price is not None
                and compare <= price):
            errors.append(f"Variation {i + 1}: compare-at must be above "
                          f"the variation price.")
        overrides.append({
            "_posted": True,
            "option_values": opts,
            "vid": vid,
            "sku": sku,
            "msku": (form.get(f"v{i}_msku") or "").strip() or None,
            "barcode": (form.get(f"v{i}_barcode") or "").strip() or None,
            "price_cents": price,
            "compare_at_cents": compare,
            "cost_cents": cost,
            "inventory_qty": _parse_optional_int(
                form.get(f"v{i}_qty"), f"Variation {i + 1} quantity",
                errors, default=0),
            "track_inventory": f"v{i}_track" in form,
            "allow_oversell": f"v{i}_oversell" in form,
            "low_threshold": _parse_optional_int(
                form.get(f"v{i}_low"), f"Variation {i + 1} low threshold",
                errors, default=5),
            "image_src": (form.get(f"v{i}_image") or "").strip() or None,
        })
    return overrides


def _validate_uploads(files):
    """Type/size-check uploaded photos without writing anything.
    Returns ([(safe_name, bytes)], error)."""
    out = []
    for f in files.getlist("photos"):
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit(".", 1)[-1].lower() \
            if "." in f.filename else ""
        if ext not in PRODUCT_IMAGE_EXTS:
            return None, (f"Rejected {f.filename}: only "
                          f"{'/'.join(sorted(PRODUCT_IMAGE_EXTS))} allowed.")
        blob = f.read()
        if len(blob) > MAX_IMAGE_BYTES:
            return None, (f"Rejected {f.filename}: over the 5 MB limit.")
        if not blob:
            continue
        out.append((secure_filename(f.filename), blob))
    return out, None


def _existing_image_rows(form):
    """Kept existing images from the image manager (ordered by position)."""
    rows, i = [], 0
    while f"imgsrc_{i}" in form:
        src = (form.get(f"imgsrc_{i}") or "").strip()
        if src:
            try:
                pos = int(form.get(f"imgpos_{i}", i))
            except (TypeError, ValueError):
                pos = i
            rows.append({"src": src, "pos": pos,
                         "remove": f"imgremove_{i}" in form})
        i += 1
    return [{"src": r["src"], "primary": False}
            for r in sorted((r for r in rows if not r["remove"]),
                            key=lambda r: r["pos"])]


def _write_uploads(pid, uploads):
    """Write validated upload blobs to static/img/products/<pid>/. Returns
    the list of new src paths."""
    srcs = []
    if not uploads:
        return srcs
    udir = os.path.join(PRODUCTS_IMG_DIR, pid)
    os.makedirs(udir, exist_ok=True)
    for safe_name, blob in uploads:
        fname = f"{uuid.uuid4().hex[:8]}-{safe_name}"
        with open(os.path.join(udir, fname), "wb") as fh:
            fh.write(blob)
        srcs.append(f"/static/img/products/{pid}/{fname}")
    return srcs


def _build_image_list(form, pid, uploads=None):
    """Combine kept existing images, written uploads, and the image-URL
    field; apply the primary flag. Returns (images, error)."""
    images = _existing_image_rows(form)
    url = (form.get("image_url") or "").strip()
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        # Validate before writing anything so a bad URL can't orphan files.
        return None, "Image URL must start with http:// or https://."
    if uploads:
        images += [{"src": s, "primary": False}
                   for s in _write_uploads(pid, uploads)]
    if url:
        images.append({"src": url, "primary": False})
    primary_src = (form.get("primary_image") or "").strip()
    picked = False
    for im in images:
        im["primary"] = bool(primary_src) and im["src"] == primary_src
        picked = picked or im["primary"]
    if images and not picked:
        images[0]["primary"] = True
    return images, None


def _field_values(product):
    """Form-field display values, keyed by field name. Used for GET
    prefills and as the base for POST error redisplay (raw posted values
    merged on top)."""
    if product is None:
        return {
            "name": "", "status": "draft", "category": "led-bulbs",
            "product_type": "", "tier": "", "blurb": "", "description": "",
            "features": "", "badge": "", "warranty": "1-year warranty",
            "price": "", "sale_price": "", "cost": "", "sku": "",
            "msku": "", "barcode": "", "taxable": True, "weight_oz": "",
            "length_in": "", "width_in": "", "height_in": "",
            "country_of_origin": "", "hs_code": "", "seo_title": "",
            "seo_description": "", "tags": "", "fitment_positions": "",
            "supplier_name": "", "supplier_sku": "", "supplier_notes": "",
            "notes": "", "stock": "25", "low_threshold": "5",
            "vendor": "",
        }

    def dollars(c):
        return "" if c is None else f"{c / 100:.2f}"

    def num(v):
        return "" if v is None else str(v)

    stock = db.get_stock(product["id"])
    return {
        "name": product["name"], "status": product["status"],
        "category": product["category"],
        "product_type": product["product_type"],
        "tier": product["tier"], "blurb": product["blurb"],
        "description": product["description"],
        "features": "\n".join(product["features"]),
        "badge": product["badge"], "warranty": product["warranty"],
        "price": dollars(product["price_cents"]),
        "sale_price": dollars(product["sale_price_cents"]),
        "cost": dollars(product["cost_cents"]),
        "sku": product["sku"] or "", "msku": product["msku"] or "",
        "barcode": product["barcode"] or "",
        "taxable": product["taxable"],
        "weight_oz": num(product["weight_oz"]),
        "length_in": num(product["length_in"]),
        "width_in": num(product["width_in"]),
        "height_in": num(product["height_in"]),
        "country_of_origin": product["country_of_origin"],
        "hs_code": product["hs_code"],
        "seo_title": product["seo_title"],
        "seo_description": product["seo_description"],
        "tags": ", ".join(product["tags"]),
        "fitment_positions": ", ".join(product["fitment_positions"]),
        "supplier_name": product["supplier_name"],
        "supplier_sku": product["supplier_sku"],
        "supplier_notes": product["supplier_notes"],
        "notes": product["notes"],
        "vendor": product["vendor"] or "",
        "stock": str(stock["stock"]) if stock else "0",
        "low_threshold": str(stock["low_threshold"]) if stock else "5",
    }


def _form_prefill(product=None):
    """Build template context for the product form (GET, or POST redisplay
    is handled separately with raw echoes)."""
    if product is None:
        return {
            "p": None, "pid": None, "is_new": True,
            "f": _field_values(None),
            "groups_text": "Size: Universal",
            "variations": [], "stock": 25, "low_threshold": 5,
        }
    groups = product.get("variant_groups") or []
    groups_text = "\n".join(
        f"{g['name']}: {', '.join(g['values'])}" for g in groups)
    existing = {db._variation_key(v["option_values"]): v
                for v in product.get("variations", [])}
    variations = []
    for i, opts in enumerate(db._variation_matrix(groups)):
        v = existing.get(db._variation_key(opts), {})
        variations.append({
            "i": i, "label": " / ".join(str(x) for x in opts.values()) or
            "Standard", "opts_json": json.dumps(opts),
            "vid": v.get("id", ""), "sku": v.get("sku") or "",
            "msku": v.get("msku") or "", "barcode": v.get("barcode") or "",
            "price": "" if v.get("price_cents") is None
            else f"{v['price_cents'] / 100:.2f}",
            "compare": "" if v.get("compare_at_cents") is None
            else f"{v['compare_at_cents'] / 100:.2f}",
            "cost": "" if v.get("cost_cents") is None
            else f"{v['cost_cents'] / 100:.2f}",
            "qty": v.get("inventory_qty", 0),
            "track": v.get("track_inventory", True),
            "oversell": v.get("allow_oversell", False),
            "low": v.get("low_threshold", 5),
            "image": v.get("image_src") or "",
            "margin": v.get("margin"),
            "eff_price": v.get("effective_price_cents"),
        })
    stock = db.get_stock(product["id"])
    return {
        "p": product, "pid": product["id"], "is_new": False,
        "f": _field_values(product),
        "groups_text": groups_text, "variations": variations,
        "stock": stock["stock"] if stock else 0,
        "low_threshold": stock["low_threshold"] if stock else 5,
    }


import csvimport


# ------------------------------------------------- bulk CSV product import
#
# Two-step safety flow: upload -> validate/preview (no writes) -> confirm.
# The uploaded file is staged on disk under a random token; confirm
# re-validates before writing, so tampering or a stale preview cannot
# write unchecked data.

def _import_staging_dir():
    d = db.DB_PATH.parent / "import_staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _prune_staging():
    try:
        now = time.time()
        for p in _import_staging_dir().glob("*.csv"):
            if now - p.stat().st_mtime > 2 * 3600:
                p.unlink(missing_ok=True)
        for p in _import_staging_dir().glob("square-*.json"):
            if now - p.stat().st_mtime > 2 * 3600:
                p.unlink(missing_ok=True)
    except OSError:
        pass


@app.route("/admin/products/import", methods=["GET"])
@admin_required
def admin_product_import():
    _prune_staging()
    return render_template("admin_product_import.html", preview=None,
                           errors=[], mode="full", token=None,
                           filename=None)


@app.route("/admin/products/import/template")
@admin_required
def admin_product_import_template():
    mode = request.args.get("mode", "full")
    if mode not in ("full", "inventory"):
        mode = "full"
    return Response(
        csvimport.template_csv(mode), mimetype="text/csv",
        headers={"Content-Disposition":
                 "attachment; filename=product-import-%s-template.csv" % mode})


@app.route("/admin/products/export")
@admin_required
def admin_product_export():
    products = db.list_products(include_drafts=True, include_archived=True)
    return Response(
        csvimport.export_csv(products), mimetype="text/csv",
        headers={"Content-Disposition":
                 "attachment; filename=bravo-products-export.csv"})


@app.route("/admin/products/import", methods=["POST"])
@admin_required
def admin_product_import_upload():
    mode = request.form.get("mode", "full")
    if mode not in ("full", "inventory"):
        mode = "full"
    f = request.files.get("csv")
    errors = []
    if not f or not f.filename:
        errors.append("Choose a CSV file to upload.")
    elif not f.filename.lower().endswith(".csv"):
        errors.append("Only .csv files are accepted.")
    if errors:
        return render_template("admin_product_import.html", preview=None,
                               errors=errors, mode=mode, token=None,
                               filename=None), 400
    raw = f.read(csvimport.MAX_CSV_BYTES + 1)
    if len(raw) > csvimport.MAX_CSV_BYTES:
        return render_template(
            "admin_product_import.html", preview=None,
            errors=["File is larger than 5 MB -- split it and upload "
                    "in parts."],
            mode=mode, token=None, filename=None), 400
    token = uuid.uuid4().hex
    (_import_staging_dir() / f"{token}.csv").write_bytes(raw)
    result = csvimport.validate(raw, mode)
    return render_template("admin_product_import.html", preview=result,
                           errors=[], mode=mode, token=token,
                           filename=f.filename)


@app.route("/admin/products/import/confirm", methods=["POST"])
@admin_required
def admin_product_import_confirm():
    token = request.form.get("token", "")
    mode = request.form.get("mode", "full")
    if mode not in ("full", "inventory"):
        mode = "full"
    path = _import_staging_dir() / f"{token}.csv"
    if not re.fullmatch(r"[0-9a-f]{32}", token) or not path.exists():
        flash("That import expired -- please upload the file again.")
        return redirect(url_for("admin_product_import"))
    result = csvimport.validate(path.read_bytes(), mode)
    if not result["valid"]:
        # Re-show the preview with the errors; nothing was written.
        return render_template("admin_product_import.html", preview=result,
                               errors=[], mode=mode, token=token,
                               filename="staged upload"), 400
    counts = csvimport.apply(result)
    path.unlink(missing_ok=True)
    if mode == "inventory":
        flash("Inventory import complete: %d stock count(s) updated."
              % counts["updated"])
    else:
        flash("Import complete: %d product(s) created, %d updated, "
              "%d variation(s) created/updated."
              % (counts["created"], counts["updated"],
                 counts["variations"]))
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/products/new", methods=["GET", "POST"])
@admin_required
def admin_product_new():
    if request.method == "GET":
        return render_template("admin_product_form.html",
                               errors=[], money=money, **_form_prefill())
    form = request.form
    data, errors = _product_form_data(form)
    overrides = _variation_overrides_from_form(
        form, data["variant_groups"], errors)
    uploads, upload_error = _validate_uploads(request.files)
    if upload_error:
        errors.append(upload_error)
    if errors:
        ctx = _form_prefill()
        fvals = _field_values(product if "product" in dir() else None)
        fvals.update(data["_raw"])
        if "taxable" not in data["_raw"]:
            fvals["taxable"] = False
        ctx["f"] = fvals
        ctx["errors"] = errors
        ctx["groups_text"] = data["_groups_text"]
        ctx["p"] = None
        return render_template("admin_product_form.html", money=money, **ctx), 400
    pid = db.unique_product_id(data["name"])
    images, img_error = _build_image_list(form, pid, uploads)
    if img_error:
        ctx = _form_prefill()
        fvals = _field_values(product if "product" in dir() else None)
        fvals.update(data["_raw"])
        if "taxable" not in data["_raw"]:
            fvals["taxable"] = False
        ctx["f"] = fvals
        ctx["errors"] = [img_error]
        ctx["groups_text"] = data["_groups_text"]
        ctx["p"] = None
        return render_template("admin_product_form.html", money=money, **ctx), 400
    data["images"] = images or []
    db.create_product({**data, "id": pid})
    db.sync_variations(pid, data["variant_groups"], overrides,
                       new_track_default=1)
    db.set_stock(pid, data["stock"], data["low_threshold"])
    flash(f"Product '{data['name']}' created as {data['status']}.")
    return redirect(url_for("admin_product_edit", pid=pid))


@app.route("/admin/product/<pid>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_edit(pid):
    product = db.get_product(pid)
    if not product:
        abort(404)
    if request.method == "GET":
        saved = request.args.get("saved")
        return render_template("admin_product_form.html", errors=[], money=money,
                               just_saved=bool(saved),
                               **_form_prefill(product))
    form = request.form
    data, errors = _product_form_data(form, pid=pid)
    overrides = _variation_overrides_from_form(
        form, data["variant_groups"], errors)
    uploads, upload_error = _validate_uploads(request.files)
    if upload_error:
        errors.append(upload_error)
    if errors:
        ctx = _form_prefill(product)
        fvals = _field_values(product if "product" in dir() else None)
        fvals.update(data["_raw"])
        if "taxable" not in data["_raw"]:
            fvals["taxable"] = False
        ctx["f"] = fvals
        ctx["errors"] = errors
        ctx["groups_text"] = data["_groups_text"]
        return render_template("admin_product_form.html", money=money, **ctx), 400
    images, img_error = _build_image_list(form, pid, uploads)
    if img_error:
        ctx = _form_prefill(product)
        fvals = _field_values(product if "product" in dir() else None)
        fvals.update(data["_raw"])
        if "taxable" not in data["_raw"]:
            fvals["taxable"] = False
        ctx["f"] = fvals
        ctx["errors"] = [img_error]
        ctx["groups_text"] = data["_groups_text"]
        return render_template("admin_product_form.html", money=money, **ctx), 400
    data["images"] = images or []
    db.update_product(pid, data)
    db.sync_variations(pid, data["variant_groups"], overrides,
                       new_track_default=1)
    db.set_stock(pid, data["stock"], data["low_threshold"])
    return redirect(url_for("admin_product_edit", pid=pid, saved=1))


@app.route("/admin/product/<pid>/delete", methods=["POST"])
@admin_required
def admin_product_delete(pid):
    """Hard delete. Refused when any order references the product — the
    admin must archive/unpublish it instead, so order history stays
    intact."""
    ok, message = db.delete_product(pid)
    if ok:
        flash(f"Product deleted. ({message})")
        return redirect(url_for("admin_dashboard") + "#products")
    flash(message)
    return redirect(url_for("admin_product_edit", pid=pid))


@app.route("/admin/product/<pid>/archive", methods=["POST"])
@admin_required
def admin_product_archive(pid):
    action = request.form.get("action", "archive")
    p = db.get_product(pid)
    if not p:
        abort(404)
    db.set_status(pid, "archived" if action == "archive" else "draft")
    flash(f"'{p['name']}' "
          f"{'archived' if action == 'archive' else 'moved to drafts'}.")
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/inventory", methods=["POST"])
@admin_required
def admin_inventory():
    pid = request.form.get("product_id", "")
    if db.get_stock(pid) is not None:
        try:
            db.set_stock(pid, int(request.form.get("stock", 0)),
                         int(request.form.get("low_threshold",
                                              db.PLACEHOLDER_LOW_THRESHOLD)))
        except (TypeError, ValueError):
            pass
    return redirect(url_for("admin_dashboard") + "#inventory")


# ------------------------------------------------- admin: order manager
#
# Order dashboard, detail, notes, refunds, Pirate Ship CSV export, bulk
# tracking import, packing slips, pick lists, and notification settings.


@app.route("/admin/orders")
@admin_required
def admin_orders():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    orders = db.search_orders(q=q or None,
                              status=status if status in
                              db.ORDER_STATUSES else None,
                              date_from=date_from or None,
                              date_to=date_to or None)
    return render_template("admin_orders.html", orders=orders, money=money,
                           q=q, status=status, date_from=date_from,
                           date_to=date_to,
                           statuses=db.ORDER_STATUSES,
                           unfulfilled=db.unfulfilled_orders())


@app.route("/admin/order/<int:oid>")
@admin_required
def admin_order_detail(oid):
    order = db.get_order(oid)
    if not order:
        abort(404)
    carrier, track_url = ((None, None) if not order.get("tracking_number")
                          else shiputil.detect_carrier(
                              order["tracking_number"]))
    return render_template("admin_order_detail.html", o=order, money=money,
                           events=db.list_order_events(oid),
                           carrier=carrier, track_url=track_url,
                           stripe_ready=payments.stripe_configured(),
                           refundable_cents=order["total_cents"]
                           - (order.get("refunded_cents") or 0))


@app.route("/admin/order/<int:oid>/status", methods=["POST"])
@admin_required
def admin_order_status(oid):
    action = request.form.get("action", "")
    tracking = request.form.get("tracking", "").strip()
    ok, msg = db.transition_order(oid, action,
                                  tracking_number=tracking or None)
    if ok and action == "paid":
        landing.increment_promo_usage(oid)
    if ok and action == "shipped":
        order = db.get_order(oid)
        if order:
            carrier, url = shiputil.detect_carrier(
                order.get("tracking_number"))
            emails.notify_customer_shipped(order, carrier=carrier,
                                           tracking_url=url)
    flash(msg)
    return redirect(request.form.get("next")
                    or (url_for("admin_dashboard") + f"#order-{oid}"))


@app.route("/admin/order/<int:oid>/notes", methods=["POST"])
@admin_required
def admin_order_notes(oid):
    db.update_order_notes(oid, request.form.get("notes", ""))
    flash("Admin note saved.")
    return redirect(url_for("admin_order_detail", oid=oid) + "#notes")


@app.route("/admin/order/<int:oid>/refund", methods=["POST"])
@admin_required
def admin_order_refund(oid):
    order = db.get_order(oid)
    if not order:
        abort(404)
    detail = url_for("admin_order_detail", oid=oid)
    refundable = order["total_cents"] - (order.get("refunded_cents") or 0)
    amount_raw = (request.form.get("amount") or "").strip().replace(
        "$", "").replace(",", "")
    reason_key = request.form.get("reason", "").strip()
    if not order.get("stripe_payment_intent_id"):
        flash("No Stripe payment on this order — issue the refund in the "
              "Stripe dashboard, then add an admin note.")
        return redirect(detail)
    try:
        amount_cents = (refundable if not amount_raw
                        else int(round(float(amount_raw) * 100)))
    except (TypeError, ValueError):
        flash("Refund amount must be a number.")
        return redirect(detail)
    if amount_cents <= 0 or amount_cents > refundable:
        flash(f"Refund amount must be between $0.01 and "
              f"{money(refundable)}.")
        return redirect(detail)
    stripe_reason = {"requested": "requested_by_customer",
                     "duplicate": "duplicate",
                     "fraud": "fraudulent"}.get(reason_key)
    try:
        payments.refund_payment_intent(order["stripe_payment_intent_id"],
                                       None if amount_cents == refundable
                                       else amount_cents,
                                       stripe_reason)
    except Exception as exc:  # noqa: BLE001 - surface Stripe errors
        flash(f"Refund failed: {exc}")
        return redirect(detail)
    db.record_refund(oid, amount_cents, reason_key or "full refund")
    flash(f"Refunded {money(amount_cents)} on order #{oid}.")
    return redirect(detail)


@app.route("/admin/orders/export", methods=["POST"])
@admin_required
def admin_orders_export():
    """Pirate Ship CSV: selected paid orders -> import into Pirate Ship ->
    buy labels there -> paste tracking back on /admin/orders/tracking."""
    raw_ids = request.form.getlist("order_ids")
    orders = []
    for raw in raw_ids:
        try:
            o = db.get_order(int(raw))
        except (TypeError, ValueError):
            continue
        if o and o["status"] == "paid":
            orders.append(o)
    if not orders:
        flash("No paid, unshipped orders selected.")
        return redirect(url_for("admin_orders"))
    try:
        tare_oz = float(db.get_setting("shipping_tare_oz", "3") or 3)
    except (TypeError, ValueError):
        tare_oz = 3.0
    csv_text = shiputil.pirate_ship_csv(orders,
                                        get_product=db.get_product,
                                        tare_oz=tare_oz)
    return Response(csv_text, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             "attachment; filename=pirate-ship-orders.csv"})


@app.route("/admin/orders/tracking", methods=["GET", "POST"])
@admin_required
def admin_orders_tracking():
    """Bulk tracking import: one `order_id tracking_number` per line."""
    results = []
    if request.method == "POST":
        entries, errors = shiputil.parse_bulk_tracking(
            request.form.get("bulk", ""))
        for oid, tracking in entries:
            order = db.get_order(oid)
            if not order:
                results.append((oid, tracking, False, "order not found"))
                continue
            if order["status"] != "paid":
                results.append((oid, tracking, False,
                                f"status is '{order['status']}'"
                                " — must be paid"))
                continue
            carrier, url = shiputil.detect_carrier(tracking)
            ok, msg = db.transition_order(oid, "shipped",
                                           tracking_number=tracking)
            if ok:
                emails.notify_customer_shipped(db.get_order(oid),
                                               carrier=carrier,
                                               tracking_url=url)
                results.append((oid, tracking, True, f"shipped ({carrier})"))
            else:
                results.append((oid, tracking, False, msg))
        for err in errors:
            results.append((None, None, False, err))
        if entries and not errors:
            flash(f"Processed {len(entries)} tracking line(s).")
    return render_template("admin_tracking.html", results=results,
                           unfulfilled=db.unfulfilled_orders())


@app.route("/admin/order/<int:oid>/packing-slip")
@admin_required
def admin_packing_slip(oid):
    order = db.get_order(oid)
    if not order:
        abort(404)
    return render_template("packing_slip.html", o=order, money=money)


@app.route("/admin/orders/pick-list")
@admin_required
def admin_pick_list():
    """Aggregate pick list for the selected (or all unfulfilled) orders."""
    raw_ids = request.args.getlist("ids")
    if raw_ids:
        orders = []
        for raw in raw_ids:
            try:
                o = db.get_order(int(raw))
            except (TypeError, ValueError):
                continue
            if o and o["status"] == "paid":
                orders.append(o)
    else:
        orders = db.unfulfilled_orders()
    agg = {}
    for o in orders:
        for item in o["line_items"]:
            key = (item.get("product_id"), item.get("variation_id"),
                   item.get("variation_label") or "", item.get("name") or "")
            agg[key] = agg.get(key, 0) + max(0, int(item.get("qty", 0) or 0))
    lines = []
    for (pid, vid, vlabel, name), qty in sorted(
            agg.items(), key=lambda kv: (kv[0][3], kv[0][2])):
        sku = ""
        if vid:
            v = db.get_variation(vid)
            sku = (v.get("sku") if v else "") or ""
        if not sku and pid:
            p = db.get_product(pid)
            sku = (p.get("sku") if p else "") or ""
        lines.append({"name": name, "variation_label": vlabel,
                      "sku": sku, "qty": qty})
    return render_template("pick_list.html", lines=lines, orders=orders,
                           money=money)


SQUARE_SECRET_KEYS = ("square_access_token", "square_webhook_signature_key")


def _masked_square_settings(settings):
    """Copy of the settings dict safe for templates: Square secrets are
    replaced with a configured/last-4 indicator, never the raw value."""
    out = dict(settings)
    for key in SQUARE_SECRET_KEYS:
        raw = out.get(key, "")
        out[key + "_configured"] = bool(raw)
        out[key + "_last4"] = raw[-4:] if raw else ""
        out[key] = ""  # password field renders empty; blank POST keeps it
    return out


def _settings_2fa_ctx():
    return {
        "totp_enabled": _totp_required(),
        "backup_count": len(db.get_totp_backup_hashes()),
    }


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        for key, _label, _group in db.NOTIFY_TOGGLES:
            db.set_setting(key,
                           "1" if request.form.get(key) == "on" else "0")
        db.set_setting("email_owner_orders",
                       request.form.get("email_owner_orders", "").strip())
        db.set_setting("email_owner_alerts",
                       request.form.get("email_owner_alerts", "").strip())
        try:
            tare = max(0.0, float(request.form.get("shipping_tare_oz",
                                                  "3") or 0))
        except (TypeError, ValueError):
            tare = 3.0
        db.set_setting("shipping_tare_oz", str(tare))
        # --- Square POS: blank secret fields keep the saved value ---
        token = request.form.get("square_access_token", "").strip()
        if token:
            db.set_setting("square_access_token", token)
        db.set_setting("square_location_id",
                       request.form.get("square_location_id", "").strip())
        env = request.form.get("square_environment", "sandbox")
        db.set_setting("square_environment",
                       env if env in square_sync.ENVIRONMENTS else "sandbox")
        sigkey = request.form.get("square_webhook_signature_key",
                                  "").strip()
        if sigkey:
            db.set_setting("square_webhook_signature_key", sigkey)
        db.set_setting("square_auto_sync",
                       "1" if request.form.get("square_auto_sync") == "on"
                       else "0")
        flash("Settings saved.")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html",
                           settings=_masked_square_settings(
                               db.get_all_settings()),
                           toggles=db.NOTIFY_TOGGLES,
                           **_settings_2fa_ctx())


@app.route("/admin/settings/2fa-codes", methods=["POST"])
@admin_required
def admin_settings_2fa_codes():
    """Generate a fresh set of one-time backup codes (shown once)."""
    codes = _generate_backup_codes(10)
    db.set_totp_backup_hashes([_hash_backup_code(c) for c in codes])
    return render_template("admin_settings.html",
                           settings=_masked_square_settings(
                               db.get_all_settings()),
                           toggles=db.NOTIFY_TOGGLES,
                           new_2fa_codes=codes,
                           **_settings_2fa_ctx())


# ------------------------------------------------------------ Square POS
#
# Two-way inventory sync with the in-store Square POS. The website is the
# catalog master: "push catalog" creates Square items/variations from our
# products (matched on SKU), then counts sync both ways.


@app.route("/admin/square")
@admin_required
def admin_square():
    products = db.list_products(include_drafts=False)
    rows = []
    linked = unlinked = 0
    for p in products:
        if p.get("status") != "active":
            continue
        pvars = [v for v in (p.get("variations") or [])
                 if v.get("track_inventory")]
        p_linked = sum(1 for v in pvars
                       if v.get("square_catalog_object_id"))
        linked += p_linked
        unlinked += len(pvars) - p_linked
        rows.append({
            "id": p["id"], "name": p["name"], "sku": p.get("sku") or "",
            "item_id": p.get("square_catalog_object_id") or "",
            "synced_at": p.get("square_last_synced_at") or "",
            "variations": pvars,
            "linked": p_linked, "total": len(pvars),
        })
    return render_template("admin_square.html",
                           configured=square_sync.square_configured(),
                           environment=square_sync.environment(),
                           auto_sync=square_sync.auto_sync_enabled(),
                           rows=rows, linked=linked, unlinked=unlinked,
                           log=db.list_square_sync_log(100))


@app.route("/admin/square/test", methods=["POST"])
@admin_required
def admin_square_test():
    ok, message = square_sync.test_connection()
    flash(message)
    return redirect(url_for("admin_square"))


@app.route("/admin/square/push-catalog", methods=["POST"])
@admin_required
def admin_square_push_catalog():
    try:
        report = square_sync.push_catalog()
    except square_sync.SquareError as exc:
        flash(f"Square push failed: {exc}")
        return redirect(url_for("admin_square"))
    created = sum(1 for r in report if r["action"] == "created")
    updated = sum(1 for r in report if r["action"] == "updated")
    skipped = sum(1 for r in report if r["action"] == "skipped")
    errors = [r for r in report if r["action"] == "error"]
    flash(f"Square catalog push: {created} created, {updated} updated, "
          f"{skipped} skipped, {len(errors)} errors.")
    for r in errors[:5]:
        flash(f"Error — {r['name']}: {r['detail']}")
    return redirect(url_for("admin_square"))


@app.route("/admin/square/pull", methods=["POST"])
@admin_required
def admin_square_pull():
    try:
        report = square_sync.pull_inventory_counts()
    except square_sync.SquareError as exc:
        flash(f"Square pull failed: {exc}")
        return redirect(url_for("admin_square"))
    changed = sum(1 for r in report
                  if r["result"] in ("ok", "clamped")
                  and r["old_qty"] != r["new_qty"])
    clamped = sum(1 for r in report if r["result"] == "clamped")
    skipped = sum(1 for r in report if r["result"] == "skipped")
    flash(f"Square pull: {changed} counts updated"
          + (f" ({clamped} clamped at 0)" if clamped else "")
          + (f", {skipped} unlinked skipped" if skipped else "") + ".")
    return redirect(url_for("admin_square"))


@app.route("/admin/square/product/<pid>/push", methods=["POST"])
@admin_required
def admin_square_product_push(pid):
    product = db.get_product(pid)
    if not product:
        abort(404)
    try:
        report = square_sync.push_catalog([product])
    except square_sync.SquareError as exc:
        flash(f"Square push failed: {exc}")
        return redirect(url_for("admin_square"))
    r = report[0] if report else {"action": "error", "detail": "no report"}
    flash(f"“{product['name']}”: {r['action']} — {r.get('detail', '')}")
    return redirect(url_for("admin_square"))


@app.route("/admin/square/product/<pid>/pull", methods=["POST"])
@admin_required
def admin_square_product_pull(pid):
    product = db.get_product(pid)
    if not product:
        abort(404)
    object_ids = [v.get("square_catalog_object_id")
                  for v in (product.get("variations") or [])
                  if v.get("square_catalog_object_id")]
    if not object_ids:
        flash(f"“{product['name']}” has no Square-linked variations yet —"
              " push it to Square first.")
        return redirect(url_for("admin_square"))
    try:
        report = square_sync.pull_inventory_counts(object_ids)
    except square_sync.SquareError as exc:
        flash(f"Square pull failed: {exc}")
        return redirect(url_for("admin_square"))
    changed = sum(1 for r in report
                  if r["result"] in ("ok", "clamped")
                  and r["old_qty"] != r["new_qty"])
    flash(f"“{product['name']}”: {changed} variation count(s) updated"
          " from Square.")
    return redirect(url_for("admin_square"))


@app.route("/admin/square/import", methods=["POST"])
@admin_required
def admin_square_import():
    """Fetch the Square catalog and show the validation preview.

    Nothing is written: the fetched snapshot is staged to disk and the
    plan is built from it. Confirm posts to /admin/square/import/confirm.
    """
    _prune_staging()
    if not square_sync.square_configured():
        flash("Connect Square in Settings first.")
        return redirect(url_for("admin_square"))
    try:
        snapshot = square_import.fetch_catalog()
    except square_sync.SquareError as exc:
        flash(f"Square import failed: {exc}")
        return redirect(url_for("admin_square"))
    token = uuid.uuid4().hex
    (_import_staging_dir() / f"square-{token}.json").write_text(
        square_import.snapshot_to_json(snapshot))
    plan = square_import.plan_import(snapshot)
    return render_template("admin_square_import.html", plan=plan, token=token,
                           fetched_at=snapshot.get("fetched_at"))


@app.route("/admin/square/import/confirm", methods=["POST"])
@admin_required
def admin_square_import_confirm():
    """Apply a staged Square import: re-validates, imports 'create' items
    as drafts, skips the rest. Nothing was written before this point."""
    token = request.form.get("token", "")
    path = _import_staging_dir() / f"square-{token}.json"
    if not re.fullmatch(r"[0-9a-f]{32}", token) or not path.exists():
        flash("That import expired — please fetch the catalog again.")
        return redirect(url_for("admin_square"))
    try:
        snapshot = square_import.snapshot_from_json(path.read_text())
    except (ValueError, OSError):
        flash("That import is corrupt — please fetch the catalog again.")
        return redirect(url_for("admin_square"))
    counts = square_import.apply_import(snapshot)
    path.unlink(missing_ok=True)
    flash(f"Square import complete: {counts['created']} product(s) created"
          f" as drafts, {counts['variations']} variation(s),"
          f" {counts['skipped']} skipped, {counts['errors']} blocked.")
    return redirect(url_for("admin_square"))


@app.route("/webhooks/square", methods=["POST"])
def square_webhook():
    """Square event receiver. Verifies the HMAC-SHA1 signature, then
    applies inventory.count.updated events (fail closed: bad signature or
    no signature key configured -> 400, nothing applied)."""
    payload = request.get_data()
    sig = request.headers.get("x-square-hmacsha1-signature", "")
    try:
        square_sync.verify_webhook_signature(payload, sig, request.url)
    except Exception as exc:
        app.logger.warning("square webhook rejected: %s", exc)
        return "invalid signature", 400
    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception:
        return "bad payload", 400
    if event.get("type") == "inventory.count.updated":
        applied, skipped = square_sync.handle_inventory_webhook(event)
        app.logger.info("square webhook: %d applied, %d skipped",
                        applied, skipped)
    return "ok", 200


# ------------------------------------------------------------ static pages
@app.route("/shipping")
def shipping():
    return render_template("shipping.html")


@app.route("/dot-compliance")
def dot_compliance():
    return render_template("dot-compliance.html")


@app.route("/returns")
def returns():
    return render_template("returns.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:120]
        email = (request.form.get("email") or "").strip()[:160]
        message = (request.form.get("message") or "").strip()[:4000]
        if not name or "@" not in email or not message:
            return render_template("contact.html",
                                   error="Please fill in your name, "
                                         "a valid email, and a message."), 200
        emails.notify_owner_contact_message(name, email, message)
        return render_template("contact.html", sent=True)
    return render_template("contact.html")


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True)
