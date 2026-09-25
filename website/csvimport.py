"""Bulk CSV product import for /admin.

Two modes:
  full      - product + variation rows (create or update)
  inventory - stock-count updates only

Two-step flow: validate() parses and checks everything WITHOUT writing;
apply() writes only when validation is clean. The preview page shows a
per-row create/update/error status before anything is written.

Blank-cell rule (full mode): on UPDATE, a blank cell keeps the existing
value; on CREATE, a blank cell takes the documented default. There is no
way to clear an optional text field via CSV -- use the product form.

Price semantics: `price` is the selling price. When `compare_at_price` is
also given, `price` is the sale price and `compare_at_price` is the regular
(struck-through) price.
"""

import csv
import io
import re

import db
from catalog import CATEGORIES

MAX_CSV_BYTES = 5 * 1024 * 1024

FULL_COLUMNS = [
    "name", "sku", "msku", "barcode", "category", "product_type", "tier",
    "price", "compare_at_price", "cost", "vendor",
    "weight_oz", "length_in", "width_in", "height_in",
    "quantity", "low_threshold", "taxable", "status",
    "description", "blurb", "features", "tags",
    "seo_title", "seo_description",
    "fitment_positions", "badge", "warranty",
    "supplier_name", "supplier_sku", "supplier_notes", "notes",
    "option1_name", "option1_value",
    "option2_name", "option2_value",
    "option3_name", "option3_value",
    "variation_sku", "variation_msku", "variation_barcode",
    "variation_price", "variation_compare_at", "variation_cost",
    "variation_quantity",
]

INVENTORY_COLUMNS = ["sku", "quantity", "variation_sku"]

_VARIATION_COLS = (
    "option1_name", "option1_value", "option2_name", "option2_value",
    "option3_name", "option3_value", "variation_sku", "variation_msku",
    "variation_barcode", "variation_price", "variation_compare_at",
    "variation_cost", "variation_quantity",
)


# ------------------------------------------------------------------ parsing
def parse_csv(raw_bytes):
    """Decode + parse. Returns (rows, error); rows are {col: value} dicts
    with a _rownum key (1-based, header = 1)."""
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "File must be UTF-8 encoded CSV."
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error as e:
        return None, f"Could not parse CSV: {e}"
    if not reader.fieldnames:
        return None, "CSV has no header row."
    headers = [(h or "").strip() for h in reader.fieldnames]
    rows = []
    for i, raw in enumerate(reader):
        row = {}
        for h, v in zip(headers, [raw.get(k) for k in reader.fieldnames]):
            row[h] = (v or "").strip()
        if not any(row.values()):
            continue  # skip fully blank lines
        row["_rownum"] = i + 2
        rows.append(row)
    if not rows:
        return None, "CSV has no data rows."
    return rows, None


def check_columns(rows, allowed):
    """Reject unknown column names (typo guard). Returns error or None."""
    present = set()
    for row in rows:
        present.update(k for k in row if k != "_rownum")
    unknown = sorted(c for c in present if c not in allowed)
    if unknown:
        return ("Unknown column(s): " + ", ".join(unknown)
                + ". Check the template for exact header names.")
    return None


# ------------------------------------------------------------ value parsing
def _dollars(raw, field, errors):
    s = (raw or "").strip().replace("$", "").replace(",", "")
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
    return int(round(v * 100))


def _opt_float(raw, field, errors):
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


def _opt_int(raw, field, errors):
    s = (raw or "").strip()
    if not s:
        return None
    try:
        v = int(s)
    except ValueError:
        errors.append(f"{field} must be a whole number.")
        return None
    if v < 0:
        errors.append(f"{field} cannot be negative.")
        return None
    return v


def _opt_bool(raw, field, errors):
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s in ("yes", "y", "true", "1", "on"):
        return True
    if s in ("no", "n", "false", "0", "off"):
        return False
    errors.append(f"{field} must be yes/no (got '{raw.strip()}').")
    return None


def _split_list(raw):
    return [x.strip() for x in re.split(r"[;,]", raw or "") if x.strip()]


def _first_nonblank(rows, col):
    for r in rows:
        v = (r.get(col) or "").strip()
        if v:
            return v
    return ""


