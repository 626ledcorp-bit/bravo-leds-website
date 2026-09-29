"""Build vehicle-specific interior LED kits from competitor quantity data
x our fitment size data.

Inputs:
  research/precisionled_kits.json (+ precisionled_toyota.json when landed)
  research/lasfit_sealight_kits.json (diode-dynamics entries: qty + size)
  fitment/fitment_live.db (bulb sizes per year/make/model/position)
  website/kit_data/interior_kits.json (existing 21 kits: sizes kept, qtys updated)

Output: website/kit_data/interior_kits.json (merged)
"""
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))          # website/
ROOT = os.path.dirname(BASE)                               # 626leds/
RESEARCH = os.path.join(ROOT, "research")
CURRENT_YEAR = 2026

sys.path.insert(0, BASE)
from fitment_loader import norm_size

# competitor location -> our position key
LOC2POS = {
    "map": "map_light",
    "dome": "dome_light",
    "courtesy": "courtesy_step",
    "courtesydoor": "courtesy_step",
    "courtesyfootwell": "courtesy_step",
    "footwell": "courtesy_step",
    "trunk": "trunk_cargo",
    "cargo": "trunk_cargo",
    "door": "door_light",
    "vanitymirror": "vanity_mirror",
    "vanity": "vanity_mirror",
    "licenseplate": "license_plate",
    "glovebox": "glove_box",
    "reading": "reading_light",
    "readings": "reading_light",
}

