"""SQLite layer for the product catalog.

Products are seeded from catalog.py on startup (idempotent). The cart lives
in the Flask session; prices are always re-looked-up here at checkout time —
never trusted from the client.

Phase 2 additions: orders table (new -> paid -> shipped -> cancelled),
inventory table (stock per product, seeded with clearly-marked PLACEHOLDER
defaults the owner can edit in /admin), and sales-summary helpers.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from catalog import PRODUCTS, CATEGORIES
from fitment_loader import norm_size

DB_PATH = Path(os.environ.get(
    "STORE_DB",
    Path(__file__).resolve().parent / "data" / "store.db",
))


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- products
#
# Products live in the DB (seeded once from catalog.py). The admin product
# manager in /admin owns them after that: restarts never overwrite.
#
# Per-variation model: each product has rows in product_variations, one per
# option combination (Size x Color temp, ...). A variation may override price,
# compare-at, cost, SKU/MSKU/barcode, image, and inventory. NULL price =
# fall back to the product-level price.

PRODUCT_STATUSES = ("active", "draft", "archived")


def init_db():
    con = _connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            tier TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            warranty TEXT NOT NULL,
            sizes TEXT NOT NULL,        -- JSON list
            color_temps TEXT NOT NULL,  -- JSON list
            badge TEXT,
            blurb TEXT NOT NULL,
            features TEXT NOT NULL      -- JSON list
        )
    """)
    # Columns added idempotently for existing DBs (all nullable except
    # draft/taxable which carry defaults).
    _PRODUCT_COLUMNS = (
        ("draft", "ADD COLUMN draft INTEGER NOT NULL DEFAULT 0"),
        ("sale_price_cents", "ADD COLUMN sale_price_cents INTEGER"),
        ("variants", "ADD COLUMN variants TEXT"),              # JSON list of {name, values}
        ("image_paths", "ADD COLUMN image_paths TEXT"),        # legacy; superseded by images
        ("images", "ADD COLUMN images TEXT"),                # JSON list of {src, primary}
        ("fitment_positions", "ADD COLUMN fitment_positions TEXT"),  # JSON list
        ("cost_cents", "ADD COLUMN cost_cents INTEGER"),       # internal: never public
        ("supplier_name", "ADD COLUMN supplier_name TEXT"),    # internal
        ("supplier_sku", "ADD COLUMN supplier_sku TEXT"),      # internal
        ("supplier_notes", "ADD COLUMN supplier_notes TEXT"),  # internal
        ("sku", "ADD COLUMN sku TEXT"),                        # product SKU, unique, optional
        ("weight_oz", "ADD COLUMN weight_oz REAL"),            # internal for now
        ("description", "ADD COLUMN description TEXT"),        # full description (public)
        ("product_type", "ADD COLUMN product_type TEXT"),
        ("status", "ADD COLUMN status TEXT"),                  # active/draft/archived
        ("tags", "ADD COLUMN tags TEXT"),                      # JSON list, admin search
        ("vendor", "ADD COLUMN vendor TEXT"),                  # internal
        ("msku", "ADD COLUMN msku TEXT"),                      # manufacturer SKU, internal
        ("barcode", "ADD COLUMN barcode TEXT"),                # internal
        ("taxable", "ADD COLUMN taxable INTEGER NOT NULL DEFAULT 1"),
        ("length_in", "ADD COLUMN length_in REAL"),
        ("width_in", "ADD COLUMN width_in REAL"),
        ("height_in", "ADD COLUMN height_in REAL"),
        ("country_of_origin", "ADD COLUMN country_of_origin TEXT"),
        ("hs_code", "ADD COLUMN hs_code TEXT"),
        ("seo_title", "ADD COLUMN seo_title TEXT"),
        ("seo_description", "ADD COLUMN seo_description TEXT"),
        ("notes", "ADD COLUMN notes TEXT"),                    # internal admin notes
        ("square_catalog_object_id",                            # Square item object id
         "ADD COLUMN square_catalog_object_id TEXT"),
        ("square_last_synced_at",                               # last Square sync
         "ADD COLUMN square_last_synced_at TEXT"),
    )
    cols = [r[1] for r in
            con.execute("PRAGMA table_info(products)").fetchall()]
    for col_name, ddl in _PRODUCT_COLUMNS:
        if col_name not in cols:
            con.execute(f"ALTER TABLE products {ddl}")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sku "
                "ON products(sku)")
    # status supersedes the old draft flag; migrate once, keep both synced.
    for r in con.execute(
            "SELECT id, draft FROM products "
            "WHERE status IS NULL").fetchall():
        st = "draft" if r["draft"] else "active"
        con.execute("UPDATE products SET status = ? WHERE id = ?",
                    (st, r["id"]))
    con.execute("UPDATE products SET status = 'active' "
                "WHERE status IS NULL OR status NOT IN "
                "('active', 'draft', 'archived')")
    # ---- per-variation table ----
    con.execute("""
        CREATE TABLE IF NOT EXISTS product_variations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            option_values TEXT NOT NULL,   -- JSON dict {"Size": "H11", ...}
            sku TEXT,
            msku TEXT,
            barcode TEXT,
            price_cents INTEGER,           -- NULL = product-level price
            compare_at_cents INTEGER,      -- NULL = no compare-at
            cost_cents INTEGER,            -- internal
            inventory_qty INTEGER NOT NULL DEFAULT 0,
            track_inventory INTEGER NOT NULL DEFAULT 1,
            allow_oversell INTEGER NOT NULL DEFAULT 0,
            low_threshold INTEGER NOT NULL DEFAULT 5,
            image_src TEXT                 -- optional per-variation image
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_variation_sku "
                "ON product_variations(sku)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_variation_product "
                "ON product_variations(product_id)")
    # Idempotent Square columns for existing DBs.
    _VAR_SQUARE_COLUMNS = (
        ("square_catalog_object_id",
         "ADD COLUMN square_catalog_object_id TEXT"),  # Square variation id
        ("square_last_synced_at",
         "ADD COLUMN square_last_synced_at TEXT"),
    )
    vcols = [r[1] for r in
             con.execute("PRAGMA table_info(product_variations)").fetchall()]
    for col_name, ddl in _VAR_SQUARE_COLUMNS:
        if col_name not in vcols:
            con.execute(f"ALTER TABLE product_variations {ddl}")
    con.execute("CREATE INDEX IF NOT EXISTS idx_variation_square_id "
                "ON product_variations(square_catalog_object_id)")
    # Seed from catalog.py: INSERT new rows only, never overwrite. The admin
    # product manager owns existing rows — a restart must not clobber the
    # owner's edits.
    for p in PRODUCTS:
        groups = _seed_variant_groups(p)
        cur = con.execute("""
            INSERT OR IGNORE INTO products
              (id, name, category, tier, price_cents, warranty,
               sizes, color_temps, badge, blurb, features, draft, status,
               variants, image_paths, fitment_positions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p["id"], p["name"], p["category"], p["tier"], p["price_cents"],
            p["warranty"], json.dumps(p["sizes"]),
            json.dumps(p["color_temps"]), p.get("badge"),
            p["blurb"], json.dumps(p["features"]),
            int(bool(p.get("draft", False))),
            "draft" if p.get("draft") else "active",
            json.dumps(groups), json.dumps([]), json.dumps([]),
        ))
        if cur.rowcount:
            _seed_variations_for(con, p["id"], groups, track_inventory=0)
    # Migrate rows seeded before the variants model existed.
    for r in con.execute(
            "SELECT id, sizes, color_temps FROM products "
            "WHERE variants IS NULL").fetchall():
        sizes = json.loads(r["sizes"]) if r["sizes"] else []
        temps = json.loads(r["color_temps"]) if r["color_temps"] else []
        groups = []
        if sizes:
            groups.append({"name": "Size", "values": sizes})
        if temps:
            groups.append({"name": "Color temp", "values": temps})
        con.execute("UPDATE products SET variants = ? WHERE id = ?",
                    (json.dumps(groups), r["id"]))
    # Migrate products that predate the variations table: one row per option
    # combination, inventory NOT tracked (product-level inventory keeps
    # working exactly as before).
    for (pid,) in con.execute("""
            SELECT p.id FROM products p
            WHERE NOT EXISTS (SELECT 1 FROM product_variations v
                              WHERE v.product_id = p.id)
        """).fetchall():
        r = con.execute("SELECT variants FROM products WHERE id = ?",
                        (pid,)).fetchone()
        groups = json.loads(r["variants"]) if r and r["variants"] else []
        _seed_variations_for(con, pid, groups, track_inventory=0)
    # Reorder stored option_values to follow the product's group order
    # (older rows were stored alphabetically-sorted).
    for (pid,) in con.execute("SELECT id FROM products").fetchall():
        r = con.execute("SELECT variants FROM products WHERE id = ?",
                        (pid,)).fetchone()
        groups = json.loads(r["variants"]) if r and r["variants"] else []
        for v in con.execute(
                "SELECT id, option_values FROM product_variations"
                " WHERE product_id = ?", (pid,)).fetchall():
            ov = json.loads(v["option_values"]) if v["option_values"] else {}
            fixed = _ordered_option_values(groups, ov)
            if list(fixed.keys()) != list(ov.keys()):
                con.execute("UPDATE product_variations SET option_values = ?"
                            " WHERE id = ?",
                            (json.dumps(fixed), v["id"]))
    # --- Phase 2 tables (never delete/reseed existing data) ---
    con.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',   -- new/paid/shipped/cancelled
            customer_name TEXT,
            customer_email TEXT,
            addr_line1 TEXT,
            addr_line2 TEXT,
            addr_city TEXT,
            addr_state TEXT,
            addr_zip TEXT,
            line_items TEXT NOT NULL,              -- JSON snapshot of the cart
            subtotal_cents INTEGER NOT NULL,
            shipping_cents INTEGER NOT NULL DEFAULT 0,
            total_cents INTEGER NOT NULL,
            stripe_session_id TEXT UNIQUE,
            stripe_payment_intent_id TEXT,
            tracking_number TEXT,
            inventory_applied INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        )
    """)
    # Track 3 promo snapshot columns (older DBs lack them — add once).
    ocols = [r[1] for r in
             con.execute("PRAGMA table_info(orders)").fetchall()]
    if "promo_code" not in ocols:
        con.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")
    if "discount_cents" not in ocols:
        con.execute("ALTER TABLE orders ADD COLUMN discount_cents "
                    "INTEGER NOT NULL DEFAULT 0")
    if "refunded_cents" not in ocols:
        con.execute("ALTER TABLE orders ADD COLUMN refunded_cents "
                    "INTEGER NOT NULL DEFAULT 0")
    init_settings(con)
    init_order_events(con)
    init_square_sync_log(con)
    con.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            product_id TEXT PRIMARY KEY,
            stock INTEGER NOT NULL DEFAULT 0,
            low_threshold INTEGER NOT NULL DEFAULT 5,
            updated_at TEXT NOT NULL
        )
    """)
    # Seed inventory with PLACEHOLDER defaults for products that have no row
    # yet. The owner edits these in /admin -> Inventory (or inline on the
    # product form). Existing rows are never touched (INSERT OR IGNORE).
    for (pid,) in con.execute("SELECT id FROM products").fetchall():
        con.execute("""
            INSERT OR IGNORE INTO inventory
              (product_id, stock, low_threshold, updated_at)
            VALUES (?, ?, ?, ?)
        """, (pid, PLACEHOLDER_DEFAULT_STOCK,
              PLACEHOLDER_LOW_THRESHOLD, _utcnow()))
    con.commit()
    con.close()