# ------------------------------------------------------------------ full
def _validate_product_group(key, rows, file_variation_skus):
    """Validate one product (all rows sharing a SKU, or a single row).
    Returns the group dict with action/errors/fields/variations."""
    gerrs = []
    sku = (rows[0].get("sku") or "").strip()
    existing = db.get_product_by_sku(sku) if sku else None
    is_new = existing is None
    action = "create" if is_new else "update"

    # Conflicting names inside the group = two products sharing one SKU.
    names = {(r.get("name") or "").strip().lower()
             for r in rows if (r.get("name") or "").strip()}
    if len(names) > 1:
        gerrs.append("Rows share SKU '%s' but have different names -- one "
                     "SKU is one product." % sku)

    name_raw = _first_nonblank(rows, "name")
    if name_raw:
        name = db.clean_product_name(name_raw, gerrs)
    elif is_new:
        gerrs.append("Name is required for new products.")
        name = ""
    else:
        name = existing["name"]

    # Category / status with create-defaults.
    cat_raw = _first_nonblank(rows, "category").lower()
    if cat_raw:
        if cat_raw not in CATEGORIES:
            gerrs.append(f"Unknown category '{cat_raw}'.")
            category = existing["category"] if existing else "led-bulbs"
        else:
            category = cat_raw
    else:
        category = "led-bulbs" if is_new else existing["category"]

    status_raw = _first_nonblank(rows, "status").lower()
    if status_raw:
        if status_raw not in db.PRODUCT_STATUSES:
            gerrs.append(f"Invalid status '{status_raw}' "
                         f"(active/draft/archived).")
            status = "draft" if is_new else existing["status"]
        else:
            status = status_raw
    else:
        status = "draft" if is_new else existing["status"]

    # Price pair semantics (see module docstring).
    price_raw = _first_nonblank(rows, "price")
    compare_raw = _first_nonblank(rows, "compare_at_price")
    price_cents = sale_cents = None
    if is_new and not price_raw:
        gerrs.append("Price is required for new products.")
    if price_raw or compare_raw:
        pv = _dollars(price_raw, "Price", gerrs) if price_raw else None
        cv = _dollars(compare_raw, "Compare-at price",
                      gerrs) if compare_raw else None
        if pv is not None and pv <= 0:
            gerrs.append("Price must be greater than zero.")
        if compare_raw and pv is not None and cv is not None:
            price_cents, sale_cents = cv, pv
            if sale_cents >= price_cents:
                gerrs.append("Sale price (price) must be below the regular "
                             "price (compare_at_price).")
        elif price_raw:
            price_cents = pv
            sale_cents = (existing["sale_price_cents"] if existing else None)
            if (sale_cents is not None and price_cents is not None
                    and sale_cents >= price_cents):
                gerrs.append("Existing sale price would not be below the new "
                             "price -- update compare_at_price too.")
        else:  # compare_at alone
            price_cents = cv
            sale_cents = existing["sale_price_cents"] if existing else None
            if (sale_cents is not None and price_cents is not None
                    and sale_cents >= price_cents):
                gerrs.append("Existing sale price would not be below the new "
                             "regular price.")
    elif existing:
        price_cents, sale_cents = (existing["price_cents"],
                                   existing["sale_price_cents"])

    cost_cents = _dollars(_first_nonblank(rows, "cost"), "Cost", gerrs)
    if cost_cents is None and existing:
        cost_cents = existing["cost_cents"]

    quantity = _opt_int(_first_nonblank(rows, "quantity"), "Quantity", gerrs)
    low_threshold = _opt_int(_first_nonblank(rows, "low_threshold"),
                             "Low threshold", gerrs)
    taxable = _opt_bool(_first_nonblank(rows, "taxable"), "Taxable", gerrs)
    if taxable is None:
        taxable = True if is_new else existing["taxable"]

    copy = {
        "blurb": _first_nonblank(rows, "blurb"),
        "description": _first_nonblank(rows, "description"),
        "seo_title": _first_nonblank(rows, "seo_title"),
        "seo_description": _first_nonblank(rows, "seo_description"),
        "features": [f.strip() for f in
                     re.split(r"\||\n", _first_nonblank(rows, "features"))
                     if f.strip()],
    }
    if existing and not any([copy["blurb"], copy["description"],
                             copy["seo_title"], copy["seo_description"],
                             copy["features"]]):
        copy = {"blurb": existing["blurb"], "description": existing["description"],
                "seo_title": existing["seo_title"],
                "seo_description": existing["seo_description"],
                "features": list(existing["features"])}
    elif existing:
        # Merge: blank CSV cells keep the existing copy.
        for k in ("blurb", "description", "seo_title", "seo_description"):
            if not copy[k]:
                copy[k] = existing[k]
        if not copy["features"]:
            copy["features"] = list(existing["features"])
    db.guard_public_copy(copy, gerrs)

    fields = {
        "name": name, "category": category, "status": status,
        "price_cents": price_cents, "sale_price_cents": sale_cents,
        "cost_cents": cost_cents, "quantity": quantity,
        "low_threshold": low_threshold, "taxable": taxable,
        "description": copy["description"], "blurb": copy["blurb"],
        "features": copy["features"],
        "seo_title": copy["seo_title"] or None,
        "seo_description": copy["seo_description"] or None,
        "product_type": _first_nonblank(rows, "product_type"),
        "tier": _first_nonblank(rows, "tier"),
        "tags": _split_list(_first_nonblank(rows, "tags")),
        "vendor": _first_nonblank(rows, "vendor"),
        "msku": _first_nonblank(rows, "msku") or None,
        "barcode": _first_nonblank(rows, "barcode") or None,
        "weight_oz": _opt_float(_first_nonblank(rows, "weight_oz"), "Weight",
                                gerrs),
        "length_in": _opt_float(_first_nonblank(rows, "length_in"), "Length",
                                gerrs),
        "width_in": _opt_float(_first_nonblank(rows, "width_in"), "Width",
                               gerrs),
        "height_in": _opt_float(_first_nonblank(rows, "height_in"), "Height",
                                gerrs),
        "fitment_positions": _split_list(
            _first_nonblank(rows, "fitment_positions")),
        "badge": _first_nonblank(rows, "badge"),
        "warranty": _first_nonblank(rows, "warranty") or "1-year warranty",
        "supplier_name": _first_nonblank(rows, "supplier_name"),
        "supplier_sku": _first_nonblank(rows, "supplier_sku"),
        "supplier_notes": _first_nonblank(rows, "supplier_notes"),
        "notes": _first_nonblank(rows, "notes"),
        "country_of_origin": _first_nonblank(rows, "country_of_origin"),
        "hs_code": _first_nonblank(rows, "hs_code"),
    }
    # Update path: blank cells keep existing values (copy fields merged above;
    # numeric Nones and empty strings both mean "not supplied").
    if existing:
        keep = {
            "product_type": existing["product_type"],
            "tier": existing["tier"], "tags": list(existing["tags"]),
            "vendor": existing["vendor"] or "",
            "msku": existing["msku"], "barcode": existing["barcode"],
            "weight_oz": existing["weight_oz"],
            "length_in": existing["length_in"],
            "width_in": existing["width_in"],
            "height_in": existing["height_in"],
            "fitment_positions": list(existing["fitment_positions"]),
            "badge": existing["badge"] or "",
            "warranty": existing["warranty"],
            "supplier_name": existing["supplier_name"] or "",
            "supplier_sku": existing["supplier_sku"] or "",
            "supplier_notes": existing["supplier_notes"] or "",
            "notes": existing["notes"] or "",
            "country_of_origin": existing["country_of_origin"] or "",
            "hs_code": existing["hs_code"] or "",
            "seo_title": existing["seo_title"],
            "seo_description": existing["seo_description"],
        }
        for k, v in keep.items():
            if fields[k] is None or fields[k] == "" or fields[k] == []:
                fields[k] = v

    # SKU uniqueness (product-level).
    if sku and db.sku_taken(sku,
                            exclude_pid=existing["id"] if existing else None):
        gerrs.append(f"SKU '{sku}' is already used by another product.")

    # ---- variations ----
    var_rows = [r for r in rows
                if any((r.get(c) or "").strip() for c in _VARIATION_COLS)]
    variations = [_validate_variation_row(
        r, existing, is_new, file_variation_skus) for r in var_rows]

    return {
        "key": sku or "#row%d" % rows[0]["_rownum"],
        "sku": sku or None,
        "row_numbers": [r["_rownum"] for r in rows],
        "action": "create" if is_new else "update",
        "name": name,
        "errors": gerrs,
        "fields": fields,
        "variations": variations,
        "existing_pid": existing["id"] if existing else None,
    }


