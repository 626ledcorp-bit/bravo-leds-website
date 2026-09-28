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
    return mk, mo


def load_competitor_kits():
    """-> list of dicts: make, model, years[], items[(pos, qty)], source,
    sizes{q} for diode-dynamics (pos -> size)."""
    kits = []
    for fname in ("precisionled_kits.json", "precisionled_toyota.json"):
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
                kits.append({"make": k["make"], "model": k["model"],
                             "years": expand_years(k.get("year_range")),
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


def match_model(our_models, target):
    """our_models: set of norm model names for the make/year. Returns best."""
    t = norm(target)
    if t in our_models:
        return t
    for part in re.split(r"/", target):
        if norm(part) in our_models:
            return norm(part)
    # prefix: "transit" vs "transit150250350"
    for m in our_models:
        if m.startswith(t) or t.startswith(m):
            return m
    return None


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def comp_qty_for(make_d, model_d, year, comp_kits):
    """{pos: (qty, source)} from competitor kits matching a vehicle."""
    mk, mo = norm(make_d), norm(model_d)
    out = {}
    for ck in comp_kits:
        if ck["_mk"] != mk or year not in ck["years"]:
            continue
        if not match_model({mo}, ck["model"]) and mo != ck["_mo"]:
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
            mo = match_model(models, ck["model"])
            if not mo and ck["_mo"] in models:
                mo = ck["_mo"]
            if not mo:
                continue
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

    # merge: update estimated quantities on ALL existing kits from
    # competitor data (direct match, independent of our size data)
    existing = {k["kit_id"]: k for k in
                json.load(open(os.path.join(BASE, "kit_data", "interior_kits.json")))["kits"]}
    updated = 0
    for kid, old in existing.items():
        qmap = comp_qty_for(old["make"], old["model"], old["year"], comp_kits)
        if not qmap:
            continue
        changed = False
        for i in old["items"]:
            if i["position"] in qmap and i.get("estimated"):
                qty, src = qmap[i["position"]]
                i["quantity"] = qty
                i["estimated"] = False
                i["source"] = src
                changed = True
        if changed:
            old["total_bulbs"] = sum(i["quantity"] for i in old["items"])
            old["estimated"] = any(i.get("estimated") for i in old["items"])
            old["sources"] = sorted(
                set(old.get("sources", [])) | {s for _, s in qmap.values()})
            updated += 1
    print(f"kits updated with real quantities: {updated}")

    # final: new kits + existing (existing win on ID collision already handled)
    final = dict(existing)
    added = 0
    for kid, new in built.items():
        if kid not in final:
            final[kid] = new
            added += 1
    print(f"new kits added: {added}, total: {len(final)}")

    kits = sorted(final.values(),
                  key=lambda k: (k["make"].lower(), k["model"].lower(), k["year"]))
    out = os.path.join(BASE, "kit_data", "interior_kits.json")
    json.dump({"kits": kits}, open(out, "w"), indent=1)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
