"""Square POS inventory sync for Bravo LEDs.

The WEBSITE is the catalog master (the store has very little item data in
Square today): "Push catalog to Square" creates Square catalog items and
item variations from website products, matched on SKU, and stores the
returned Square object IDs on our variation rows. After that, inventory
counts sync both ways:

  - Website order paid  -> push resulting counts to Square (batch-change).
  - Square sale/inventory change -> Square webhook hits /webhooks/square,
    we verify the signature and update our counts by object-ID mapping.

Credentials live in the settings table (set via /admin Settings) with env
overrides SQUARE_ACCESS_TOKEN / SQUARE_LOCATION_ID. They are never logged,
never rendered unmasked, and never leave the server.

Square API: Inventory API + Catalog API over plain HTTPS (requests).
Docs: https://developer.squareup.com/reference/square

No network in tests: _request() is monkeypatched.
"""

import base64
import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone

import db

log = logging.getLogger(__name__)

SQUARE_VERSION = "2024-12-18"
SANDBOX_BASE = "https://connect.squareupsandbox.com"
PROD_BASE = "https://connect.squareup.com"

ENVIRONMENTS = ("sandbox", "production")


class SquareError(Exception):
    """Any Square API or configuration failure."""


# ------------------------------------------------------------------ config


def _cfg(key):
    """Settings value with env override for the two deploy-time secrets."""
    env_map = {
        "square_access_token": "SQUARE_ACCESS_TOKEN",
        "square_location_id": "SQUARE_LOCATION_ID",
    }
    if key in env_map:
        val = os.environ.get(env_map[key])
        if val:
            return val
    return db.get_setting(key, "")


def square_configured():
    """True when we have an access token AND a location id (either source)."""
    return bool(_cfg("square_access_token")) and bool(_cfg("square_location_id"))


def environment():
    env = _cfg("square_environment") or "sandbox"
    return env if env in ENVIRONMENTS else "sandbox"


def base_url():
    return PROD_BASE if environment() == "production" else SANDBOX_BASE


def auto_sync_enabled():
    return db.get_setting("square_auto_sync", "0") == "1"


# ------------------------------------------------------------------ HTTP


def _request(method, path, body=None):
    """Low-level Square API call. Returns the decoded JSON dict.

    Raises SquareError on any transport problem or non-2xx response.
    Monkeypatched in tests — this is the only network touchpoint.
    """
    import requests

    token = _cfg("square_access_token")
    if not token:
        raise SquareError("Square access token is not configured")
    url = base_url() + path
    try:
        resp = requests.request(
            method, url,
            headers={
                "Authorization": f"Bearer {token}",
                "Square-Version": SQUARE_VERSION,
                "Content-Type": "application/json",
            },
            json=body, timeout=20)
    except Exception as exc:
        raise SquareError(f"Square request failed: {exc}")
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not resp.ok:
        errors = data.get("errors") or []
        detail = "; ".join(e.get("detail") or e.get("code", "?")
                           for e in errors) or f"HTTP {resp.status_code}"
        raise SquareError(f"Square API error: {detail}")
    return data if isinstance(data, dict) else {}


def test_connection():
    """Verify token + location against Square. Returns (ok, message).

    Never raises, never includes the token in the message.
    """
    if not _cfg("square_access_token"):
        return False, "No Square access token saved."
    location_id = _cfg("square_location_id")
    if not location_id:
        return False, "No Square location ID saved."
    try:
        data = _request("GET", f"/v2/locations/{location_id}")
        loc = (data.get("location") or {})
        name = loc.get("name") or location_id
        db.log_square_sync("test", detail=f"connection ok ({name})")
        return True, f"Connected to “{name}” ({environment()})."
    except SquareError as exc:
        db.log_square_sync("test", result="error", detail=str(exc))
        return False, f"Connection failed: {exc}"


# ------------------------------------------------------- webhook signature