def _validate_variation_row(row, existing, is_new, file_variation_skus):
    verrs = []
    rn = row["_rownum"]
    pairs = []
    for n in (1, 2, 3):
        oname = (row.get(f"option{n}_name") or "").strip()
        oval = (row.get(f"option{n}_value") or "").strip()
        if oval and not oname:
            verrs.append(f"Row {rn}: option{n}_value needs an option{n}_name.")
        elif oname and oval:
            pairs.append((oname, oval))
    vsku = (row.get("variation_sku") or "").strip() or None
    if vsku:
        low = vsku.lower()
        if low in file_variation_skus:
            verrs.append(f"Row {rn}: variation SKU '{vsku}' appears twice "
                         f"in this file.")
        else:
            file_variation_skus.add(low)
        other = db.get_variation_by_sku(vsku)
        if other:
            if is_new or other["product_id"] != existing["id"]:
                verrs.append(f"Row {rn}: variation SKU '{vsku}' is already "
                             f"used by another variation.")
    vprice = _dollars(row.get("variation_price"), f"Row {rn} variation price",
                      verrs)
    if vprice is not None and vprice <= 0:
        verrs.append(f"Row {rn}: variation price must be greater than zero.")
    vcompare = _dollars(row.get("variation_compare_at"),
                        f"Row {rn} variation compare-at", verrs)
    if (vprice is not None and vcompare is not None
            and vcompare <= vprice):
        verrs.append(f"Row {rn}: variation compare-at must be above the "
                     f"variation price.")
    vcost = _dollars(row.get("variation_cost"), f"Row {rn} variation cost",
                     verrs)
    vqty = _opt_int(row.get("variation_quantity"),
                    f"Row {rn} variation quantity", verrs)
    # Match an existing variation for update.
    match_vid = None
    if existing and vsku:
        hit = db.get_variation_by_sku(vsku)
        if hit and hit["product_id"] == existing["id"]:
            match_vid = hit["id"]
    if existing and match_vid is None and pairs:
        hit = db.find_variation(existing["id"], dict(pairs))
        if hit:
            match_vid = hit["id"]
    if not pairs and match_vid is None:
        # No options and no SKU match: only unambiguous on single-variation
        # products.
        if existing and len(existing.get("variations", [])) == 1:
            match_vid = existing["variations"][0]["id"]
        else:
            verrs.append(f"Row {rn}: variation needs option values or a "
                         f"matching variation_sku.")
    label = " / ".join(v for _, v in pairs) or (vsku or "Standard")
    return {
        "row": rn, "label": label,
        "action": "update" if match_vid else "create",
        "errors": verrs,
        "option_values": dict(pairs),
        "match_vid": match_vid,
        "fields": {
            "sku": vsku,
            "msku": (row.get("variation_msku") or "").strip() or None,
            "barcode": (row.get("variation_barcode") or "").strip() or None,
            "price_cents": vprice,
            "compare_at_cents": vcompare,
            "cost_cents": vcost,
            "inventory_qty": vqty,
        },
    }


