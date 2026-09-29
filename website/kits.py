"""Vehicle-specific complete interior LED kits.

Each kit is one purchasable product covering every interior bulb position for
a year/make/model: position, bulb size, quantity, and location are listed on
the kit page and in the build data.

Kits are *virtual* products: they are not rows in store.db (the store DB
lives on a persistent disk and is created at runtime), so the cart resolves
them from the committed data file website/kit_data/interior_kits.json. Prices
are server-side constants — the client can never set its own kit price.

Data notes:
- Bulb sizes come from the 2019+ union build (LASFIT per-model bulb guides
  and the owner-audited legacy scrape; see fitment/build_2019plus.py).
- Quantities are estimates based on common layouts and are marked as such
  on the kit page and in the data (estimated=true on every item).
"""
import json
import os
import re

# One price for every complete interior kit. Owner to confirm before launch.
KIT_PRICE_CENTS = 3499

KIT_VARIATION_ID = "kit-var"

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "kit_data", "interior_kits.json")

_kits = None


def _load():
    global _kits
    if _kits is None:
        try:
            with open(_DATA_PATH) as f:
                data = json.load(f)
                _kits = data.get("kits", []) if isinstance(data, dict) else data
        except (OSError, ValueError):
            _kits = []
    return _kits


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def kit_url(kit):
    return (f"/interior-kit/{kit['year']}/{slugify(kit['make'])}"
            f"/{slugify(kit['model'])}")


def get_kit(year, make, model):
    """Find a kit by year + make/model (slug or display name)."""
    sm, so = slugify(make), slugify(model)
    for k in _load():
        if (str(k.get("year")) == str(year)
                and slugify(k.get("make")) == sm
                and slugify(k.get("model")) == so):
            return k
    return None


def get_kit_by_id(kit_id):
    for k in _load():
        if k.get("kit_id") == kit_id:
            return k
    return None


def parse_kit_id(pid):
    """'kit-2022-toyota-camry' -> kit dict, or None for non-kit ids."""
    if not pid or not str(pid).startswith("kit-"):
        return None
    return get_kit_by_id(str(pid))


def kit_product_for_id(pid):
    kit = parse_kit_id(pid)
    return kit_product(kit) if kit else None


def kit_product(kit):
    """Build a product-like dict compatible with db.public_product and the
    cart pipeline (cart_detailed, cart_add, order snapshots)."""
    items = kit.get("items", [])
    total = kit.get("total_bulbs") or sum(i.get("quantity", 0) for i in items)
    name = f"{kit['year']} {kit['make']} {kit['model']} Complete Interior LED Kit"
    variation = {
        "id": KIT_VARIATION_ID,
        "label": "Complete Kit",
        "option_values": {},
        "price_cents": KIT_PRICE_CENTS,
        "effective_price_cents": KIT_PRICE_CENTS,
        "effective_compare_at_cents": None,
        "image_src": None,
        "available": True,
    }
    features = [
        f"Covers every interior position — {total} bulbs in one box",
        "Bulb sizes verified per vehicle from published bulb guides",
    ]
    if not any(i.get("estimated") for i in items):
        features.append("Quantities verified from published kit data")
    features += [
        "6000K cool white — matches modern factory lighting",
        "Plug-and-play swap for the factory interior bulbs",
        "1-year warranty on all bulbs in the kit",
    ]
    return {
        "id": kit["kit_id"],
        "name": name,
        "category": "interior",
        "tier": "Complete Kit",
        "price_cents": KIT_PRICE_CENTS,
        "sale_price_cents": None,
        "warranty": "1-year warranty",
        "image_src": kit.get("image_src", "/static/img/ph-dome.svg"),
        "interior_image_src": kit.get("interior_image_src", "/static/img/ph-dome.svg"),
        "badge": "Complete Kit",
        "blurb": (f"Every interior bulb for your {kit['year']} "
                  f"{kit['make']} {kit['model']} in one kit — map, dome, "
                  f"trunk, license plate and more. No guessing sizes."),
        "features": features,
        "images": [],
        "sizes": [],
        "color_temps": [],
        "variations": [variation],
        "variant_groups": [],
        "status": "active",
        "fitment_positions": [i["position"] for i in items],
        "kit": kit,  # full kit data for the kit page template
    }


def all_kits():
    return list(_load())