# PLACEHOLDER inventory defaults — the owner edits real counts in /admin.
PLACEHOLDER_DEFAULT_STOCK = 25
PLACEHOLDER_LOW_THRESHOLD = 5


def _seed_variant_groups(p):
    """Build the generic variant groups for a catalog.py seed product."""
    groups = []
    if p.get("sizes"):
        groups.append({"name": "Size", "values": list(p["sizes"])})
    if p.get("color_temps"):
        groups.append({"name": "Color temp", "values": list(p["color_temps"])})
    return groups


def _group_values(groups, *names):
    """Values of the first variant group whose name matches (case-insensitive,
    ignoring spaces/underscores). Legacy accessor for sizes/color_temps."""
    want = {re.sub(r"[\s_]+", "", n).lower() for n in names}
    for g in groups or []:
        if re.sub(r"[\s_]+", "", g.get("name", "")).lower() in want:
            return list(g.get("values", []))
    return []


def primary_image_src(p):
    """First-choice image for cards: primary upload/URL, else first image,
    else the category placeholder SVG."""
    images = p.get("images") or []
    for im in images:
        if im.get("primary") and im.get("src"):
            return im["src"]
    for im in images:
        if im.get("src"):
            return im["src"]
    icon = CATEGORIES.get(p.get("category"), {}).get("icon", "bulb")
    return f"/static/img/ph-{icon}.svg"


def _row_to_product(r, with_variations=True):
    d = dict(r)
    d["sizes"] = json.loads(d["sizes"]) if d.get("sizes") else []
    d["color_temps"] = (json.loads(d["color_temps"])
                        if d.get("color_temps") else [])
    d["features"] = json.loads(d["features"]) if d.get("features") else []
    groups = json.loads(d["variants"]) if d.get("variants") else None
    if not groups:
        # Last-resort fallback for rows predating the variants column.
        groups = []
        if d["sizes"]:
            groups.append({"name": "Size", "values": d["sizes"]})
        if d["color_temps"]:
            groups.append({"name": "Color temp", "values": d["color_temps"]})
    d["variant_groups"] = groups
    # Legacy aliases stay in sync with the generic model.
    d["sizes"] = _group_values(groups, "size")
    d["color_temps"] = _group_values(groups, "color temp", "color_temp",
                                     "color")
    imgs = d.get("images") or d.get("image_paths")
    d["images"] = json.loads(imgs) if imgs else []
    d["fitment_positions"] = (json.loads(d["fitment_positions"])
                              if d.get("fitment_positions") else [])
    d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
    d["price"] = d["price_cents"] / 100
    d["status"] = d.get("status") or ("draft" if d.get("draft") else "active")
    d["draft"] = d["status"] != "active"
    d["taxable"] = bool(d.get("taxable", 1))
    d["primary_image"] = primary_image_src(d)
    # SEO falls back to name/blurb when blank.
    d["seo_title"] = d.get("seo_title") or d["name"]
    d["seo_description"] = d.get("seo_description") or d.get("blurb", "")
    if with_variations:
        d["variations"] = list_variations(d["id"], d)
    else:
        d["variations"] = []
    d["default_variation"] = d["variations"][0] if d["variations"] else None
    d["margin"] = margin_for(d["price_cents"], d.get("cost_cents"))
    return d


# Fields that must never appear on public pages, in public templates, or in
# any JSON response. Admin-only.
INTERNAL_FIELDS = frozenset({
    "cost_cents", "supplier_name", "supplier_sku", "supplier_notes",
    "weight_oz", "length_in", "width_in", "height_in",
    "country_of_origin", "hs_code", "notes",
    "vendor", "msku", "barcode", "taxable", "tags", "sku",
    "square_catalog_object_id", "square_last_synced_at",
})

# Internal-only fields on a variation row.
VARIATION_INTERNAL_FIELDS = frozenset({
    "cost_cents", "msku", "barcode", "sku", "inventory_qty",
    "track_inventory", "allow_oversell", "low_threshold",
    "square_catalog_object_id", "square_last_synced_at",
})


def public_product(p):
    """Copy of a product dict with internal-only fields stripped, including
    per-variation internals. Use before handing a product to any public
    template context that could be serialized, or to JSON."""
    out = {k: v for k, v in p.items() if k not in INTERNAL_FIELDS}
    out["variations"] = [public_variation(v)
                         for v in p.get("variations", [])]
    if out.get("default_variation"):
        out["default_variation"] = public_variation(out["default_variation"])
    return out


def public_variation(v):
    """Public-safe variation: id, options, effective prices, image, a sale
    flag, and a simple availability flag. No cost/SKU/inventory internals."""
    if not v:
        return None
    price = v["effective_price_cents"]
    compare = v["effective_compare_at_cents"]
    return {
        "id": v["id"],
        "option_values": v["option_values"],
        "price_cents": price,
        "compare_at_cents": compare,
        "on_sale": compare is not None and compare > price,
        "image_src": v.get("image_src"),
        "available": bool(v.get("available", True)),
    }


def margin_for(price_cents, cost_cents):
    """Margin readout: dollars and percent, or None when cost unknown."""
    if price_cents is None or cost_cents is None or price_cents <= 0:
        return None
    dollars = (price_cents - cost_cents) / 100
    return {"dollars": dollars,
            "pct": round((price_cents - cost_cents) / price_cents * 100, 1)}