def validate_full(rows):
    """Full validation, no writes. Returns the result dict for preview."""
    col_err = check_columns(rows, FULL_COLUMNS)
    result = {"mode": "full", "products": [], "errors": [], "valid": False,
              "summary": {"create": 0, "update": 0, "errors": 0}}
    if col_err:
        result["errors"].append(col_err)
        return result
    # Group rows by SKU (case-insensitive); rows without a SKU are each
    # their own product.
    groups, order = {}, []
    for r in rows:
        sku = (r.get("sku") or "").strip()
        key = sku.lower() if sku else f"#row{r['_rownum']}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    file_variation_skus = set()
    for key in order:
        g = _validate_product_group(key, groups[key], file_variation_skus)
        result["products"].append(g)
    n_err = 0
    for g in result["products"]:
        errs = len(g["errors"]) + sum(len(v["errors"])
                                      for v in g["variations"])
        if errs:
            n_err += 1
            result["summary"]["errors"] += 1
        else:
            result["summary"][g["action"]] += 1
    result["valid"] = not result["errors"] and n_err == 0
    return result


# -------------------------------------------------------------- inventory
def validate_inventory(rows):
    result = {"mode": "inventory", "rows": [], "errors": [], "valid": False,
              "summary": {"update": 0, "errors": 0}}
    col_err = check_columns(rows, INVENTORY_COLUMNS)
    if col_err:
        result["errors"].append(col_err)
        return result
    seen = set()
    for r in rows:
        rn = r["_rownum"]
        errs = []
        sku = (r.get("sku") or "").strip()
        vsku = (r.get("variation_sku") or "").strip() or None
        qty = _opt_int(r.get("quantity"), f"Row {rn} quantity", errs)
        if qty is None and not errs:
            errs.append(f"Row {rn}: quantity is required.")
        key = (sku.lower(), (vsku or "").lower())
        if key in seen:
            errs.append(f"Row {rn}: duplicate inventory row for this "
                        f"SKU in the file.")
        else:
            seen.add(key)
        target = None
        if vsku:
            v = db.get_variation_by_sku(vsku)
            if not v:
                errs.append(f"Row {rn}: variation SKU '{vsku}' not found.")
            else:
                p = db.get_product(v["product_id"], with_variations=False)
                if sku and p["sku"].lower() != sku.lower():
                    errs.append(f"Row {rn}: variation SKU '{vsku}' belongs "
                                f"to '{p['sku']}', not '{sku}'.")
                target = {"kind": "variation", "vid": v["id"],
                          "label": f"{p['name']} -- "
                                   f"{' / '.join(v['option_values'].values())}"
                                   f" ({vsku})"}
        elif sku:
            p = db.get_product_by_sku(sku)
            if not p:
                errs.append(f"Row {rn}: product SKU '{sku}' not found.")
            else:
                target = {"kind": "product", "pid": p["id"],
                          "label": f"{p['name']} ({sku})"}
        else:
            errs.append(f"Row {rn}: sku is required.")
        result["rows"].append({"row": rn, "sku": sku,
                               "variation_sku": vsku, "quantity": qty,
                               "errors": errs, "target": target})
    for r in result["rows"]:
        if r["errors"]:
            result["summary"]["errors"] += 1
        else:
            result["summary"]["update"] += 1
    result["valid"] = not result["errors"] and result["summary"]["errors"] == 0
    return result


