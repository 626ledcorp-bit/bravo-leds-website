"""Track B — content & merchandising for the Bravo LEDs storefront.

Everything in this module lives in a Flask Blueprint so the parallel
backend track can keep working on app.py untouched. The coordinator wires
it in after both tracks finish:

    from content import register_content_routes
    register_content_routes(app)

Schema owned here (all idempotent — safe to run on every startup):
  - newsletter_subscribers(id, email UNIQUE, created_at)
  - reviews(id, product_id, name, rating, body, status, created_at)
  - bundles(id, name, slug UNIQUE, product_ids JSON, price_cents, blurb)
  - products.sale_price_cents (PRAGMA-guarded ALTER; NULL = not on sale)

Content rules honored throughout:
  - Zero occurrences of the word "headlight" in user-facing copy.
    Use "low beam" / "high beam" / "daytime running light".
  - The word "off-road" appears only in the standard disclaimer, the
    footer, and /dot-compliance — never in product/category names or nav.
  - 1-year warranty on ALL products. Ships in 1-2 business days via
    USPS/UPS. 30-day returns on unused items.
"""

import csv
import io
import json
import re
from datetime import datetime, timezone

from flask import (Blueprint, abort, redirect, render_template, request,
                   session, url_for)

import db
from db import _connect  # reuse the store's SQLite connection helper

bp = Blueprint("content", __name__)

DISCLAIMER_HTML = (
    'For off-road and fog light use only. Not DOT/SAE approved for on-road '
    'use. Check your local laws. <a href="/dot-compliance">Learn more &rarr;</a>'
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- schema
def ensure_content_schema():
    """Create Track B tables / columns idempotently."""
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS newsletter_subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            name TEXT NOT NULL,
            rating INTEGER NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bundles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            product_ids TEXT NOT NULL,   -- JSON list of product ids
            price_cents INTEGER NOT NULL,
            blurb TEXT NOT NULL
        )
    """)
    cols = [r[1] for r in
            con.execute("PRAGMA table_info(products)").fetchall()]
    if "sale_price_cents" not in cols:
        con.execute("ALTER TABLE products "
                    "ADD COLUMN sale_price_cents INTEGER")
    con.commit()
    con.close()


# ---------------------------------------------------------------- helpers
def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money(cents):
    return f"${cents / 100:,.2f}"


def admin_required():
    """Shared auth contract: backend sets session['admin_authed'] at login."""
    if not session.get("admin_authed"):
        abort(403)


# ---- newsletter ---------------------------------------------------------
def subscribe_email(email):
    """Idempotent signup: duplicates are a silent no-op (no email leak)."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False
    con = _connect()
    con.execute(
        "INSERT OR IGNORE INTO newsletter_subscribers (email, created_at)"
        " VALUES (?, ?)", (email, _now()))
    con.commit()
    con.close()
    return True


def list_subscribers():
    ensure_content_schema()
    con = _connect()
    rows = con.execute(
        "SELECT email, created_at FROM newsletter_subscribers"
        " ORDER BY id").fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---- sale pricing -------------------------------------------------------
def set_sale_price(pid, sale_price_cents):
    """Set (or clear with None) a product's sale price. NULL = not on sale."""
    ensure_content_schema()
    con = _connect()
    con.execute("UPDATE products SET sale_price_cents = ? WHERE id = ?",
                (sale_price_cents, pid))
    con.commit()
    con.close()


# ---- bundles ------------------------------------------------------------
def create_bundle(bid, name, slug, product_ids, price_cents, blurb):
    """Upsert a bundle (kit) grouping product ids with its own price."""
    ensure_content_schema()
    con = _connect()
    con.execute("""
        INSERT INTO bundles (id, name, slug, product_ids, price_cents, blurb)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, slug=excluded.slug,
          product_ids=excluded.product_ids, price_cents=excluded.price_cents,
          blurb=excluded.blurb
    """, (bid, name, slug, json.dumps(list(product_ids)), price_cents,
          blurb))
    con.commit()
    con.close()


def delete_bundle(bid):
    ensure_content_schema()
    con = _connect()
    con.execute("DELETE FROM bundles WHERE id = ?", (bid,))
    con.commit()
    con.close()


def _bundle_row_to_dict(r):
    d = dict(r)
    pids = json.loads(d["product_ids"])
    products = [p for pid in pids if (p := db.get_product(pid))]
    regular = sum(p["price_cents"] for p in products)
    d["products"] = products
    d["regular_total_cents"] = regular
    d["savings_cents"] = max(0, regular - d["price_cents"])
    return d


