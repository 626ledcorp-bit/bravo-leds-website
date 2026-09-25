"""Shipping helpers for the Bravo LEDs order backend.

The owner ships with Pirate Ship (USPS/UPS), which has no public API — the
integration is their "Import a Spreadsheet" flow: we export unfulfilled
orders as a CSV whose columns they map once (Pirate Ship remembers the
mapping), they buy labels in Pirate Ship, then tracking numbers come back
into /admin.

Also: carrier auto-detection from tracking-number patterns, with the public
tracking URL for each supported carrier.
"""

import csv
import io
import re

# (carrier name, compiled pattern, tracking URL template)
_CARRIERS = (
    ("UPS", re.compile(r"^1Z[0-9A-Z]{16}$", re.IGNORECASE),
     "https://www.ups.com/track?tracknum={n}"),
    ("FedEx", re.compile(r"^(\d{12}|\d{15}|96\d{20})$"),
     "https://www.fedex.com/fedextrack/?trknbr={n}"),
    ("USPS", re.compile(r"^(9\d{19}|9\d{21}|[A-Z]{2}\d{9}[A-Z]{2})$",
                        re.IGNORECASE),
     "https://tools.usps.com/go/TrackConfirmAction?tLabels={n}"),
    ("DHL", re.compile(r"^\d{10,11}$"),
     "https://www.dhl.com/us-en/home/tracking/"
     "tracking-express.html?submit=1&tracking-id={n}"),
)


def detect_carrier(tracking_number):
    """(carrier_name, tracking_url) for a tracking number, or
    ("Unknown", None) when no pattern matches."""
    n = (tracking_number or "").strip().replace(" ", "")
    for name, pattern, url in _CARRIERS:
        if pattern.match(n):
            return name, url.format(n=n)
    return "Unknown", None


# Pirate Ship "Import a Spreadsheet" columns. Pirate Ship lets the owner map
# columns on the first import and remembers the mapping, so these headers
# are descriptive rather than contractual — but they match the field names
# Pirate Ship shows in its mapper.
PIRATE_SHIP_COLUMNS = [
    "Order ID",
    "Recipient Name",
    "Company",
    "Address Line 1",
    "Address Line 2",
    "City",
    "State",
    "Zipcode",
    "Country",
    "Phone",
    "Email",
    "Weight (oz)",
    "Length (in)",
    "Width (in)",
    "Height (in)",
    "Package Contents",
    "Order Total",
]


def order_package(order, get_product, tare_oz=0.0):
    """Estimated package weight (oz) and dimensions (in) for one order.

    Weight = sum of product weight_oz x qty + tare_oz packaging allowance.
    Dimensions = the largest length/width/height seen across the order's
    products (simple heuristic for single-box shipments). Missing data ->
    0, which the owner corrects in Pirate Ship.
    """
    weight = float(tare_oz or 0)
    dims = [0.0, 0.0, 0.0]
    for item in order.get("line_items", []):
        try:
            p = get_product(item.get("product_id")) if get_product else None
        except Exception:  # noqa: BLE001 - never break an export
            p = None
        qty = max(0, int(item.get("qty", 0) or 0))
        if p:
            try:
                weight += float(p.get("weight_oz") or 0) * qty
            except (TypeError, ValueError):
                pass
            for i, key in enumerate(("length_in", "width_in", "height_in")):
                try:
                    v = float(p.get(key) or 0)
                except (TypeError, ValueError):
                    v = 0.0
                dims[i] = max(dims[i], v)
    return round(weight, 2), [round(d, 2) for d in dims]


def pirate_ship_csv(orders, get_product=None, tare_oz=0.0):
    """Build the Pirate Ship import CSV for a list of order dicts.

    One row per order. Returns the CSV text (UTF-8, with BOM so Excel
    opens it cleanly).
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PIRATE_SHIP_COLUMNS)
    writer.writeheader()
    for o in orders:
        weight, (l, w, h) = order_package(o, get_product, tare_oz)
        contents = "; ".join(
            f"{i.get('qty', 0)}x {i.get('name', '')}"
            f"{' (' + i['variation_label'] + ')' if i.get('variation_label') else ''}"
            for i in o.get("line_items", []))
        writer.writerow({
            "Order ID": o.get("id", ""),
            "Recipient Name": o.get("customer_name", "") or "",
            "Company": "",
            "Address Line 1": o.get("addr_line1", "") or "",
            "Address Line 2": o.get("addr_line2", "") or "",
            "City": o.get("addr_city", "") or "",
            "State": o.get("addr_state", "") or "",
            "Zipcode": o.get("addr_zip", "") or "",
            "Country": "US",
            "Phone": "",
            "Email": o.get("customer_email", "") or "",
            "Weight (oz)": weight,
            "Length (in)": l,
            "Width (in)": w,
            "Height (in)": h,
            "Package Contents": contents,
            "Order Total": f"{(o.get('total_cents') or 0) / 100:.2f}",
        })
    return "\ufeff" + buf.getvalue()


def parse_bulk_tracking(text):
    """Parse the bulk tracking textarea: one `orderid tracking` per line.

    Returns (entries, errors): entries = [(order_id:int, tracking:str)],
    errors = [line numbers / messages for lines that couldn't be parsed].
    """
    entries, errors = [], []
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            errors.append(f"line {lineno}: need 'order_id tracking_number'")
            continue
        try:
            oid = int(parts[0])
        except ValueError:
            errors.append(f"line {lineno}: bad order id {parts[0]!r}")
            continue
        tracking = parts[1].strip()
        if not tracking:
            errors.append(f"line {lineno}: empty tracking number")
            continue
        entries.append((oid, tracking))
    return entries, errors