def get_product_by_sku(sku):
    """Case-insensitive product lookup by SKU. None when blank/unknown."""
    sku = (sku or "").strip()
    if not sku:
        return None
    con = _connect()
    r = con.execute("SELECT * FROM products WHERE lower(sku) = lower(?)",
                    (sku,)).fetchone()
    con.close()
    return _row_to_product(r) if r else None


def get_variation_by_sku(sku):
    """Global variation lookup by SKU (case-insensitive). Returns the
    variation dict, or None."""
    sku = (sku or "").strip()
    if not sku:
        return None
    con = _connect()
    r = con.execute("SELECT * FROM product_variations"
                    " WHERE lower(sku) = lower(?)", (sku,)).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    prod = get_product(d["product_id"], with_variations=False)
    return _row_to_variation(r, prod)


def find_variation(pid, option_values):
    """Match a variation of product pid by its option-values key
    (order-independent). None when no match."""
    key = _variation_key(option_values)
    for v in list_variations(pid):
        if _variation_key(v["option_values"]) == key:
            return v
    return None


_VARIATION_WRITABLE = (
    "sku", "msku", "barcode", "price_cents", "compare_at_cents",
    "cost_cents", "inventory_qty", "track_inventory", "allow_oversell",
    "low_threshold", "image_src",
)


def create_variation(pid, option_values, fields=None):
    """Insert one variation row for pid. fields may carry any of the
    _VARIATION_WRITABLE keys. Returns the new variation id."""
    fields = dict(fields or {})
    groups = (get_product(pid, with_variations=False) or {}).get(
        "variant_groups") or []
    stored = json.dumps(_ordered_option_values(groups, option_values))
    con = _connect()
    pos = con.execute("SELECT COALESCE(MAX(position), -1) + 1"
                      " FROM product_variations WHERE product_id = ?",
                      (pid,)).fetchone()[0]
    cur = con.execute("""
        INSERT INTO product_variations
          (product_id, position, option_values, sku, msku, barcode,
           price_cents, compare_at_cents, cost_cents, inventory_qty,
           track_inventory, allow_oversell, low_threshold, image_src)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (pid, pos, stored,
          fields.get("sku") or None,
          fields.get("msku") or None,
          fields.get("barcode") or None,
          fields.get("price_cents"),
          fields.get("compare_at_cents"),
          fields.get("cost_cents"),
          max(0, int(fields.get("inventory_qty", 0) or 0)),
          1 if fields.get("track_inventory", True) else 0,
          1 if fields.get("allow_oversell") else 0,
          max(0, int(fields.get("low_threshold",
                               PLACEHOLDER_LOW_THRESHOLD) or 0)),
          fields.get("image_src") or None))
    con.commit()
    vid = cur.lastrowid
    con.close()
    return vid


def update_variation_fields(vid, fields):
    """Partial update of a variation: only the supplied _VARIATION_WRITABLE
    keys are touched. Returns True when the row existed."""
    sets, params = [], []
    for key in _VARIATION_WRITABLE:
        if key in fields:
            sets.append(f"{key} = ?")
            params.append(fields[key])
    if not sets:
        return False
    con = _connect()
    cur = con.execute(
        f"UPDATE product_variations SET {', '.join(sets)} WHERE id = ?",
        (*params, vid))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    return changed


def delete_variation(vid):
    """Remove one variation row (used by the CSV importer to drop the
    placeholder seeded on product create when the CSV defines real
    variations)."""
    con = _connect()
    con.execute("DELETE FROM product_variations WHERE id = ?", (vid,))
    con.commit()
    con.close()


def set_product_variant_groups(pid, groups):
    """Replace a product's option-group definition and re-order every
    existing variation's option_values to the new group order (unknown keys
    are kept, appended). Used by CSV import when new option groups appear."""
    con = _connect()
    con.execute("UPDATE products SET variants = ? WHERE id = ?",
                (json.dumps(groups), pid))
    for v in con.execute("SELECT id, option_values FROM product_variations"
                         " WHERE product_id = ?", (pid,)).fetchall():
        ov = json.loads(v["option_values"]) if v["option_values"] else {}
        con.execute("UPDATE product_variations SET option_values = ?"
                    " WHERE id = ?",
                    (json.dumps(_ordered_option_values(groups, ov)), v["id"]))
    con.commit()
    con.close()


# ------------------------------------------------------------ variations
def _variation_matrix(groups):
    """Cartesian product of option groups -> list of option dicts."""
    import itertools
    names = [g["name"] for g in groups or [] if g.get("values")]
    if not names:
        return [{}]
    vals = [g["values"] for g in groups if g.get("values")]
    return [dict(zip(names, combo)) for combo in itertools.product(*vals)]


def _variation_key(option_values):
    """Canonical match key (order-independent)."""
    return json.dumps(option_values or {}, sort_keys=True)


def _ordered_option_values(groups, option_values):
    """option_values dict ordered by the product's group order (nice labels),
    with any unknown keys appended."""
    order = [g["name"] for g in groups or [] if g.get("name")]
    ov = dict(option_values or {})
    out = {n: ov[n] for n in order if n in ov}
    for k, v in ov.items():
        if k not in out:
            out[k] = v
    return out


def _seed_variations_for(con, pid, groups, track_inventory=1):
    """Insert one variation row per option combination (used by seed and
    migration). Returns the inserted row ids. Commits on the given
    connection."""
    rows = []
    for pos, opts in enumerate(_variation_matrix(groups)):
        cur = con.execute("""
            INSERT INTO product_variations
              (product_id, position, option_values, inventory_qty,
               track_inventory, allow_oversell, low_threshold)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (pid, pos, json.dumps(_ordered_option_values(groups, opts)), 0,
              track_inventory, PLACEHOLDER_LOW_THRESHOLD))
        rows.append(cur.lastrowid)
    con.commit()
    return rows


def _row_to_variation(r, product=None):
    d = dict(r)
    d["option_values"] = (json.loads(d["option_values"])
                          if d.get("option_values") else {})
    d["track_inventory"] = bool(d.get("track_inventory", 1))
    d["allow_oversell"] = bool(d.get("allow_oversell", 0))
    d["effective_price_cents"] = (
        d["price_cents"] if d["price_cents"] is not None
        else (product.get("sale_price_cents") or product["price_cents"])
        if product else d["price_cents"])
    if d["compare_at_cents"] is not None:
        d["effective_compare_at_cents"] = d["compare_at_cents"]
    elif product and (d["price_cents"] is not None
                      or product.get("sale_price_cents")):
        d["effective_compare_at_cents"] = product["price_cents"]
    else:
        d["effective_compare_at_cents"] = None
    d["on_sale"] = (d["effective_compare_at_cents"] is not None
                    and d["effective_compare_at_cents"]
                    > d["effective_price_cents"])
    d["margin"] = margin_for(d["effective_price_cents"], d.get("cost_cents"))
    d["low_stock"] = (d["track_inventory"]
                      and d["inventory_qty"] <= d["low_threshold"])
    d["available"] = (not d["track_inventory"] or d["allow_oversell"]
                      or d["inventory_qty"] > 0)
    d["label"] = " / ".join(str(v) for v in d["option_values"].values())
    return d


def get_variation(vid):
    con = _connect()
    r = con.execute("SELECT * FROM product_variations WHERE id = ?",
                    (vid,)).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    prod = get_product(d["product_id"], with_variations=False)
    return _row_to_variation(r, prod)


def list_variations(pid, product=None):
    con = _connect()
    rows = con.execute(
        "SELECT * FROM product_variations WHERE product_id = ?"
        " ORDER BY position, id", (pid,)).fetchall()
    con.close()
    if product is None:
        product = get_product(pid, with_variations=False)
    return [_row_to_variation(r, product) for r in rows]