# ------------------------------------------------------------------ entry
def validate(raw_bytes, mode):
    """Full-file validation with zero writes. Returns the result dict."""
    rows, err = parse_csv(raw_bytes)
    if err:
        return {"mode": mode, "products": [], "rows": [], "errors": [err],
                "valid": False,
                "summary": ({"create": 0, "update": 0, "errors": 0}
                            if mode == "full"
                            else {"update": 0, "errors": 0})}
    if mode == "inventory":
        return validate_inventory(rows)
    return validate_full(rows)


# ------------------------------------------------------------------- apply
def _data_from_existing(p):
    stock = db.get_stock(p["id"])
    return {
        "name": p["name"], "description": p["description"] or "",
        "product_type": p["product_type"] or "", "status": p["status"],
        "category": p["category"], "tier": p["tier"] or "",
        "tags": list(p["tags"]), "price_cents": p["price_cents"],
        "sale_price_cents": p["sale_price_cents"],
        "cost_cents": p["cost_cents"],
        "warranty": p["warranty"] or "1-year warranty",
        "badge": p["badge"] or "", "blurb": p["blurb"] or "",
        "features": list(p["features"]),
        "variant_groups": [dict(g) for g in p["variant_groups"]],
        "images": list(p["images"]),
        "fitment_positions": list(p["fitment_positions"]),
        "supplier_name": p["supplier_name"] or "",
        "supplier_sku": p["supplier_sku"] or "",
        "supplier_notes": p["supplier_notes"] or "",
        "notes": p["notes"] or "",
        "sku": p["sku"], "msku": p["msku"], "barcode": p["barcode"],
        "vendor": p["vendor"] or "", "taxable": p["taxable"],
        "weight_oz": p["weight_oz"], "length_in": p["length_in"],
        "width_in": p["width_in"], "height_in": p["height_in"],
        "country_of_origin": p["country_of_origin"] or "",
        "hs_code": p["hs_code"] or "",
        "seo_title": p["seo_title"] or "",
        "seo_description": p["seo_description"] or "",
        "stock": stock["stock"] if stock else 25,
        "low_threshold": stock["low_threshold"] if stock else 5,
    }


