"""Square -> Bravo catalog import for /admin.

The store sometimes adds products in Square POS first. This flow pulls the
Square catalog into the website as DRAFT products (hidden until enriched and
published in /admin) — a bridge until the upload process is standardized
website-first.

Two-step flow: fetch_catalog() pulls everything from Square (no writes);
plan_import() validates and maps every item WITHOUT writing (per-item
create / skip / error for the preview); apply_import() writes only the
"create" items as drafts and stores the Square object IDs on the new
product/variation rows so the existing two-way inventory sync picks them
up immediately.

Mapping:
  Square item name        -> product name (Bravo prefix auto-applied;
                             names containing 'headlight' or 'off-road'
                             are BLOCKED as errors, never imported)
  Square variation SKU    -> variation sku (collision -> skip item, never
                             duplicate)
  Square variation price  -> variation price_cents (price_money.amount)
  Square item description -> description / blurb ('headlight' blocked)
  Square image URLs       -> product image URLs (first = primary)
  Square category name    -> website category when the name matches a
                             website category, else uncategorized (reported)
  Square inventory counts -> variation inventory_qty

No network in tests: all Square calls go through square_sync._request,
which is monkeypatched.
"""

import json
from datetime import datetime, timezone

import db
import square_sync
from catalog import CATEGORIES

OPTION_GROUP = "Variant"  # single option group used for imported variations


# ------------------------------------------------------------------ fetching