def sync_variations(pid, groups, overrides=None, new_track_default=1):
    """Rebuild the variation matrix for new option groups, preserving
    per-variation overrides matched by option values. overrides: list of
    dicts with an 'option_values' key plus override fields. Rows that fall
    out of the matrix are deleted."""
    overrides = overrides or []
    by_key = {_variation_key(o.get("option_values")): o for o in overrides}
    matrix = _variation_matrix(groups)
    con = _connect()
    existing = {}
    for r in con.execute(
            "SELECT * FROM product_variations WHERE product_id = ?",
            (pid,)).fetchall():
        e = dict(r)
        # Canonical key: stored JSON is group-ordered, the matrix key is
        # sorted — both must map to the same row.
        existing[_variation_key(json.loads(e["option_values"]))] = e
    want_keys = set()
    for pos, opts in enumerate(matrix):
        key = _variation_key(opts)
        want_keys.add(key)
        stored = json.dumps(_ordered_option_values(groups, opts))
        o = by_key.get(key, {})
        fields = {
            "sku": o.get("sku") or None,
            "msku": o.get("msku") or None,
            "barcode": o.get("barcode") or None,
            "price_cents": o.get("price_cents"),
            "compare_at_cents": o.get("compare_at_cents"),
            "cost_cents": o.get("cost_cents"),
            "inventory_qty": max(0, int(o.get("inventory_qty", 0) or 0)),
            "track_inventory": 1 if o.get("track_inventory", True) else 0,
            "allow_oversell": 1 if o.get("allow_oversell") else 0,
            "low_threshold": max(0, int(o.get("low_threshold",
                                             PLACEHOLDER_LOW_THRESHOLD) or 0)),
            "image_src": o.get("image_src") or None,
        }
        if key in existing:
            e = existing[key]
            # Preserve stored overrides unless the form posted new ones for
            # this exact row (posted rows carry _posted=True).
            if not o.get("_posted"):
                fields = {k: e[k] for k in fields}
                fields["inventory_qty"] = e["inventory_qty"]
            con.execute("""
                UPDATE product_variations SET position = ?, option_values = ?,
                  sku = ?, msku = ?, barcode = ?, price_cents = ?,
                  compare_at_cents = ?, cost_cents = ?, inventory_qty = ?,
                  track_inventory = ?, allow_oversell = ?,
                  low_threshold = ?, image_src = ?
                WHERE id = ?
            """, (pos, stored, fields["sku"], fields["msku"], fields["barcode"],
                  fields["price_cents"], fields["compare_at_cents"],
                  fields["cost_cents"], fields["inventory_qty"],
                  fields["track_inventory"], fields["allow_oversell"],
                  fields["low_threshold"], fields["image_src"],
                  e["id"]))
        else:
            con.execute("""
                INSERT INTO product_variations
                  (product_id, position, option_values, sku, msku, barcode,
                   price_cents, compare_at_cents, cost_cents, inventory_qty,
                   track_inventory, allow_oversell, low_threshold, image_src)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pid, pos, stored, fields["sku"], fields["msku"],
                  fields["barcode"], fields["price_cents"],
                  fields["compare_at_cents"], fields["cost_cents"],
                  fields["inventory_qty"], new_track_default
                  if "_posted" not in o else fields["track_inventory"],
                  fields["allow_oversell"], fields["low_threshold"],
                  fields["image_src"]))
    for key, e in existing.items():
        if key not in want_keys:
            con.execute("DELETE FROM product_variations WHERE id = ?",
                        (e["id"],))
    con.commit()
    con.close()


def variation_sku_taken(sku, exclude_vid=None):
    """True if any other variation OR any product already uses this SKU
    (case-insensitive). Product and variation SKUs share one namespace."""
    if not sku:
        return False
    sku = sku.strip()
    con = _connect()
    r = con.execute(
        "SELECT id FROM product_variations WHERE lower(sku) = lower(?)",
        (sku,)).fetchone()
    if r and r["id"] != exclude_vid:
        con.close()
        return True
    r = con.execute(
        "SELECT 1 FROM products WHERE lower(sku) = lower(?)",
        (sku,)).fetchone()
    con.close()
    return bool(r)


def set_variation_stock(vid, qty, allow_oversell=False):
    con = _connect()
    if allow_oversell:
        con.execute("UPDATE product_variations SET inventory_qty = ?"
                    " WHERE id = ?", (int(qty), vid))
    else:
        con.execute("UPDATE product_variations SET inventory_qty = CASE"
                    " WHEN ? < 0 THEN 0 ELSE ? END WHERE id = ?",
                    (int(qty), int(qty), vid))
    con.commit()
    con.close()


def decrement_variation_stock(vid, qty):
    """Decrement a tracked variation's inventory (clamped at 0 unless the
    variation allows oversell). Returns the new quantity."""
    v = get_variation(vid)
    if not v or not v["track_inventory"]:
        return None
    new_qty = v["inventory_qty"] - max(0, int(qty))
    if not v["allow_oversell"]:
        new_qty = max(0, new_qty)
    set_variation_stock(vid, new_qty, allow_oversell=True)
    return new_qty


def resolve_cart_variation(product, item):
    """Pick the variation for a cart line. Prefers an explicit variation_id
    (validated against the product); falls back to legacy size/color_temp
    matching, then to the default variation."""
    variations = product.get("variations") or []
    if not variations:
        return None
    vid = item.get("variation_id")
    if vid is not None:
        for v in variations:
            if str(v["id"]) == str(vid):
                return v
    # Legacy session items stored size/color_temp instead of variation_id.
    size, temp = item.get("size", ""), item.get("color_temp", "")
    if size or temp:
        for v in variations:
            ov = v["option_values"]
            if size and ov.get("Size") != size:
                continue
            if temp and ov.get("Color temp") != temp:
                continue
            return v
    return variations[0]


def variation_sell_price(product, variation):
    """Server-side price for a cart line: variation override, else the
    product's effective price (sale when set). Never trust the client."""
    if variation and variation.get("price_cents") is not None:
        return variation["price_cents"]
    return product.get("sale_price_cents") or product["price_cents"]


def variation_price_range(product):
    """(min, max) of effective sell prices across a product's variations,
    or None when the product has no variations. Accepts full internal
    variation dicts or public_variation dicts (whose price_cents is the
    effective price)."""
    prices = [v["effective_price_cents"] if "effective_price_cents" in v
              else v["price_cents"]
              for v in product.get("variations", [])]
    if not prices:
        return None
    return (min(prices), max(prices))