POS_LABEL = {
    "map_light": "Map Light", "dome_light": "Dome Light",
    "glove_box": "Glove Box", "vanity_mirror": "Vanity Mirror",
    "courtesy_step": "Courtesy / Step", "trunk_cargo": "Trunk / Cargo",
    "license_plate": "License Plate", "door_light": "Door Light",
    "reading_light": "Reading Light",
}
LOC_DESC = {
    "map_light": "Front map lights (overhead console)",
    "dome_light": "Dome light",
    "glove_box": "Glove box light",
    "vanity_mirror": "Sun visor vanity mirror lights",
    "courtesy_step": "Courtesy / step lights",
    "trunk_cargo": "Trunk / cargo area light",
    "license_plate": "License plate lights",
    "door_light": "Door lights",
    "reading_light": "Reading lights",
}
QTY_DEFAULT = {
    "map_light": 2, "dome_light": 1, "glove_box": 1, "vanity_mirror": 2,
    "courtesy_step": 2, "trunk_cargo": 1, "license_plate": 2,
    "door_light": 2, "reading_light": 2,
}
POS_ORDER = ["map_light", "dome_light", "reading_light", "vanity_mirror",
             "courtesy_step", "door_light", "glove_box", "trunk_cargo",
             "license_plate"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def loc_to_pos(loc):
    n = norm(loc).replace("lights", "").replace("light", "")
    n = re.sub(r"^(front|rear)", "", n)
    return LOC2POS.get(n)


def expand_years(year_range):
    m = re.match(r"\s*(\d{4})\s*-\s*(\d{4}|present)\s*$", str(year_range))
    if not m:
        return []
    y1 = int(m.group(1))
    y2 = CURRENT_YEAR if m.group(2) == "present" else int(m.group(2))
    if y2 < y1 or y1 < 1985:
        return []
    return list(range(y1, min(y2, CURRENT_YEAR) + 1))


def remap_make_model(make, model):
    """Competitor naming -> our DB naming."""
    mk, mo = norm(make), norm(model)
    if mk == "dodge" and mo.startswith("ram"):
        # "Ram 1500" -> make Ram, model 1500
        return "ram", norm(mo[3:] or mo)
    if mk == "mercedes":
        # PrecisionLED "Mercedes" vs our DB "Mercedes-Benz"
        return "mercedesbenz", mo
    return mk, mo


# Euro line -> trim prefix: competitor "3 Series E90" vs our DB "328i".
EURO_LINES = {
    "bmw": {"1series": "1", "2series": "2", "3series": "3",
            "5series": "5", "6series": "6", "7series": "7"},
    "mercedesbenz": {"aclass": "a", "bclass": "b", "cclass": "c",
                     "eclass": "e", "sclass": "s", "claclass": "cla",
                     "cl": "cl", "clk": "clk", "cls": "cls", "slk": "slk",
                     "sl": "sl", "ml": "ml", "gl": "gl", "glk": "glk",
                     "g": "g", "r": "r"},
}
BODY_WORDS = ("coupe", "avant", "wagon", "sedan", "convertible",
              "hatchback", "roadster", "cabrio", "cabriolet")


def euro_trims(make_norm, model_norm, our_models):
    """Expand a Euro line+chassis name to our DB trims.

    ('bmw', '3seriese90') -> ['323i','325i','328i',...] (whatever exists
    that year). Returns [] when not applicable."""
    lines = EURO_LINES.get(make_norm)
    if not lines:
        return []
    m = model_norm
    for w in BODY_WORDS:
        if m.endswith(w) and len(m) > len(w):
            m = m[: -len(w)]
    if make_norm == "bmw":
        m = re.sub(r"(e|f|g)\d{2,3}$", "", m)
    elif make_norm == "mercedesbenz":
        m = re.sub(r"(w|c|x|r|v)\d{3}$", "", m)
    prefix = lines.get(m)
    if not prefix:
        return []
    out = []
    for cand in sorted(our_models):
        if not cand.startswith(prefix):
            continue
        if prefix in ("c", "cl") and cand.startswith("clk"):
            continue  # CLK is its own line, not C-Class / CL
        if cand.startswith("m") and prefix[0].isdigit():
            continue  # skip M/Alpina models on line expansion
        out.append(cand)
    return out


def load_competitor_kits():
    """-> list of dicts: make, model, years[], items[(pos, qty)], source,
    sizes{q} for diode-dynamics (pos -> size)."""
    kits = []
    for fname in ("precisionled_kits.json", "precisionled_toyota.json",
                  "precisionled_missing_captured.json"):
        p = os.path.join(RESEARCH, fname)
        if not os.path.exists(p):
            continue
        for k in json.load(open(p)).get("kits", []):
            items = []
            for i in k.get("items", []) or []:
                pos = loc_to_pos(i.get("location"))
                if pos and i.get("quantity"):
                    items.append((pos, int(i["quantity"])))
            if items:
                # Prefer the page title's year range when it disagrees with the
                # URL slug (e.g. Prius slug says 2010-present, title says
                # 2010-2015 — the title is the tighter, trustworthy bound).
                yr = k.get("title_years") or k.get("year_range")
                kits.append({"make": k["make"], "model": k["model"],
                             "years": expand_years(yr),
                             "items": items, "source": "precisionled",
                             "sizes": {}})
    p = os.path.join(RESEARCH, "lasfit_sealight_kits.json")
    if os.path.exists(p):
        for k in json.load(open(p)).get("kits", []):
            if k.get("source") != "diode-dynamics":
                continue
            if k.get("kit_type") == "combo":
                continue
            items, sizes = [], {}
            for i in k.get("items", []) or []:
                pos = loc_to_pos(i.get("location"))
                if pos and i.get("quantity"):
                    items.append((pos, int(i["quantity"])))
                    if i.get("bulb_size"):
                        sizes[pos] = norm_size(i["bulb_size"])
            if items:
                kits.append({"make": k["make"], "model": k["model"],
                             "years": expand_years(k.get("year_range")),
                             "items": items, "source": "diode-dynamics",
                             "sizes": sizes,
                             "note": k.get("note")})
    # dedupe: same (make, model, years, items) -> keep first
    seen, out = set(), []
    for k in kits:
        key = (norm(k["make"]), norm(k["model"]),
               tuple(k["years"]), tuple(sorted(k["items"])))
        if key not in seen:
            seen.add(key)
            out.append(k)
    for k in out:
        mk, mo = remap_make_model(k["make"], k["model"])
        k["_mk"], k["_mo"] = mk, mo
    return out


def load_our_vehicles():
    """(year, make_norm, model_norm) -> {pos: most-common bulb_size}."""
    con = sqlite3.connect(f"file:{os.path.join(ROOT, 'fitment', 'fitment_live.db')}?mode=ro", uri=True)
    veh = {}
    for vid, year, make, model in con.execute(
            "SELECT vehicle_id, year, make, model FROM vehicles"):
        veh[vid] = (year, make, model, norm(make), norm(model))
    pos_size = {}
    for vid, pos, raw, size in con.execute(
            "SELECT vehicle_id, position, bulb_size_raw, bulb_size FROM fitment"):
        if pos not in POS_LABEL:
            continue
        s = norm_size(size or raw)
        if not s:
            continue
        key = veh.get(vid)
        if not key:
            continue
        k = (key[0], key[3], key[4], pos)
        d = pos_size.setdefault(k, {})
        d[s] = d.get(s, 0) + 1
    con.close()
    out = {}
    for (year, mk, mo, pos), counts in pos_size.items():
        out[(year, mk, mo, pos)] = max(counts, key=lambda s: counts[s])
    return out


def match_models(make_norm, our_models, target):
    """our_models: set of norm model names for the make/year.
    Returns a list. Exact match wins; Euro line+chassis expands to trims;
    otherwise every trim whose name starts with the competitor's line name
    (e.g. 'is' -> is250, is350). Sorted for deterministic builds."""
    t = norm(target)
    if t in our_models:
        return [t]
    euro = euro_trims(make_norm, t, our_models)
    if euro:
        return euro
    out = set()
    for part in re.split(r"/", target):
        if norm(part) in our_models:
            out.add(norm(part))
    if out:
        return sorted(out)
    # trim expansion: 'is' -> is250, is350, isf ...
    for m in sorted(our_models):
        if m.startswith(t) and m != t:
            out.add(m)
    if out:
        return sorted(out)
    # last resort: competitor name starts with our model ('civic si' -> civic)
    for m in sorted(our_models):
        if t.startswith(m):
            return [m]
    return []


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def comp_qty_for(make_d, model_d, year, comp_kits):
    """{pos: (qty, source)} from competitor kits matching a vehicle."""
    mk, mo = norm(make_d), norm(model_d)
    out = {}
    for ck in comp_kits:
        if ck["_mk"] != mk or year not in ck["years"]:
            continue
        if not match_models(mk, {mo}, ck["model"]) and mo != ck["_mo"]:
            continue
        for pos, qty in ck["items"]:
            if pos not in out or qty > out[pos][0]:
                out[pos] = (qty, ck["source"])
    return out


def main():
    comp_kits = load_competitor_kits()
    print(f"competitor kits: {len(comp_kits)}")
    our = load_our_vehicles()
    # index our vehicles by (year, make_norm) -> set(model_norm)
    idx = {}
    for (year, mk, mo, pos) in our:
        idx.setdefault((year, mk), set()).add(mo)
    # display names
    con = sqlite3.connect(f"file:{os.path.join(ROOT, 'fitment', 'fitment_live.db')}?mode=ro", uri=True)
    disp = {}
    for year, make, model in con.execute(
            "SELECT DISTINCT year, make, model FROM vehicles"):
        disp[(year, norm(make), norm(model))] = (make, model)
    con.close()

    built, unmatched = {}, []
    for ck in comp_kits:
        mk = ck["_mk"]
        for year in ck["years"]:
            models = idx.get((year, mk))
            if not models:
                continue
            mos = match_models(mk, models, ck["model"])
            if not mos and ck["_mo"] in models:
                mos = [ck["_mo"]]
            if not mos:
                continue
            for mo in mos:
                make_d, model_d = disp[(year, mk, mo)]
                items, total, srcs = [], 0, set()
                any_est = False
                comp_pos = {}
                for pos, qty in ck["items"]:
                    # merge duplicates -> max qty (base vs premium variants)
                    comp_pos[pos] = max(comp_pos.get(pos, 0), qty)
                for pos in POS_ORDER:
                    if pos in comp_pos:
                        qty = comp_pos[pos]
                        size = ck["sizes"].get(pos) or our.get((year, mk, mo, pos))
                        if not size:
                            continue
                        items.append({
                            "position": pos, "position_label": POS_LABEL[pos],
                            "bulb_size": size, "quantity": qty,
                            "location_desc": LOC_DESC[pos],
                            "estimated": False, "source": ck["source"]})
                        srcs.add(ck["source"])
                        total += qty
                    elif (year, mk, mo, pos) in our:
                        qty = QTY_DEFAULT[pos]
                        items.append({
                            "position": pos, "position_label": POS_LABEL[pos],
                            "bulb_size": our[(year, mk, mo, pos)], "quantity": qty,
                            "location_desc": LOC_DESC[pos],
                            "estimated": True, "source": "fitment-db"})
                        total += qty
                        any_est = True
                if len(items) < 2:
                    continue
                kit_id = f"kit-{year}-{slug(make_d)}-{slug(model_d)}"
                if kit_id in built and built[kit_id]["total_bulbs"] >= total:
                    continue  # keep the fuller base/premium variant
                note = ck.get("note")
                built[kit_id] = {
                    "kit_id": kit_id, "year": year, "make": make_d,
                    "model": model_d, "sources": sorted(srcs),
                    "estimated": any_est, "total_bulbs": total,
                    "items": items, **({"note": note} if note else {}),
                }
    print(f"built kits: {len(built)}")

    # merge with on-disk kits (existing win on ID collision)
    existing = {k["kit_id"]: k for k in
                json.load(open(os.path.join(BASE, "kit_data", "interior_kits.json")))["kits"]}
    final = dict(existing)
    added = 0
    for kid, new in built.items():
        if kid not in final:
            final[kid] = new
            added += 1
    print(f"new kits added: {added}, total: {len(final)}")

    # refresh: replace estimated quantities on ALL final kits (new and old)
    # from competitor data (direct match, independent of our size data).
    # Runs after the merge so fresh builds and incremental builds converge.
    updated = 0
    for kid, k in final.items():
        qmap = comp_qty_for(k["make"], k["model"], k["year"], comp_kits)
        if not qmap:
            continue
        changed = False
        for i in k["items"]:
            if i["position"] in qmap and i.get("estimated"):
                qty, src = qmap[i["position"]]
                i["quantity"] = qty
                i["estimated"] = False
                i["source"] = src
                changed = True
        if changed:
            k["total_bulbs"] = sum(i["quantity"] for i in k["items"])
            k["estimated"] = any(i.get("estimated") for i in k["items"])
            k["sources"] = sorted(
                set(k.get("sources", [])) | {s for _, s in qmap.values()})
            updated += 1
    print(f"kits updated with real quantities: {updated}")

    kits = sorted(final.values(),
                  key=lambda k: (k["make"].lower(), k["model"].lower(), k["year"]))
    out = os.path.join(BASE, "kit_data", "interior_kits.json")
    json.dump({"kits": kits}, open(out, "w"), indent=1)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