def fetch_catalog():
    """Pull the full Square catalog. Returns a JSON-serializable snapshot.

    Raises square_sync.SquareError when Square is not configured or any
    API call fails. Writes nothing.
    """
    if not square_sync.square_configured():
        raise square_sync.SquareError("Square is not configured")
    # 1. ITEM ids, paginated.
    item_ids = []
    cursor = None
    while True:
        path = "/v2/catalog/list?types=ITEM"
        if cursor:
            path += "&cursor=" + cursor
        data = square_sync._request("GET", path)
        for obj in data.get("objects") or []:
            if obj.get("type") == "ITEM" and obj.get("id"):
                item_ids.append(obj["id"])
        cursor = data.get("cursor")
        if not cursor:
            break
    # 2. Full objects + related IMAGE/CATEGORY objects.
    objects, related = [], {}
    for i in range(0, len(item_ids), 500):
        chunk = item_ids[i:i + 500]
        if not chunk:
            continue
        data = square_sync._request("POST", "/v2/catalog/batch-retrieve", {
            "object_ids": chunk,
            "include_related_objects": True,
        })
        objects.extend(data.get("objects") or [])
        for ro in data.get("related_objects") or []:
            related[ro.get("id")] = ro
    # 3. Inventory counts for every variation.
    var_ids = [v.get("id")
               for o in objects
               for v in ((o.get("item_data") or {}).get("variations") or [])
               if v.get("id")]
    counts = {}
    location_id = square_sync._cfg("square_location_id")
    for i in range(0, len(var_ids), 500):
        chunk = var_ids[i:i + 500]
        if not chunk:
            continue
        data = square_sync._request(
            "POST", "/v2/inventory/batch-retrieve-counts", {
                "catalog_object_ids": chunk,
                "location_ids": [location_id],
            })
        for c in data.get("counts") or []:
            if (c.get("state") or "IN_STOCK") != "IN_STOCK":
                continue
            try:
                counts[c["catalog_object_id"]] = max(
                    0, int(c.get("quantity") or 0))
            except (TypeError, ValueError):
                continue
    # 4. Normalize into a JSON-serializable snapshot.
    items = []
    for o in objects:
        if o.get("type") != "ITEM":
            continue
        idata = o.get("item_data") or {}
        cat_id = idata.get("category_id") or ""
        if not cat_id:
            cats = idata.get("categories") or []
            cat_id = (cats[0] or {}).get("id") if cats else ""
        cat_name = ""
        if cat_id and related.get(cat_id):
            cat_name = ((related[cat_id].get("category_data") or {})
                        .get("name") or "")
        urls = []
        for iid in idata.get("image_ids") or []:
            url = ((related.get(iid) or {}).get("image_data") or {}).get("url")
            if url:
                urls.append(url)
        variations = []
        for v in idata.get("variations") or []:
            vd = v.get("item_variation_data") or {}
            try:
                price = int((vd.get("price_money") or {}).get("amount") or 0)
            except (TypeError, ValueError):
                price = 0
            variations.append({
                "id": v.get("id"),
                "name": (vd.get("name") or "").strip(),
                "sku": (vd.get("sku") or "").strip(),
                "price_cents": max(0, price),
                "qty": counts.get(v.get("id"), 0),
            })
        items.append({
            "id": o.get("id"),
            "name": (idata.get("name") or "").strip(),
            "description": (idata.get("description") or "").strip(),
            "category_name": cat_name,
            "image_urls": urls,
            "variations": variations,
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }


# ------------------------------------------------------------------ planning


def _website_category_slug(square_name):
    """Website category slug whose display name matches Square's, or None."""
    want = (square_name or "").strip().lower()
    if not want:
        return None
    for slug, info in CATEGORIES.items():
        if (info.get("name") or "").lower() == want:
            return slug
    return None


def plan_import(snapshot):
    """Validate + map a fetched snapshot. No writes.

    Returns {"items": [...], "counts": {...}, "importable": bool}.
    Each item: square_item_id, raw_name, name, action
    (create|skip|error), reason, category (slug or ""), category_name,
    category_note, description, blurb, price_cents, images
    ([{"src","primary"}]), variations ([{id,name,sku,price_cents,qty}]).
    """
    planned = []
    seen_skus = set()
    for it in snapshot.get("items") or []:
        errors = []
        name = db.clean_product_name(it.get("name"), errors)
        description = it.get("description") or ""
        copy_errs = []
        db.guard_public_copy({"blurb": description[:160],
                              "description": description}, copy_errs)
        errors.extend(copy_errs)
        variations = it.get("variations") or []
        sku_conflict = ""
        if db.get_product_by_square_id(it.get("id")):
            sku_conflict = "already imported from Square"
        elif any(db.get_variation_by_square_id((v or {}).get("id"))
                 for v in variations):
            sku_conflict = "variations already linked to website products"
        elif not variations:
            sku_conflict = "no variations in Square"
        else:
            for v in variations:
                sku = (v.get("sku") or "").strip()
                if not sku:
                    continue
                low = sku.lower()
                if low in seen_skus:
                    sku_conflict = (f"SKU {sku} appears on more than one"
                                    f" Square item")
                    break
                if db.sku_taken(sku):
                    sku_conflict = f"SKU {sku} already exists on the website"
                    break
        for v in variations:
            sku = (v.get("sku") or "").strip().lower()
            if sku:
                seen_skus.add(sku)
        cat_slug = _website_category_slug(it.get("category_name"))
        cat_note = ""
        if it.get("category_name") and not cat_slug:
            cat_note = (f"Square category “{it['category_name']}” matches no"
                        f" website category — imported uncategorized")
        prices = [v["price_cents"] for v in variations]
        images = [{"src": u, "primary": i == 0}
                  for i, u in enumerate(it.get("image_urls") or [])]
        if errors:
            action, reason = "error", "; ".join(errors)
        elif sku_conflict:
            action, reason = "skip", sku_conflict
        else:
            action, reason = "create", ""
        planned.append({
            "square_item_id": it.get("id"),
            "raw_name": it.get("name") or "",
            "name": name,
            "action": action,
            "reason": reason,
            "category": cat_slug or "",
            "category_name": it.get("category_name") or "",
            "category_note": cat_note,
            "description": description,
            "blurb": description[:160],
            "price_cents": min(prices) if prices else 0,
            "images": images,
            "variations": variations,
        })
    counts = {"create": 0, "skip": 0, "error": 0}
    for g in planned:
        counts[g["action"]] += 1
    return {"items": planned, "counts": counts,
            "importable": counts["create"] > 0}


# ------------------------------------------------------------------ applying


def apply_import(snapshot):
    """Write all 'create' items from a re-validated plan as DRAFT products.

    Stores Square object IDs on the new rows so the two-way inventory
    sync picks them up. Returns counts
    {created, variations, skipped, errors}. Never touches 'skip'/'error'
    items. Internal fields (cost, supplier, msku, notes) are never set.
    """
    plan = plan_import(snapshot)
    counts = {"created": 0, "variations": 0, "skipped": 0, "errors": 0}
    for g in plan["items"]:
        if g["action"] != "create":
            counts["skipped" if g["action"] == "skip" else "errors"] += 1
            continue
        pid = db.create_product({
            "id": db.unique_product_id(g["name"]),
            "name": g["name"],
            "description": g["description"],
            "blurb": g["blurb"],
            "category": g["category"],
            "price_cents": g["price_cents"],
            "status": "draft",  # hidden until enriched + published in /admin
            "variant_groups": [],
            "images": g["images"],
        })
        # Drop create_product's placeholder variation; Square defines the
        # real variation set below.
        for sv in db.list_variations(pid):
            if not sv["option_values"] and not sv["sku"]:
                db.delete_variation(sv["id"])
        names = [v["name"] or f"Option {i + 1}"
                 for i, v in enumerate(g["variations"])]
        if len(names) > 1:
            db.set_product_variant_groups(
                pid, [{"name": OPTION_GROUP, "values": names}])
            opt = lambda n: {OPTION_GROUP: n}  # noqa: E731
        else:
            opt = lambda n: {}  # noqa: E731
        db.set_product_square_id(pid, g["square_item_id"])
        for i, v in enumerate(g["variations"]):
            sku = (v["sku"] or "").strip() or f"{pid}-v{i + 1}"
            vid = db.create_variation(pid, opt(names[i]), {
                "sku": sku,
                "price_cents": v["price_cents"],
                "inventory_qty": max(0, int(v["qty"] or 0)),
                "track_inventory": True,
            })
            db.set_variation_square_id(vid, v["id"])
            db.log_square_sync("import", sku=sku, square_object_id=v["id"],
                               new_qty=max(0, int(v["qty"] or 0)),
                               detail=f"imported from Square as draft:"
                                      f" {g['name']}")
            counts["variations"] += 1
        db.touch_square_synced("product", pid)
        counts["created"] += 1
    return counts


def snapshot_to_json(snapshot):
    return json.dumps(snapshot)


def snapshot_from_json(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("not a Square import snapshot")
    return data