def get_bundle(slug):
    ensure_content_schema()
    con = _connect()
    r = con.execute("SELECT * FROM bundles WHERE slug = ?",
                    (slug,)).fetchone()
    con.close()
    return _bundle_row_to_dict(r) if r else None


def list_bundles():
    ensure_content_schema()
    con = _connect()
    rows = con.execute("SELECT * FROM bundles ORDER BY price_cents").fetchall()
    con.close()
    return [_bundle_row_to_dict(r) for r in rows]


# ---- reviews ------------------------------------------------------------
def submit_review(product_id, name, rating, body):
    name = (name or "").strip()[:80]
    body = (body or "").strip()[:2000]
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        rating = 0
    if not (db.get_product(product_id) and name and body
            and 1 <= rating <= 5):
        return None
    ensure_content_schema()
    con = _connect()
    cur = con.execute(
        "INSERT INTO reviews (product_id, name, rating, body, status,"
        " created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (product_id, name, rating, body, _now()))
    rid = cur.lastrowid
    con.commit()
    con.close()
    return rid


def approved_reviews(product_id):
    ensure_content_schema()
    con = _connect()
    rows = con.execute(
        "SELECT * FROM reviews WHERE product_id = ? AND status = 'approved'"
        " ORDER BY id DESC", (product_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def list_all_reviews():
    ensure_content_schema()
    con = _connect()
    rows = con.execute(
        "SELECT * FROM reviews ORDER BY"
        " CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def set_review_status(rid, status):
    ensure_content_schema()
    con = _connect()
    con.execute("UPDATE reviews SET status = ? WHERE id = ?",
                (status, rid))
    con.commit()
    con.close()


def delete_review(rid):
    ensure_content_schema()
    con = _connect()
    con.execute("DELETE FROM reviews WHERE id = ?", (rid,))
    con.commit()
    con.close()


# ---------------------------------------------------------------- guides
GUIDES = {
    "low-beam-bulb-swap": {
        "title": "Low Beam LED Bulb Swap",
        "difficulty": "Easy", "time": "20–30 min per side",
        "tools": ["Gloves or a clean cloth",
                  "Small flat screwdriver (some cars have access panels)"],
        "blurb": "The classic upgrade: trade tired halogens for crisp LED "
                 "low beams without cutting a single wire.",
        "steps": [
            "Park on a level surface, switch everything off, and let the old "
            "bulbs cool down — halogens run hot.",
            "Open the hood and find the back of the low beam housing. On "
            "some cars a plastic access cover or the airbox sits in front "
            "of it; move those aside first.",
            "Squeeze the locking tab and unplug the factory connector from "
            "the old bulb.",
            "Twist the old bulb about a quarter-turn counterclockwise and "
            "pull it straight out of the housing.",
            "Put on gloves (or use a clean cloth). Line up the LED bulb's "
            "locking tabs with the housing slots, push it in, and twist "
            "clockwise until it seats firmly.",
            "Check the bulb's rotation: the LED chips should face 3 and 9 "
            "o'clock (left and right) so the beam pattern matches what the "
            "housing was designed for.",
            "Plug the factory connector back in. If the LED doesn't light, "
            "flip the plug 180 degrees — LEDs are polarity-sensitive.",
            "Switch the low beams on and check the beam on a wall before "
            "closing the hood. Then repeat on the other side.",
        ],
        "images": {
            2: {"src": "img/guides/low-beam-bulb-swap/step-2.jpg",
                "alt": "Photo: moving the access cover aside to reach the back of the low beam housing"},
            3: {"src": "img/guides/low-beam-bulb-swap/step-3.jpg",
                "alt": "Photo: squeezing the locking tab to unplug the factory connector"},
            4: {"src": "img/guides/low-beam-bulb-swap/step-4.jpg",
                "alt": "Photo: twisting the old bulb counterclockwise and pulling it out"},
            5: {"src": "img/guides/low-beam-bulb-swap/step-5.jpg",
                "alt": "Photo: seating the new LED bulb in the low beam housing"},
            6: {"src": "img/guides/low-beam-bulb-swap/step-6.jpg",
                "alt": "Photo: LED chips facing 3 and 9 o'clock for the correct beam pattern"},
            7: {"src": "img/guides/low-beam-bulb-swap/step-7.jpg",
                "alt": "Photo: reconnecting the factory plug to the LED bulb"},
            8: {"src": "img/guides/low-beam-bulb-swap/step-8.jpg",
                "alt": "Photo: checking the low beam pattern on a wall"},
        },
        "safety": [
            "If your owner's manual calls for it — or you're unsure — "
            "disconnect the negative battery terminal first.",
            "Never touch the LED chips or lens with bare fingers; skin oils "
            "create hot spots and shorten bulb life.",
            "Confirm the bulb's fan or heatsink has room behind the housing "
            "and the dust cap still seals.",
            "Seeing flicker or a 'bulb out' warning? Your car likely needs "
            "a CANbus decoder — see Decoders & Accessories.",
        ],
    },
    "high-beam-bulb-swap": {
        "title": "High Beam LED Bulb Swap",
        "difficulty": "Easy", "time": "20–30 min per side",
        "tools": ["Gloves or a clean cloth",
                  "Small flat screwdriver (for access panels on some cars)"],
        "blurb": "Same plug-and-play process as the low beams, with one "
                 "extra check if your car uses the high beams as daytime "
                 "running lights.",
        "steps": [
            "Park level, switch everything off, and let the old bulbs cool.",
            "Open the hood and locate the high beam socket at the back of "
            "the housing — it's usually the inboard bulb.",
            "Unplug the factory connector, then twist the old bulb "
            "counterclockwise and pull it straight out.",
            "With gloves on, seat the LED bulb: tabs aligned, push in, "
            "quarter-turn clockwise until locked.",
            "Set the LED chips to face 3 and 9 o'clock for the correct "
            "beam pattern.",
            "Reconnect the plug (flip it 180° if the LED stays dark — "
            "polarity matters).",
            "Test the high beams, then test again with the daytime running "
            "lights if your car runs the high beams as DRLs.",
            "Repeat on the other side.",
        ],
        "images": {
            2: {"src": "img/guides/high-beam-bulb-swap/step-2.jpg",
                "alt": "Photo: locating the inboard high beam socket at the back of the housing"},
            3: {"src": "img/guides/high-beam-bulb-swap/step-3.jpg",
                "alt": "Photo: unplugging the connector and twisting the old bulb out"},
            4: {"src": "img/guides/high-beam-bulb-swap/step-4.jpg",
                "alt": "Photo: seating and locking the LED bulb in the high beam socket"},
            5: {"src": "img/guides/high-beam-bulb-swap/step-5.jpg",
                "alt": "Photo: LED chips at 3 and 9 o'clock for the correct beam pattern"},
            6: {"src": "img/guides/high-beam-bulb-swap/step-6.jpg",
                "alt": "Photo: reconnecting the plug to the LED bulb"},
            7: {"src": "img/guides/high-beam-bulb-swap/step-7.jpg",
                "alt": "Photo: testing the high beams and daytime running lights"},
        },
        "safety": [
            "Disconnect the negative battery terminal if your manual "
            "recommends it.",
            "Don't touch the LED chips with bare fingers.",
            "Check clearance for the fan/heatsink behind the housing and "
            "that the dust cap reseats.",
            "High beams wired as daytime running lights run at reduced "
            "voltage on some cars — LEDs may flicker or glow dim in DRL "
            "mode. A CANbus decoder usually fixes it.",
        ],
    },
    "fog-light-bulbs": {
        "title": "Fog Light LED Bulb Swap",
        "difficulty": "Easy–Moderate", "time": "15–20 min per side",
        "tools": ["Gloves or a clean cloth",
                  "Trim tool or flat screwdriver (for wheel-well clips)",
                  "Jack/stands only if your car needs the wheel removed"],
        "blurb": "Fog housings are low and tight, but the swap itself is "
                 "the same twist-and-plug routine — you just reach them "
                 "from below.",
        "steps": [
            "Turn the steering wheel fully toward the side you're working "
            "on — this opens up room in the wheel well.",
            "Peel back the wheel-well liner or pop the small access panel "
            "behind the bumper to reach the back of the fog housing.",
            "Unplug the factory connector from the old fog bulb.",
            "Twist the old bulb counterclockwise and pull it straight out "
            "of the housing.",
            "With gloves on, insert the LED bulb, align the tabs, and "
            "twist clockwise until it locks.",
            "Reconnect the plug (flip 180° if it doesn't light).",
            "Switch the fog lights on and confirm both sides match before "
            "clipping the liner back in place.",
        ],
        "images": {
            1: {"src": "img/guides/fog-light-bulbs/step-1.jpg",
                "alt": "Photo: front wheel turned fully aside to open room in the wheel well"},
            2: {"src": "img/guides/fog-light-bulbs/step-2.jpg",
                "alt": "Photo: peeling back the wheel-well liner to reach the back of the fog housing"},
            3: {"src": "img/guides/fog-light-bulbs/step-3.jpg",
                "alt": "Photo: unplugging the factory connector from the old fog bulb"},
            4: {"src": "img/guides/fog-light-bulbs/step-4.jpg",
                "alt": "Photo: twisting the old fog bulb counterclockwise and pulling it out"},
            5: {"src": "img/guides/fog-light-bulbs/step-5.jpg",
                "alt": "Photo: new LED bulb inserted and twisted clockwise to lock"},
            7: {"src": "img/guides/fog-light-bulbs/step-7.jpg",
                "alt": "Photo: both fog lights on with matching beams"},
        },
        "safety": [
            "Work with the engine off and the lights off; fog housings trap "
            "heat and road grime — gloves keep you clean and the chips "
            "oil-free.",
            "Never touch the LED chips or lens with bare fingers.",
            "Keep the heatsink clear of the liner and splash shields so it "
            "can vent; a pinched fan will cook the bulb.",
            "Flicker or a dash warning on some cars means you need a CANbus "
            "decoder.",
        ],
    },
    "turn-signal-bulbs": {
        "title": "Turn Signal LED Bulbs + Hyperflash Fix",
        "difficulty": "Moderate", "time": "30–45 min per side with resistors",
        "tools": ["Gloves or a clean cloth",
                  "Trim tool (for trunk liners / access panels)",
                  "Load resistor kit — one per turn-signal circuit"],
        "blurb": "LED turn signals blink fast ('hyperflash') because they "
                 "draw less power. Here's the bulb swap and the resistor "
                 "fix that calms the blink back down.",
        "steps": [
            "Reach the bulb: front signals are usually behind the housing "
            "under the hood or through the wheel well; rears come out "
            "through the trunk trim or by unbolting the taillight.",
            "Twist the bulb socket counterclockwise, pull it out, and "
            "remove the old bulb.",
            "With gloves on, push the LED bulb into the socket the same "
            "way the old one sat. If it doesn't light, pull it and rotate "
            "180° (polarity).",
            "Test the signal. If it blinks about twice as fast as normal, "
            "that's hyperflash — the car thinks a bulb is out because the "
            "LED draws so little current.",
            "Fix it with a load resistor: wire one resistor per "
            "turn-signal circuit, in parallel — one lead spliced to the "
            "signal-positive wire, the other to ground.",
            "Mount each resistor to bare metal, well away from plastic, "
            "wiring, and trim. Resistors get genuinely hot in use.",
            "Test the signals again — blink rate should be back to normal — "
            "then reassemble the trim.",
        ],
        "images": {
            1: {"src": "img/guides/turn-signal-bulbs/step-1.jpg",
                "alt": "Photo: reaching the turn signal housing under the hood"},
            2: {"src": "img/guides/turn-signal-bulbs/step-2.jpg",
                "alt": "Photo: twisting the turn signal socket counterclockwise and removing the old bulb"},
            3: {"src": "img/guides/turn-signal-bulbs/step-3.jpg",
                "alt": "Photo: pushing the new LED bulb into the socket"},
            4: {"src": "img/guides/turn-signal-bulbs/step-4.jpg",
                "alt": "Photo: testing the turn signal — fast hyperflash blink means the car needs a load fix"},
            5: {"src": "img/guides/turn-signal-bulbs/step-5.jpg",
                "alt": "Photo: wiring a load resistor in parallel to the signal wire and ground"},
            6: {"src": "img/guides/turn-signal-bulbs/step-6.jpg",
                "alt": "Photo: load resistor mounted to bare metal, away from plastic and wiring"},
            7: {"src": "img/guides/turn-signal-bulbs/step-7.jpg",
                "alt": "Photo: turn signal blinking at a normal steady rate"},
        },
        "safety": [
            "Disconnect the negative battery terminal before splicing "
            "anything.",
            "Resistors get hot enough to melt plastic — metal mounting "
            "surface only, never touching wires or trim.",
            "Don't touch LED chips with bare fingers.",
            "Many modern cars use CANbus monitoring instead of a flasher "
            "relay; a plug-and-play CANbus-ready bulb or decoder may fix "
            "hyperflash with no splicing at all.",
        ],
    },
    "brake-backup-bulbs": {
        "title": "Brake + Backup LED Bulb Swap",
        "difficulty": "Easy", "time": "15–25 min per side",
        "tools": ["Gloves or a clean cloth",
                  "Trim tool or socket set (for trunk trim / taillight bolts)"],
        "blurb": "LEDs light up a fraction of a second faster than "
                 "incandescents — real stopping-distance safety — and turn "
                 "your backup camera from grainy to clear.",
        "steps": [
            "Open the trunk or tailgate and pull back the trim panel "
            "behind the taillight, or unbolt the taillight from outside — "
            "varies by car.",
            "Twist the bulb socket counterclockwise and pull it out of "
            "the housing.",
            "Pull the old bulb straight out of the socket.",
            "With gloves on, push the LED replacement in. Use red for "
            "brake positions, white for reverse.",
            "Test before reassembling: brake pedal pressed (have a helper "
            "watch), then shift into reverse and check the backup camera "
            "view. If an LED stays dark, rotate it 180° — polarity.",
            "Reinstall the socket, trim, and bolts.",
        ],
        "images": {
            1: {"src": "img/guides/brake-backup-bulbs/step-1.jpg",
                "alt": "Photo: trunk trim panel pulled back to reveal the taillight bulb sockets"},
            2: {"src": "img/guides/brake-backup-bulbs/step-2.jpg",
                "alt": "Photo: twisting the taillight bulb socket counterclockwise and pulling it out"},
            3: {"src": "img/guides/brake-backup-bulbs/step-3.jpg",
                "alt": "Photo: pulling the old wedge bulb straight out of its socket"},
            4: {"src": "img/guides/brake-backup-bulbs/step-4.jpg",
                "alt": "Photo: red LED installed in the brake position and white LED in the reverse position"},
            5: {"src": "img/guides/brake-backup-bulbs/step-5.jpg",
                "alt": "Photo: testing brake and reverse lights with a helper watching from behind"},
        },
        "safety": [
            "Engine off, parking brake on — you'll be working around the "
            "back of the car with the ignition in accessory for testing.",
            "Don't touch the LED chips with bare fingers.",
            "Confirm the new bulb seats fully; a loose wedge bulb can "
            "flicker over bumps.",
            "Strobe-style brake bulbs flash before going solid — check "
            "your local laws before running them.",
            "A 'bulb out' warning on the dash usually means a CANbus "
            "decoder is needed for that position.",
        ],
    },
    "interior-dome-bulbs": {
        "title": "Interior & Dome LED Bulb Swap",
        "difficulty": "Easiest", "time": "5–10 min for the whole car",
        "tools": ["Plastic trim tool (a taped flat screwdriver works)",
                  "Gloves or a clean cloth"],
        "blurb": "The fastest transformation on this list: swap every "
                 "yellow interior bulb for clean white light in minutes.",
        "steps": [
            "Switch the dome/map lights off (or pull the interior-light "
            "fuse) so you're not working on a live socket.",
            "Pry the clear lens off with a plastic trim tool — work from "
            "one edge and go gently; lenses scratch easily.",
            "For wedge bulbs (T10/194/168): pull the old bulb straight out "
            "and push the LED in. For festoon bulbs: compress the spring "
            "clip, lift the old tube out, and drop the LED in — match the "
            "length (31, 36, or 42mm).",
            "Test each position as you go. Dead LED? Pull it and flip it "
            "180° — interior sockets are polarity-sensitive.",
            "Snap the lenses back on and do a final walk-around with all "
            "the doors open.",
        ],
        "images": {
            2: {"src": "img/guides/interior-dome-bulbs/step-2.jpg",
                "alt": "Photo: prying the dome light lens off with a plastic trim tool"},
            3: {"src": "img/guides/interior-dome-bulbs/step-3.jpg",
                "alt": "Photo: installing a wedge LED bulb in the dome socket"},
            4: {"src": "img/guides/interior-dome-bulbs/step-4.jpg",
                "alt": "Photo: testing the new LED dome light"},
            5: {"src": "img/guides/interior-dome-bulbs/step-5.jpg",
                "alt": "Photo: car interior glowing with new white dome and map lights"},
        },
        "safety": [
            "Use a plastic trim tool, not a bare screwdriver — headliner "
            "fabric and lenses mark easily.",
            "Don't touch LED chips with bare fingers.",
            "Match festoon length exactly; a too-long tube stresses the "
            "spring clips, a too-short one arcs.",
            "If a bulb stays lit dimly when switched off, that's residual "
            "current in the circuit — harmless, but a CANbus-friendly bulb "
            "usually cures it.",
        ],
    },
}

GUIDE_ORDER = [
    "low-beam-bulb-swap",
    "high-beam-bulb-swap",
    "fog-light-bulbs",
    "turn-signal-bulbs",
    "brake-backup-bulbs",
    "interior-dome-bulbs",
]


# ---------------------------------------------------------------- routes
@bp.route("/faq")
def faq():
    return render_template("faq.html")


@bp.route("/guides")
def guides_index():
    guides = [(slug, GUIDES[slug]) for slug in GUIDE_ORDER]
    return render_template("guides.html", guides=guides)


@bp.route("/guides/<slug>")
def guide_detail(slug):
    guide = GUIDES.get(slug)
    if not guide:
        abort(404)
    return render_template("guide.html", slug=slug, guide=guide,
                           guide_order=GUIDE_ORDER, guides=GUIDES)


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/terms")
def terms():
    return render_template("terms.html")


@bp.route("/newsletter", methods=["GET", "POST"])
def newsletter():
    if request.method == "POST":
        ok = subscribe_email(request.form.get("email", ""))
        nxt = request.form.get("next", "") or "/"
        if not nxt.startswith("/"):
            nxt = "/"
        sep = "&" if "?" in nxt else "?"
        return redirect(f"{nxt}{sep}nl={'ok' if ok else 'bad'}")
    return redirect("/")


@bp.route("/product/<pid>/review", methods=["POST"])
def review_submit(pid):
    rid = submit_review(pid,
                        request.form.get("name", ""),
                        request.form.get("rating", ""),
                        request.form.get("body", ""))
    target = f"/product/{pid}"
    if rid:
        # Owner alert — new review awaiting moderation. Email send never
        # blocks the customer's thank-you page.
        try:
            import emails  # local: emails imports db; keep it lazy
            product = db.get_product(pid)
            emails.notify_owner_review_submitted(
                product["name"] if product else pid,
                (request.form.get("name") or "").strip()[:80],
                request.form.get("rating", "?"),
                (request.form.get("body") or "").strip()[:2000])
        except Exception:  # noqa: BLE001 - review saved regardless
            pass
        return redirect(f"{target}?review=thanks")
    return redirect(f"{target}?review=error")


@bp.route("/admin/reviews")
def admin_reviews():
    admin_required()
    return render_template("admin_reviews.html",
                           reviews=list_all_reviews())


@bp.route("/admin/reviews/<int:rid>/approve", methods=["POST"])
def admin_review_approve(rid):
    admin_required()
    set_review_status(rid, "approved")
    return redirect(url_for("content.admin_reviews"))


@bp.route("/admin/reviews/<int:rid>/delete", methods=["POST"])
def admin_review_delete(rid):
    admin_required()
    delete_review(rid)
    return redirect(url_for("content.admin_reviews"))


@bp.route("/admin/newsletter.csv")
def admin_newsletter_csv():
    admin_required()
    subs = list_subscribers()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "subscribed_at"])
    for s in subs:
        w.writerow([s["email"], s["created_at"]])
    data = buf.getvalue()
    return (
        data,
        200,
        {"Content-Type": "text/csv",
         "Content-Disposition":
         "attachment; filename=newsletter_subscribers.csv"},
    )


def register_content_routes(app):
    """Wire the content blueprint into the app (called by the coordinator).

    Idempotent: safe to call twice (e.g. app.py wires it and a test wires
    it again) — the second call is a no-op.
    """
    if bp.name in app.blueprints:
        return
    ensure_content_schema()
    app.register_blueprint(bp)

    @app.context_processor
    def _content_globals():
        out = {"disclaimer_html": DISCLAIMER_HTML}
        try:
            out["content_bundles"] = list_bundles()
        except Exception:
            out["content_bundles"] = []
        out["review_ctx"] = None
        va = getattr(request, "view_args", None) or {}
        pid = va.get("pid")
        if pid and request.path.startswith("/product/"):
            try:
                revs = approved_reviews(pid)
            except Exception:
                revs = []
            avg = (sum(r["rating"] for r in revs) / len(revs)
                   if revs else 0)
            out["review_ctx"] = {"reviews": revs,
                                 "count": len(revs),
                                 "avg": round(avg, 1)}
        return out