def apply_full(result):
    """Write a validated full-import result. Raises ValueError if invalid."""
    if not result.get("valid"):
        raise ValueError("Cannot apply: import has validation errors.")
    counts = {"created": 0, "updated": 0, "variations": 0}
    for g in result["products"]:
        fields = g["fields"]
        if g["action"] == "create":
            data = dict(fields)
            data["id"] = db.unique_product_id(fields["name"])
            data["sku"] = g["sku"]  # product SKU lives on the group, not fields
            data["variant_groups"] = []  # variations created row-by-row below
            pid = db.create_product(data)
            counts["created"] += 1
            if g["variations"]:
                # Drop create_product's placeholder variation; the CSV
                # defines the real variation set row-by-row.
                for sv in db.list_variations(pid):
                    if not sv["option_values"] and not sv["sku"]:
                        db.delete_variation(sv["id"])
        else:
            pid = g["existing_pid"]
            p = db.get_product(pid)
            data = _data_from_existing(p)
            for k, v in fields.items():
                if k in ("quantity", "low_threshold"):
                    continue
                data[k] = v
            db.update_product(pid, data)
            counts["updated"] += 1
        # Option groups: union of CSV-derived names, existing groups kept.
        groups = [dict(gg) for gg in
                  (db.get_product(pid, with_variations=False)
                   ["variant_groups"])]
        for v in g["variations"]:
            for oname, oval in v["option_values"].items():
                grp = next((x for x in groups if x["name"] == oname), None)
                if grp is None:
                    groups.append({"name": oname, "values": [oval]})
                elif oval not in grp["values"]:
                    grp["values"].append(oval)
        db.set_product_variant_groups(pid, groups)
        # Variations (row-driven; import never deletes variations).
        current = {db._variation_key(vv["option_values"]): vv
                   for vv in db.list_variations(pid)}
        for v in g["variations"]:
            f = {k2: v2 for k2, v2 in v["fields"].items()
                 if v2 is not None}
            vid = v["match_vid"]
            if vid is None and v["option_values"]:
                hit = current.get(db._variation_key(v["option_values"]))
                vid = hit["id"] if hit else None
            if vid is None and not v["option_values"]:
                singles = db.list_variations(pid)
                if len(singles) == 1:
                    vid = singles[0]["id"]
            if vid is not None:
                db.update_variation_fields(vid, f)
            else:
                db.create_variation(pid, v["option_values"], f)
            counts["variations"] += 1
        # Product-level stock.
        if fields["quantity"] is not None:
            db.set_stock(pid, fields["quantity"],
                         fields["low_threshold"]
                         if fields["low_threshold"] is not None
                         else db.get_stock(pid)["low_threshold"])
        elif fields["low_threshold"] is not None:
            db.set_low_threshold(pid, fields["low_threshold"])
    return counts


def apply_inventory(result):
    if not result.get("valid"):
        raise ValueError("Cannot apply: import has validation errors.")
    counts = {"updated": 0}
    for r in result["rows"]:
        if r["target"]["kind"] == "variation":
            db.set_variation_stock(r["target"]["vid"], r["quantity"])
        else:
            db.set_stock(r["target"]["pid"], r["quantity"])
        counts["updated"] += 1
    return counts


def apply(result):
    if result["mode"] == "inventory":
        return apply_inventory(result)
    return apply_full(result)


# ---------------------------------------------------------------- template
def template_csv(mode):
    """Headers + one example row for the given mode."""
    out = io.StringIO()
    if mode == "inventory":
        cols = INVENTORY_COLUMNS
        example = {"sku": "BRAVO-LED-H11-PREM", "quantity": "40",
                   "variation_sku": ""}
    else:
        cols = FULL_COLUMNS
        example = {
            "name": "Example LED Bulbs", "sku": "EXAMPLE-001",
            "msku": "", "barcode": "", "category": "led-bulbs",
            "product_type": "", "tier": "Premium",
            "price": "69.99", "compare_at_price": "", "cost": "22.50",
            "vendor": "Bravo", "weight_oz": "3.2", "length_in": "4",
            "width_in": "3", "height_in": "3",
            "quantity": "25", "low_threshold": "5", "taxable": "yes",
            "status": "draft",
            "description": "Example product description.",
            "blurb": "Bright, easy install.",
            "features": "Plug-and-play|6000K cool white",
            "tags": "h11, fog",
            "seo_title": "", "seo_description": "",
            "fitment_positions": "Fog", "badge": "", "warranty": "",
            "supplier_name": "", "supplier_sku": "", "supplier_notes": "",
            "notes": "",
            "option1_name": "Size", "option1_value": "H11",
            "option2_name": "Color", "option2_value": "6000K",
            "option3_name": "", "option3_value": "",
            "variation_sku": "EXAMPLE-001-H11-6K", "variation_msku": "",
            "variation_barcode": "", "variation_price": "",
            "variation_compare_at": "", "variation_cost": "",
            "variation_quantity": "25",
        }
    w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerow({c: example.get(c, "") for c in cols})
    return out.getvalue()