def verify_webhook_signature(body, signature_header, notification_url):
    """Verify Square's x-square-hmacsha1-signature header.

    Square's algorithm: base64(HMAC-SHA1(signature_key,
    notification_url + raw_body)). Raises SquareError on any problem —
    fail closed, like the Stripe webhook.
    """
    key = _cfg("square_webhook_signature_key")
    if not key:
        raise SquareError("Square webhook signature key is not configured")
    if not signature_header:
        raise SquareError("missing x-square-hmacsha1-signature header")
    if not notification_url:
        raise SquareError("cannot determine notification URL")
    payload = notification_url.encode("utf-8") + bytes(body or b"")
    digest = hmac.new(key.encode("utf-8"), payload,
                      hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode("ascii")
    if not hmac.compare_digest(expected, signature_header):
        raise SquareError("Square webhook signature mismatch")
    return True


# ------------------------------------------------------------- catalog push


def find_variation_by_sku(sku):
    """Search Square's catalog for an ITEM_VARIATION with this exact SKU.

    Used before creating, so re-pushing (or a SKU that already exists in
    Square) adopts the existing object instead of duplicating it.
    Returns the Square object id, or None.
    """
    if not sku:
        return None
    data = _request("POST", "/v2/catalog/search", {
        "object_types": ["ITEM_VARIATION"],
        "query": {"text_query": {"keywords": [sku]}},
        "limit": 10,
    })
    for obj in data.get("objects") or []:
        vd = (obj.get("item_variation_data") or {})
        if (vd.get("sku") or "").strip().lower() == sku.strip().lower():
            return obj.get("id")
    return None


def _variation_payload(product, variation, temp_prefix):
    """Build one ITEM_VARIATION object for batch-upsert."""
    label = variation.get("label") or "Standard"
    sku = (variation.get("sku") or "").strip()
    price = variation.get("effective_price_cents")
    if price is None:
        price = product["price_cents"]
    return {
        "type": "ITEM_VARIATION",
        "id": f"{temp_prefix}var-{variation['id']}",
        "item_variation_data": {
            "name": label[:255],
            "sku": sku,
            "pricing_type": "FIXED_PRICING",
            "price_money": {"amount": int(price or 0), "currency": "USD"},
            "track_inventory": True,
        },
    }


def _item_payload(product, variations, temp_prefix):
    var_payloads = [_variation_payload(product, v, temp_prefix)
                    for v in variations]
    return {
        "type": "ITEM",
        "id": f"{temp_prefix}item-{product['id']}",
        "item_data": {
            "name": product["name"][:255],
            "description": (product.get("blurb") or "")[:1024],
            "variations": var_payloads,
        },
    }


def push_catalog(products=None):
    """Create/update Square catalog items from website products.

    products: list of product dicts (with variations); defaults to all
    active products. Matching: a variation that already has a stored
    square_catalog_object_id is updated in place; otherwise we search
    Square by SKU and adopt the match; otherwise it is created.
    Returns a per-item report list of dicts
    {product_id, name, action: created|updated|skipped, detail}.
    """
    if not square_configured():
        raise SquareError("Square is not configured")
    if products is None:
        products = [p for p in db.list_products(include_drafts=False)
                    if p.get("status") == "active"]
    report = []
    for product in products:
        variations = [v for v in (product.get("variations") or [])
                      if v.get("track_inventory")]
        if not variations:
            report.append({"product_id": product["id"],
                           "name": product["name"], "action": "skipped",
                           "detail": "no tracked variations"})
            db.log_square_sync("catalog_push", result="skipped",
                               detail=f"{product['name']}: no tracked"
                                      " variations")
            continue
        temp_prefix = f"#bravo-{uuid.uuid4().hex[:8]}-"
        # Adopt existing Square objects before building the batch: a
        # stored id means update-in-place; a SKU hit means adopt.
        var_updates = {}  # vid -> real square id to force-update
        create_vars = []
        for v in variations:
            sq_id = v.get("square_catalog_object_id")
            if sq_id:
                var_updates[v["id"]] = sq_id
                continue
            hit = find_variation_by_sku(v.get("sku") or "") if v.get("sku") else None
            if hit:
                var_updates[v["id"]] = hit
                db.set_variation_square_id(v["id"], hit)
                db.log_square_sync("link", sku=v.get("sku") or "",
                                   square_object_id=hit,
                                   detail=f"adopted existing Square"
                                          f" variation for {product['name']}")
            else:
                create_vars.append(v)
        objects = []
        if create_vars or not var_updates:
            # New item (or fully new variations): one ITEM batch entry.
            payload_vars = create_vars if var_updates else variations
            objects.append(_item_payload(product, payload_vars, temp_prefix))
        # Force-update adopted/stored variations individually so the
        # ITEM id is preserved: upsert the variation objects directly.
        for v in variations:
            if v["id"] in var_updates and v not in create_vars:
                vp = _variation_payload(product, v, temp_prefix)
                vp["id"] = var_updates[v["id"]]  # real id -> update
                objects.append(vp)
        if not objects:
            report.append({"product_id": product["id"],
                           "name": product["name"], "action": "skipped",
                           "detail": "nothing to upsert"})
            continue
        try:
            data = _request("POST", "/v2/catalog/batch-upsert", {
                "idempotency_key": str(uuid.uuid4()),
                "batches": [{"objects": objects}],
            })
        except SquareError as exc:
            report.append({"product_id": product["id"],
                           "name": product["name"], "action": "error",
                           "detail": str(exc)})
            db.log_square_sync("catalog_push", result="error",
                               detail=f"{product['name']}: {exc}")
            continue
        mappings = {m.get("client_object_id"): m.get("object_id")
                    for m in (data.get("id_mappings") or [])}
        item_key = f"{temp_prefix}item-{product['id']}"
        if mappings.get(item_key):
            db.set_product_square_id(product["id"], mappings[item_key])
        for v in create_vars:
            real = mappings.get(f"{temp_prefix}var-{v['id']}")
            if real:
                db.set_variation_square_id(v["id"], real)
        created_n = len(create_vars)
        updated_n = len(var_updates)
        action = "created" if created_n else "updated"
        db.log_square_sync(
            "catalog_push", square_object_id=mappings.get(item_key, ""),
            detail=f"{product['name']}: {created_n} variation(s) created,"
                   f" {updated_n} updated")
        db.touch_square_synced("product", product["id"])
        report.append({"product_id": product["id"], "name": product["name"],
                       "action": action,
                       "detail": f"{created_n} created, {updated_n} updated"})
    return report


# ------------------------------------------------------------ count pushing


def _utc_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def push_inventory_counts(changes):
    """Push counts to Square: changes = [(square_object_id, qty, sku)].

    Uses PHYSICAL_COUNT adjustments (authoritative set). Quantities are
    Square strings; ignore_unchanged_counts keeps it cheap. Returns the
    number of changes sent. Raises SquareError on API failure.
    """
    location_id = _cfg("square_location_id")
    if not location_id:
        raise SquareError("Square location ID is not configured")
    batch = []
    for sq_id, qty, sku in changes:
        if not sq_id:
            continue
        batch.append({
            "type": "PHYSICAL_COUNT",
            "physical_count": {
                "catalog_object_id": sq_id,
                "state": "IN_STOCK",
                "location_id": location_id,
                "quantity": str(max(0, int(qty or 0))),
                "occurred_at": _utc_z(),
            },
        })
    if not batch:
        return 0
    _request("POST", "/v2/inventory/batch-change", {
        "idempotency_key": str(uuid.uuid4()),
        "changes": batch,
        "ignore_unchanged_counts": True,
    })
    for sq_id, qty, sku in changes:
        if sq_id:
            db.log_square_sync("push", sku=sku or "",
                               square_object_id=sq_id, new_qty=int(qty or 0))
    return len(batch)


def sync_order_to_square(oid):
    """Push post-payment counts for one order's lines to Square.

    Called after decrement_stock_for_order(); reads the CURRENT (already
    decremented) counts so Square ends up matching the website.
    Never raises — sync failures are logged, the order flow continues.
    Returns the number of counts pushed.
    """
    if not square_configured() or not auto_sync_enabled():
        return 0
    order = db.get_order(oid)
    if not order:
        return 0
    changes = []
    for item in order.get("line_items") or []:
        vid = item.get("variation_id")
        if not vid:
            continue
        v = db.get_variation(vid)
        if not v or not v.get("track_inventory"):
            continue
        sq_id = v.get("square_catalog_object_id")
        if not sq_id:
            continue
        changes.append((sq_id, v["inventory_qty"], v.get("sku") or ""))
        db.touch_square_synced("variation", vid)
    if not changes:
        return 0
    try:
        return push_inventory_counts(changes)
    except SquareError as exc:
        db.log_square_sync("push", result="error",
                           detail=f"order {oid}: {exc}")
        log.warning("square sync_order_to_square failed: %s", exc)
        return 0


# ------------------------------------------------------------ count pulling


def pull_inventory_counts(object_ids=None):
    """Pull Square counts and apply them locally (last-write-wins).

    object_ids defaults to every linked variation. Returns a report list
    of {sku, square_object_id, old_qty, new_qty, result}.
    """
    if not square_configured():
        raise SquareError("Square is not configured")
    if object_ids is None:
        object_ids = [sq for (_k, _i, _s, sq, _q, _l, _t)
                      in db.list_all_variations_linked() if sq]
    if not object_ids:
        return []
    data = _request("POST", "/v2/inventory/batch-retrieve-counts", {
        "catalog_object_ids": object_ids,
        "location_ids": [_cfg("square_location_id")],
    })
    by_object = {}
    for c in data.get("counts") or []:
        if (c.get("state") or "IN_STOCK") != "IN_STOCK":
            continue
        try:
            by_object[c["catalog_object_id"]] = int(c.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
    report = []
    for sq_id, qty in by_object.items():
        v = db.get_variation_by_square_id(sq_id)
        if not v:
            db.log_square_sync("pull", square_object_id=sq_id,
                               result="skipped",
                               detail="no website variation linked to this"
                                      " Square object")
            report.append({"sku": "", "square_object_id": sq_id,
                           "old_qty": None, "new_qty": None,
                           "result": "skipped"})
            continue
        old = v["inventory_qty"]
        stored = db.apply_square_count("variation", v["id"], qty, sq_id)
        report.append({"sku": v.get("sku") or "",
                       "square_object_id": sq_id, "old_qty": old,
                       "new_qty": stored,
                       "result": "clamped" if stored != qty else "ok"})
    return report


# ------------------------------------------------------- webhook handling


def handle_inventory_webhook(payload):
    """Apply an inventory.count.updated webhook payload.

    payload: parsed JSON dict. Returns (applied, skipped) counts.
    Unknown Square object ids are logged + skipped, never fatal.
    """
    counts = (((payload.get("data") or {}).get("object") or {})
              .get("inventory_counts") or [])
    applied, skipped = 0, 0
    for c in counts:
        sq_id = c.get("catalog_object_id")
        if (c.get("state") or "IN_STOCK") != "IN_STOCK":
            continue
        try:
            qty = int(c.get("quantity") or 0)
        except (TypeError, ValueError):
            skipped += 1
            continue
        v = db.get_variation_by_square_id(sq_id) if sq_id else None
        if not v:
            db.log_square_sync("webhook", square_object_id=sq_id or "",
                               result="skipped",
                               detail="count update for unlinked Square"
                                      " object")
            skipped += 1
            continue
        db.apply_square_count("variation", v["id"], qty, sq_id)
        db.touch_square_synced("variation", v["id"])
        applied += 1
    return applied, skipped