def slugify_product_id(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "product"


# ------------------------------------------------- shared import/form guards
#
# Standing store rules, reused by the admin product form and the CSV
# importer: every product name is Bravo-prefixed; the word 'headlight'
# appears nowhere customer-facing; 'off-road' never appears in names.


def clean_product_name(raw, errors):
    """Brand-prefix + name guards. Returns the cleaned name (possibly with
    errors appended)."""
    name = (raw or "").strip()
    if not name:
        errors.append("Name is required.")
        return ""
    if not name.lower().startswith("bravo "):
        name = "Bravo " + name  # auto-prefix the brand
    low = name.lower()
    if "headlight" in low:
        errors.append("Product names may not contain the word 'headlight'.")
    if "off-road" in low or "offroad" in low:
        errors.append("Product names may not contain 'off-road' "
                      "(it is fine in the description).")
    return name


def guard_public_copy(data, errors):
    """The word 'headlight' appears nowhere customer-facing
    (the /dot-compliance page is the intentional exception)."""
    for field in ("blurb", "description", "seo_title", "seo_description"):
        if "headlight" in (data.get(field) or "").lower():
            errors.append(f"{field} may not contain the word 'headlight'.")
    for feat in data.get("features") or []:
        if "headlight" in feat.lower():
            errors.append("Features may not contain the word 'headlight'.")
            break


def unique_product_id(base):
    """Slugify and de-duplicate: base, base-2, base-3, ..."""
    base = slugify_product_id(base)
    pid, n = base, 2
    while get_product(pid) is not None:
        pid = f"{base}-{n}"
        n += 1
    return pid


def sku_taken(sku, exclude_pid=None):
    """True if another product OR any variation already uses this SKU
    (case-insensitive). Product and variation SKUs share one namespace."""
    if not sku:
        return False
    sku = sku.strip()
    con = _connect()
    r = con.execute(
        "SELECT id FROM products WHERE lower(sku) = lower(?)",
        (sku,)).fetchone()
    if r and r["id"] != exclude_pid:
        con.close()
        return True
    r = con.execute(
        "SELECT 1 FROM product_variations WHERE lower(sku) = lower(?)",
        (sku,)).fetchone()
    con.close()
    return bool(r)


def create_product(data):
    """Insert a new product row (+ placeholder inventory row). data keys:
    id, name, description, product_type, status, category, tier, tags (list),
    price_cents, sale_price_cents, cost_cents, warranty, badge, blurb,
    features (list), variant_groups (list), images (list),
    fitment_positions (list), supplier_name, supplier_sku, supplier_notes,
    notes, sku, msku, barcode, vendor, taxable (bool), weight_oz, length_in,
    width_in, height_in, country_of_origin, hs_code, seo_title,
    seo_description."""
    groups = data.get("variant_groups") or []
    status = data.get("status") or "draft"
    if status not in PRODUCT_STATUSES:
        status = "draft"
    con = _connect()
    con.execute("""
        INSERT INTO products
          (id, name, description, product_type, status, category, tier, tags,
           price_cents, sale_price_cents, warranty,
           sizes, color_temps, badge, blurb, features, draft, variants,
           images, fitment_positions, cost_cents, supplier_name,
           supplier_sku, supplier_notes, notes, sku, msku, barcode, vendor,
           taxable, weight_oz, length_in, width_in, height_in,
           country_of_origin, hs_code, seo_title, seo_description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["id"], data["name"], data.get("description", ""),
        data.get("product_type", ""), status,
        data["category"], data.get("tier", ""),
        json.dumps(data.get("tags") or []),
        data["price_cents"], data.get("sale_price_cents"),
        data.get("warranty", "1-year warranty"),
        json.dumps(_group_values(groups, "size")),
        json.dumps(_group_values(groups, "color temp", "color_temp", "color")),
        data.get("badge") or None, data.get("blurb", ""),
        json.dumps(data.get("features") or []),
        0 if status == "active" else 1,
        json.dumps(groups),
        json.dumps(data.get("images") or []),
        json.dumps(data.get("fitment_positions") or []),
        data.get("cost_cents"), data.get("supplier_name") or None,
        data.get("supplier_sku") or None, data.get("supplier_notes") or None,
        data.get("notes") or None,
        data.get("sku") or None, data.get("msku") or None,
        data.get("barcode") or None, data.get("vendor") or None,
        1 if data.get("taxable", True) else 0,
        data.get("weight_oz"), data.get("length_in"), data.get("width_in"),
        data.get("height_in"), data.get("country_of_origin") or None,
        data.get("hs_code") or None, data.get("seo_title") or None,
        data.get("seo_description") or None,
    ))
    con.execute("""
        INSERT OR IGNORE INTO inventory
          (product_id, stock, low_threshold, updated_at)
        VALUES (?, ?, ?, ?)
    """, (data["id"], PLACEHOLDER_DEFAULT_STOCK,
          PLACEHOLDER_LOW_THRESHOLD, _utcnow()))
    con.commit()
    con.close()
    _seed_variations_for(_connect(), data["id"], groups, track_inventory=1)
    return data["id"]


def update_product(pid, data):
    """Update an existing product. Same data keys as create_product
    (id itself is immutable). Returns True if the row existed."""
    groups = data.get("variant_groups") or []
    status = data.get("status") or "draft"
    if status not in PRODUCT_STATUSES:
        status = "draft"
    con = _connect()
    cur = con.execute("""
        UPDATE products SET
          name = ?, description = ?, product_type = ?, status = ?,
          category = ?, tier = ?, tags = ?, price_cents = ?,
          sale_price_cents = ?, warranty = ?, sizes = ?, color_temps = ?,
          badge = ?, blurb = ?, features = ?, draft = ?, variants = ?,
          images = ?, image_paths = ?, fitment_positions = ?, cost_cents = ?,
          supplier_name = ?, supplier_sku = ?, supplier_notes = ?,
          notes = ?, sku = ?, msku = ?, barcode = ?, vendor = ?,
          taxable = ?, weight_oz = ?, length_in = ?, width_in = ?,
          height_in = ?, country_of_origin = ?, hs_code = ?,
          seo_title = ?, seo_description = ?
        WHERE id = ?
    """, (
        data["name"], data.get("description", ""),
        data.get("product_type", ""), status,
        data["category"], data.get("tier", ""),
        json.dumps(data.get("tags") or []),
        data["price_cents"], data.get("sale_price_cents"),
        data.get("warranty", "1-year warranty"),
        json.dumps(_group_values(groups, "size")),
        json.dumps(_group_values(groups, "color temp", "color_temp", "color")),
        data.get("badge") or None, data.get("blurb", ""),
        json.dumps(data.get("features") or []),
        0 if status == "active" else 1,
        json.dumps(groups),
        json.dumps(data.get("images") or []),
        json.dumps(data.get("images") or []),
        json.dumps(data.get("fitment_positions") or []),
        data.get("cost_cents"), data.get("supplier_name") or None,
        data.get("supplier_sku") or None, data.get("supplier_notes") or None,
        data.get("notes") or None,
        data.get("sku") or None, data.get("msku") or None,
        data.get("barcode") or None, data.get("vendor") or None,
        1 if data.get("taxable", True) else 0,
        data.get("weight_oz"), data.get("length_in"), data.get("width_in"),
        data.get("height_in"), data.get("country_of_origin") or None,
        data.get("hs_code") or None, data.get("seo_title") or None,
        data.get("seo_description") or None,
        pid,
    ))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    return changed


def product_referenced_in_orders(pid):
    """True if any order's line-item snapshot mentions this product. Such
    products must be archived/unpublished instead of deleted (history)."""
    con = _connect()
    rows = con.execute("SELECT line_items FROM orders").fetchall()
    con.close()
    for r in rows:
        try:
            items = json.loads(r["line_items"])
        except (TypeError, ValueError):
            continue
        for item in items:
            if item.get("product_id") == pid:
                return True
    return False


def delete_product(pid):
    """Delete a product + its variations + its inventory row. Returns
    (ok, message); refuses when orders reference the product."""
    if product_referenced_in_orders(pid):
        return False, ("Cannot delete: existing orders reference this "
                       "product. Archive it instead to hide it everywhere "
                       "while keeping order history intact.")
    con = _connect()
    cur = con.execute("DELETE FROM products WHERE id = ?", (pid,))
    con.execute("DELETE FROM product_variations WHERE product_id = ?", (pid,))
    con.execute("DELETE FROM inventory WHERE product_id = ?", (pid,))
    con.commit()
    deleted = cur.rowcount > 0
    con.close()
    return deleted, ("deleted" if deleted else "product not found")


def get_product(pid, with_variations=True):
    con = _connect()
    r = con.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    con.close()
    return _row_to_product(r, with_variations=with_variations) if r else None


def set_draft(pid, is_draft):
    """Admin publish/unpublish toggle. True = draft (hidden publicly).
    Archived products stay archived."""
    con = _connect()
    con.execute("""
        UPDATE products
           SET draft = ?,
               status = CASE WHEN status = 'archived' THEN 'archived'
                             WHEN ? THEN 'draft' ELSE 'active' END
         WHERE id = ?""",
                (1 if is_draft else 0, 1 if is_draft else 0, pid))
    con.commit()
    con.close()


def set_status(pid, status):
    """Set a product's lifecycle status (active/draft/archived)."""
    if status not in PRODUCT_STATUSES:
        return False
    con = _connect()
    cur = con.execute(
        "UPDATE products SET status = ?, draft = ? WHERE id = ?",
        (status, 0 if status == "active" else 1, pid))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    return changed


def list_products(category=None, include_drafts=False, include_archived=False):
    """Public listings: only active products, unless include_drafts=True
    (admin default: active + draft) or include_archived=True."""
    statuses = ["active"]
    if include_drafts:
        statuses.append("draft")
    if include_archived:
        statuses.append("archived")
    ph = ",".join("?" for _ in statuses)
    con = _connect()
    if category:
        rows = con.execute(
            f"SELECT * FROM products WHERE category = ? AND status IN ({ph})"
            " ORDER BY price_cents",
            (category, *statuses)).fetchall()
    else:
        rows = con.execute(
            f"SELECT * FROM products WHERE status IN ({ph})"
            " ORDER BY category, price_cents",
            (*statuses,)).fetchall()
    con.close()
    return [_row_to_product(r) for r in rows]


def search_products(query, include_archived=False):
    """Admin product search across name, SKU, tags, vendor."""
    q = f"%{(query or '').strip().lower()}%"
    statuses = ["active", "draft"] + (["archived"] if include_archived else [])
    ph = ",".join("?" for _ in statuses)
    con = _connect()
    rows = con.execute(
        f"""SELECT * FROM products
            WHERE status IN ({ph}) AND (
              lower(name) LIKE ? OR lower(id) LIKE ? OR lower(sku) LIKE ?
              OR lower(vendor) LIKE ? OR lower(tags) LIKE ?
              OR lower(product_type) LIKE ?)
            ORDER BY name""",
        (*statuses, q, q, q, q, q, q)).fetchall()
    con.close()
    return [_row_to_product(r) for r in rows]


def products_matching_size(bulb_size, categories):
    """Products in the given categories whose sizes include bulb_size.

    Public fitment results: drafts never appear here."""
    target = norm_size(bulb_size)
    matches = []
    for p in list_products():
        if p["category"] not in categories:
            continue
        if any(norm_size(s) == target for s in p["sizes"]):
            matches.append(p)
    matches.sort(key=lambda p: p["price_cents"])
    return matches


# ---------------------------------------------------------------- orders
ORDER_STATUSES = ("new", "paid", "shipped", "cancelled")

# Allowed status transitions. Anything not listed is rejected.
ORDER_TRANSITIONS = {
    "new": ("paid", "cancelled"),
    "paid": ("shipped", "cancelled"),
    "shipped": (),
    "cancelled": (),
}


def _row_to_order(r):
    d = dict(r)
    d["line_items"] = json.loads(d["line_items"])
    return d


def create_order(customer, lines, subtotal_cents, shipping_cents=0,
                 promo_code=None, discount_cents=0):
    """Persist a checkout snapshot with status 'new'. lines = cart_detailed().

    promo_code/discount_cents record the Track 3 promo applied at checkout;
    the order total is subtotal - discount + shipping.
    """
    discount_cents = max(0, min(subtotal_cents, int(discount_cents or 0)))
    total = subtotal_cents - discount_cents + shipping_cents
    snapshot = []
    for l in lines:
        var = l.get("variation") or {}
        snapshot.append({
            "product_id": l["product"]["id"],
            "name": l["product"]["name"],
            "variation_id": var.get("id"),
            "variation_label": l.get("variation_label", ""),
            "size": l["size"],
            "color_temp": l["color_temp"],
            "qty": l["qty"],
            "price_cents": l.get("unit_price_cents",
                                 l["product"]["price_cents"]),
            "line_total_cents": l["line_total"],
        })
    now = _utcnow()
    con = _connect()
    cur = con.execute("""
        INSERT INTO orders
          (created_at, updated_at, status, customer_name, customer_email,
           addr_line1, addr_line2, addr_city, addr_state, addr_zip,
           line_items, subtotal_cents, shipping_cents, total_cents,
           promo_code, discount_cents)
        VALUES (?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (now, now,
          customer.get("name", ""), customer.get("email", ""),
          customer.get("line1", ""), customer.get("line2", ""),
          customer.get("city", ""), customer.get("state", ""),
          customer.get("zip", ""),
          json.dumps(snapshot), subtotal_cents, shipping_cents, total,
          promo_code, discount_cents))
    con.commit()
    oid = cur.lastrowid
    con.close()
    log_order_event(oid, "created",
                    f"Order placed — ${total / 100:,.2f}")
    return oid


def get_order(oid):
    con = _connect()
    r = con.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    con.close()
    return _row_to_order(r) if r else None


def get_order_by_session(stripe_session_id):
    if not stripe_session_id:
        return None
    con = _connect()
    r = con.execute("SELECT * FROM orders WHERE stripe_session_id = ?",
                    (stripe_session_id,)).fetchone()
    con.close()
    return _row_to_order(r) if r else None


def list_orders(limit=200):
    con = _connect()
    rows = con.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [_row_to_order(r) for r in rows]


def set_stripe_session(oid, stripe_session_id):
    con = _connect()
    con.execute(
        "UPDATE orders SET stripe_session_id = ?, updated_at = ? WHERE id = ?",
        (stripe_session_id, _utcnow(), oid))
    con.commit()
    con.close()


def mark_order_paid(oid, payment_intent_id=None):
    """new -> paid. Idempotent: returns False if already paid/not new."""
    con = _connect()
    cur = con.execute("""
        UPDATE orders
           SET status = 'paid',
               stripe_payment_intent_id = COALESCE(?, stripe_payment_intent_id),
               updated_at = ?
         WHERE id = ? AND status = 'new'
    """, (payment_intent_id, _utcnow(), oid))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    if changed:
        log_order_event(oid, "paid",
                        "Payment confirmed"
                        + (f" (Stripe intent {payment_intent_id})"
                           if payment_intent_id else ""))
    return changed


def transition_order(oid, new_status, tracking_number=None):
    """Validate + apply an admin status change. Returns (ok, message)."""
    if new_status not in ORDER_STATUSES:
        return False, f"unknown status {new_status!r}"
    order = get_order(oid)
    if not order:
        return False, "order not found"
    if new_status not in ORDER_TRANSITIONS.get(order["status"], ()):
        return False, (f"cannot move {order['status']} -> {new_status}")
    con = _connect()
    if new_status == "shipped" and tracking_number:
        con.execute("""
            UPDATE orders SET status = ?, tracking_number = ?,
                              updated_at = ? WHERE id = ?
        """, (new_status, tracking_number, _utcnow(), oid))
    else:
        con.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, _utcnow(), oid))
    con.commit()
    con.close()
    detail = ""
    if new_status == "shipped" and tracking_number:
        detail = f"Tracking: {tracking_number}"
    log_order_event(oid, new_status, detail)
    return True, f"order #{oid} -> {new_status}"


def order_stats():
    """Sales summary: revenue (paid+shipped), counts by status, by date."""
    con = _connect()
    by_status = {}
    for status, n, rev in con.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(total_cents), 0)"
            " FROM orders GROUP BY status"):
        by_status[status] = {"count": n, "revenue_cents": rev or 0}
    revenue_cents = con.execute(
        "SELECT COALESCE(SUM(total_cents), 0) FROM orders"
        " WHERE status IN ('paid', 'shipped')").fetchone()[0]
    by_date = [
        {"day": day, "count": n, "revenue_cents": rev or 0}
        for day, n, rev in con.execute(
            "SELECT substr(created_at, 1, 10), COUNT(*),"
            " COALESCE(SUM(total_cents), 0)"
            " FROM orders WHERE status IN ('paid', 'shipped')"
            " GROUP BY substr(created_at, 1, 10)"
            " ORDER BY substr(created_at, 1, 10) DESC LIMIT 30")
    ]
    con.close()
    return {"by_status": by_status, "revenue_cents": revenue_cents,
            "by_date": by_date}


# ---------------------------------------------------------------- inventory
def list_inventory():
    con = _connect()
    rows = con.execute("""
        SELECT i.*, p.name, p.tier, p.price_cents
          FROM inventory i JOIN products p ON p.id = i.product_id
         ORDER BY p.name
    """).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["low_stock"] = d["stock"] <= d["low_threshold"]
        out.append(d)
    return out


def get_stock(product_id):
    con = _connect()
    r = con.execute("SELECT * FROM inventory WHERE product_id = ?",
                    (product_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def set_stock(product_id, stock, low_threshold=None):
    """Admin edit of stock counts. stock/low_threshold must be >= 0."""
    stock = max(0, int(stock))
    con = _connect()
    if low_threshold is None:
        con.execute("UPDATE inventory SET stock = ?, updated_at = ?"
                    " WHERE product_id = ?",
                    (stock, _utcnow(), product_id))
    else:
        con.execute("UPDATE inventory SET stock = ?, low_threshold = ?,"
                    " updated_at = ? WHERE product_id = ?",
                    (stock, max(0, int(low_threshold)), _utcnow(),
                     product_id))
    con.commit()
    con.close()


def set_low_threshold(product_id, low_threshold):
    """Update just the product-level low-stock threshold (CSV importer)."""
    con = _connect()
    con.execute("UPDATE inventory SET low_threshold = ?, updated_at = ?"
                " WHERE product_id = ?",
                (max(0, int(low_threshold)), _utcnow(), product_id))
    con.commit()
    con.close()


def decrement_stock_for_order(oid):
    """Decrement inventory for a paid order. Idempotent: guarded by the
    order's inventory_applied flag, so duplicate webhooks never double-count.
    Variation-tracked lines decrement the variation; everything else falls
    back to the product-level inventory row. Returns True only the first
    time it applies."""
    order = get_order(oid)
    if not order or order["inventory_applied"]:
        return False
    con = _connect()
    try:
        # Re-check inside the transaction: only decrement if this order's
        # flag is still 0. Status must be paid.
        cur = con.execute(
            "SELECT inventory_applied FROM orders WHERE id = ? AND status = 'paid'",
            (oid,))
        row = cur.fetchone()
        if not row or row["inventory_applied"]:
            con.close()
            return False
        for item in order["line_items"]:
            if not _decrement_line_item(con, item):
                con.execute(
                    "UPDATE inventory SET stock = CASE WHEN stock - ? < 0"
                    " THEN 0 ELSE stock - ? END, updated_at = ?"
                    " WHERE product_id = ?",
                    (item["qty"], item["qty"], _utcnow(),
                     item["product_id"]))
        con.execute("UPDATE orders SET inventory_applied = 1,"
                    " updated_at = ? WHERE id = ?", (_utcnow(), oid))
        con.commit()
        return True
    finally:
        con.close()


def _decrement_line_item(con, item):
    """Decrement a tracked variation's inventory for one order line.
    Returns True when the variation handled it (product-level fallback
    needed otherwise)."""
    vid = item.get("variation_id")
    if not vid:
        return False
    r = con.execute("SELECT * FROM product_variations WHERE id = ?",
                    (vid,)).fetchone()
    if not r or not r["track_inventory"]:
        return False
    if r["product_id"] != item.get("product_id"):
        return False  # variation doesn't belong to this product; fallback
    qty = max(0, int(item.get("qty", 0)))
    if r["allow_oversell"]:
        con.execute("UPDATE product_variations SET inventory_qty ="
                    " inventory_qty - ? WHERE id = ?", (qty, vid))
    else:
        con.execute("UPDATE product_variations SET inventory_qty = CASE"
                    " WHEN inventory_qty - ? < 0 THEN 0"
                    " ELSE inventory_qty - ? END WHERE id = ?",
                    (qty, qty, vid))
    return True


# ---------------------------------------------------------------- settings
#
# Notification preferences + misc store settings, editable in
# /admin/settings. No notification behavior may require a code change:
# every send path in emails.py checks its toggle here first.

# (key, default) — INSERT OR IGNORE seeded on every init_db.
SETTING_DEFAULTS = (
    # --- notification toggles ("1" = on) ---
    ("notify_owner_new_order", "1"),
    ("notify_customer_order_confirmation", "1"),
    ("notify_customer_payment_failed", "1"),
    ("notify_customer_shipped", "1"),
    ("notify_owner_low_stock", "1"),
    ("notify_owner_contact_message", "1"),
    ("notify_owner_review_submitted", "1"),
    # --- recipient addresses for owner alerts ---
    ("email_owner_orders", ""),
    ("email_owner_alerts", ""),
    # --- shipping ---
    ("shipping_tare_oz", "3"),   # packaging allowance added to CSV weights
    # --- Square POS inventory sync ---
    # Access token + webhook signature key are secrets: stored server-side
    # only, never rendered in templates unmasked, never logged.
    ("square_access_token", ""),
    ("square_location_id", ""),
    ("square_environment", "sandbox"),   # sandbox | production
    ("square_webhook_signature_key", ""),
    ("square_auto_sync", "0"),           # "1" = push counts to Square on order payment
)

NOTIFY_TOGGLES = (
    ("notify_owner_new_order",
     "Owner: new paid order alert", "owner"),
    ("notify_customer_order_confirmation",
     "Customer: order confirmation email", "customer"),
    ("notify_customer_payment_failed",
     "Customer: payment failed notice", "customer"),
    ("notify_customer_shipped",
     "Customer: shipped email with tracking", "customer"),
    ("notify_owner_low_stock",
     "Owner: low-stock alert", "owner"),
    ("notify_owner_contact_message",
     "Owner: contact-form message alert", "owner"),
    ("notify_owner_review_submitted",
     "Owner: new product review alert", "owner"),
)


def init_settings(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)
    now = _utcnow()
    for key, default in SETTING_DEFAULTS:
        con.execute("INSERT OR IGNORE INTO settings (key, value, updated_at)"
                    " VALUES (?, ?, ?)", (key, default, now))


def get_setting(key, default=""):
    """Read a setting. Never raises: a missing/broken table yields the
    default so email paths degrade safely."""
    try:
        con = _connect()
        r = con.execute("SELECT value FROM settings WHERE key = ?",
                        (key,)).fetchone()
        con.close()
    except Exception:  # noqa: BLE001 - settings must never break callers
        return default
    return r["value"] if r else default


def set_setting(key, value):
    con = _connect()
    con.execute("INSERT INTO settings (key, value, updated_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (key, str(value), _utcnow()))
    con.commit()
    con.close()


def get_all_settings():
    con = _connect()
    rows = con.execute("SELECT key, value FROM settings").fetchall()
    con.close()
    return {r["key"]: r["value"] for r in rows}


def notifications_enabled(toggle_key):
    """True when the named notification toggle is on (default on)."""
    return get_setting(toggle_key, "1") == "1"


# ---------------------------------------------------------------- order events
#
# Fulfillment timeline: every status change, tracking update, refund, and
# admin note is appended here and shown on the order detail page.


def init_order_events(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            event TEXT NOT NULL,      -- created/paid/shipped/cancelled/
                                      -- tracking/refund/note
            detail TEXT NOT NULL DEFAULT ''
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_order_events_order"
                " ON order_events(order_id)")


def log_order_event(oid, event, detail=""):
    try:
        oid = int(oid)
    except (TypeError, ValueError):
        return
    con = _connect()
    con.execute("INSERT INTO order_events (order_id, created_at, event,"
                " detail) VALUES (?, ?, ?, ?)",
                (oid, _utcnow(), event, detail or ""))
    con.commit()
    con.close()


def list_order_events(oid):
    con = _connect()
    rows = con.execute("SELECT * FROM order_events WHERE order_id = ?"
                       " ORDER BY id", (oid,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- order search
def search_orders(q=None, status=None, date_from=None, date_to=None,
                  limit=200):
    """Admin order search: q matches order id, customer name, or email;
    status filters new/paid/shipped/cancelled; dates are YYYY-MM-DD."""
    clauses, params = [], []
    if q:
        q = q.strip()
        clauses.append("(CAST(id AS TEXT) = ?"
                       " OR lower(customer_name) LIKE ?"
                       " OR lower(customer_email) LIKE ?)")
        params += [q, f"%{q.lower()}%", f"%{q.lower()}%"]
    if status in ORDER_STATUSES:
        clauses.append("status = ?")
        params.append(status)
    if date_from:
        clauses.append("substr(created_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(created_at, 1, 10) <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    con = _connect()
    rows = con.execute(
        f"SELECT * FROM orders {where} ORDER BY id DESC LIMIT ?",
        (*params, max(1, int(limit or 200)))).fetchall()
    con.close()
    return [_row_to_order(r) for r in rows]


def unfulfilled_orders(limit=500):
    """Paid but not yet shipped — the Pirate Ship export / pick list set."""
    con = _connect()
    rows = con.execute("SELECT * FROM orders WHERE status = 'paid'"
                       " ORDER BY id LIMIT ?", (limit,)).fetchall()
    con.close()
    return [_row_to_order(r) for r in rows]


def set_tracking(oid, tracking_number):
    """Set/replace the tracking number (admin). Returns the order or None."""
    con = _connect()
    con.execute("UPDATE orders SET tracking_number = ?, updated_at = ?"
                " WHERE id = ?",
                ((tracking_number or "").strip() or None, _utcnow(), oid))
    con.commit()
    con.close()
    order = get_order(oid)
    if order:
        log_order_event(oid, "tracking",
                        f"Tracking set: {tracking_number}")
    return order


def update_order_notes(oid, notes):
    con = _connect()
    cur = con.execute("UPDATE orders SET notes = ?, updated_at = ?"
                      " WHERE id = ?", (notes or "", _utcnow(), oid))
    con.commit()
    changed = cur.rowcount > 0
    con.close()
    if changed:
        log_order_event(oid, "note", "Admin note updated")
    return changed


def record_refund(oid, amount_cents, reason=""):
    """Record a Stripe refund against the order: bumps refunded_cents and
    appends a timeline event. Returns the new refunded total."""
    order = get_order(oid)
    if not order:
        return None
    new_total = (order.get("refunded_cents") or 0) + max(0, int(amount_cents))
    con = _connect()
    con.execute("UPDATE orders SET refunded_cents = ?, updated_at = ?"
                " WHERE id = ?", (new_total, _utcnow(), oid))
    con.commit()
    con.close()
    log_order_event(oid, "refund",
                    f"Refunded ${amount_cents / 100:,.2f}"
                    + (f" — {reason}" if reason else ""))
    return new_total


def low_stock_keys():
    """Set of 'product:<pid>' / 'variation:<vid>' keys currently at or
    below their low-stock threshold (tracked items only). Used to detect
    newly-low stock after an order decrements inventory."""
    keys = set()
    con = _connect()
    for r in con.execute(
            "SELECT product_id, stock, low_threshold FROM inventory"):
        if r["stock"] <= r["low_threshold"]:
            keys.add(f"product:{r['product_id']}")
    for r in con.execute(
            "SELECT id, inventory_qty, low_threshold FROM product_variations"
            " WHERE track_inventory = 1"):
        if r["inventory_qty"] <= r["low_threshold"]:
            keys.add(f"variation:{r['id']}")
    con.close()
    return keys


def low_stock_details(keys):
    """Human-readable lines for low-stock keys: [(label, sku, qty)]."""
    out = []
    con = _connect()
    for key in sorted(keys):
        kind, ident = key.split(":", 1)
        if kind == "product":
            r = con.execute("SELECT id, name, sku FROM products WHERE id = ?",
                            (ident,)).fetchone()
            i = con.execute("SELECT stock FROM inventory WHERE product_id = ?",
                            (ident,)).fetchone()
            if r:
                out.append((r["name"], r["sku"] or "—",
                            i["stock"] if i else 0))
        else:
            r = con.execute(
                "SELECT v.id, v.sku, v.option_values, v.inventory_qty,"
                " p.name FROM product_variations v"
                " JOIN products p ON p.id = v.product_id WHERE v.id = ?",
                (ident,)).fetchone()
            if r:
                try:
                    opts = ", ".join(
                        f"{k}: {v}"
                        for k, v in json.loads(r["option_values"]).items())
                except (TypeError, ValueError):
                    opts = ""
                out.append((f"{r['name']} ({opts})" if opts else r["name"],
                            r["sku"] or "—", r["inventory_qty"]))
    con.close()
    return out


# ---------------------------------------------------------------- Square sync
#
# Square POS inventory sync. The WEBSITE is the catalog master: "Push catalog
# to Square" creates Square catalog items/variations from website products
# (matched on SKU), then two-way inventory sync keeps counts aligned.
#
# Every sync action is appended to square_sync_log. Conflict policy is
# last-write-wins: each applied change overwrites with the incoming count and
# logs old/new, so the full history is auditable. Counts are never allowed
# to go negative — a sync that would do so is clamped to 0 and flagged.


def init_square_sync_log(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS square_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            direction TEXT NOT NULL,   -- catalog_push / push / pull /
                                       -- webhook / test / link
            sku TEXT NOT NULL DEFAULT '',
            square_object_id TEXT NOT NULL DEFAULT '',
            old_qty INTEGER,
            new_qty INTEGER,
            result TEXT NOT NULL DEFAULT 'ok',   -- ok / skipped / clamped /
                                                -- error
            detail TEXT NOT NULL DEFAULT ''
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_square_sync_log_created"
                " ON square_sync_log(created_at)")


def log_square_sync(direction, sku="", square_object_id="", old_qty=None,
                    new_qty=None, result="ok", detail=""):
    """Append a sync log entry. Never raises: logging must not break sync."""
    try:
        con = _connect()
        con.execute(
            "INSERT INTO square_sync_log (created_at, direction, sku,"
            " square_object_id, old_qty, new_qty, result, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_utcnow(), direction, sku or "", square_object_id or "",
             old_qty, new_qty, result, detail or ""))
        con.commit()
        con.close()
    except Exception:  # noqa: BLE001 - logging is best-effort
        pass


def list_square_sync_log(limit=100):
    con = _connect()
    rows = con.execute("SELECT * FROM square_sync_log ORDER BY id DESC"
                       " LIMIT ?", (max(1, int(limit or 100)),)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def set_product_square_id(pid, square_id):
    con = _connect()
    con.execute("UPDATE products SET square_catalog_object_id = ?,"
                " square_last_synced_at = ? WHERE id = ?",
                (square_id, _utcnow(), pid))
    con.commit()
    con.close()


def set_variation_square_id(vid, square_id):
    con = _connect()
    con.execute("UPDATE product_variations SET square_catalog_object_id = ?,"
                " square_last_synced_at = ? WHERE id = ?",
                (square_id, _utcnow(), vid))
    con.commit()
    con.close()


def touch_square_synced(kind, ident):
    """Stamp square_last_synced_at after a successful count sync."""
    table = "product_variations" if kind == "variation" else "products"
    con = _connect()
    con.execute(f"UPDATE {table} SET square_last_synced_at = ?"
                f" WHERE id = ?", (_utcnow(), ident))
    con.commit()
    con.close()


def get_product_by_square_id(square_id):
    """Product lookup by its Square catalog object id. None when unknown."""
    if not square_id:
        return None
    con = _connect()
    r = con.execute("SELECT * FROM products WHERE square_catalog_object_id"
                    " = ?", (square_id,)).fetchone()
    con.close()
    return _row_to_product(r, with_variations=False) if r else None


def get_variation_by_square_id(square_id):
    """Variation lookup by its Square catalog object id. None when unknown."""
    if not square_id:
        return None
    con = _connect()
    r = con.execute("SELECT * FROM product_variations WHERE"
                    " square_catalog_object_id = ?", (square_id,)).fetchone()
    con.close()
    if not r:
        return None
    prod = get_product(r["product_id"], with_variations=False)
    return _row_to_variation(r, prod)


def list_all_variations_linked():
    """(kind, vid, sku, square_object_id, qty, label) for every variation
    with inventory tracking on. Includes the link id (may be None)."""
    con = _connect()
    rows = con.execute(
        "SELECT v.id, v.sku, v.square_catalog_object_id, v.inventory_qty,"
        " v.option_values, v.track_inventory, p.name"
        " FROM product_variations v JOIN products p ON p.id = v.product_id"
        " ORDER BY p.name, v.position, v.id").fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            opts = ", ".join(f"{k}: {v}"
                             for k, v in json.loads(
                                 r["option_values"] or "{}").items())
        except (TypeError, ValueError):
            opts = ""
        label = f"{r['name']} ({opts})" if opts else r["name"]
        out.append(("variation", r["id"], r["sku"] or "",
                    r["square_catalog_object_id"], r["inventory_qty"],
                    label, bool(r["track_inventory"])))
    return out


def list_product_level_stock():
    """(kind, pid, sku, square_object_id, qty, label) for product-level
    inventory rows (the fallback used by lines without a tracked
    variation)."""
    con = _connect()
    rows = con.execute(
        "SELECT p.id, p.sku, p.square_catalog_object_id, i.stock, p.name"
        " FROM inventory i JOIN products p ON p.id = i.product_id"
        " ORDER BY p.name").fetchall()
    con.close()
    return [("product", r["id"], r["sku"] or "",
             r["square_catalog_object_id"], r["stock"], r["name"])
            for r in rows]


def apply_square_count(kind, ident, qty, square_object_id=""):
    """Apply an incoming Square count (last-write-wins). qty is clamped
    at 0 — a clamp is logged as result='clamped', never silent.
    Returns the stored qty."""
    qty = int(qty or 0)
    result = "ok"
    if qty < 0:
        qty = 0
        result = "clamped"
    con = _connect()
    if kind == "variation":
        r = con.execute("SELECT inventory_qty, sku FROM product_variations"
                        " WHERE id = ?", (ident,)).fetchone()
        old = r["inventory_qty"] if r else None
        sku = (r["sku"] or "") if r else ""
        if r:
            con.execute("UPDATE product_variations SET inventory_qty = ?,"
                        " square_last_synced_at = ? WHERE id = ?",
                        (qty, _utcnow(), ident))
    else:
        r = con.execute("SELECT i.stock, p.sku FROM inventory i"
                        " JOIN products p ON p.id = i.product_id"
                        " WHERE i.product_id = ?", (ident,)).fetchone()
        old = r["stock"] if r else None
        sku = (r["sku"] or "") if r else ""
        if r:
            con.execute("UPDATE inventory SET stock = ?, updated_at = ?"
                        " WHERE product_id = ?", (qty, _utcnow(), ident))
    con.commit()
    con.close()
    log_square_sync("pull", sku=sku, square_object_id=square_object_id or "",
                    old_qty=old, new_qty=qty, result=result,
                    detail="negative count clamped to 0"
                    if result == "clamped" else "")
    return qty
