#!/usr/bin/env python3
"""Build the 2019+ fitment union database and vehicle-specific interior kits.

Inputs (read-only):
  legacy_scrape.db       — owner-audited owner scrape, 1985-2019
  lasfit_guides.db       — LASFIT per-model bulb guides, 2001-2026
  sealight_guides.db     — SEALIGHT vehicle API records, 2001-2024
  ../../marketplace-autoreply/lasfit_fitment.json   (setup per year|make|model)
  ../../marketplace-autoreply/sealight_fitment.json (setup per year|make|model)

Outputs (NEW files; existing databases are never modified):
  fitment_2019plus.db        — union of the three sources, 2019+ only
  interior_kits.db           — vehicle-specific complete interior LED kits
  ../website/data/interior_kits.json — committed kit data the site loads
  fitment_2019plus_build_log.md       — build log

Conflict rule: owner-audited legacy data > LASFIT > SEALIGHT. Disagreements
are kept in the `review` table of fitment_2019plus.db and never silently
resolved.

Sylvania is excluded by owner decision and is not consulted.
"""
import json
import os
import re
import sqlite3
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
LOG = [f"# fitment_2019plus build log\n\nStarted: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"]


def log(msg):
    LOG.append(msg)
    print(msg)


# ---------------------------------------------------------------- constants

INTERIOR = ["map_light", "dome_light", "glove_box", "vanity_mirror",
            "courtesy_step", "trunk_cargo", "license_plate",
            "door_light", "reading_light"]

POSITION_LABEL = {
    "map_light": "Map Light",
    "dome_light": "Dome Light",
    "glove_box": "Glove Box",
    "vanity_mirror": "Vanity Mirror",
    "courtesy_step": "Courtesy / Step",
    "trunk_cargo": "Trunk / Cargo",
    "license_plate": "License Plate",
    "door_light": "Door Light",
    "reading_light": "Reading Light",
}

LOCATION_DESC = {
    "map_light": "Front map lights (overhead console)",
    "dome_light": "Rear dome light",
    "glove_box": "Glove box light",
    "vanity_mirror": "Sun visor vanity mirror lights",
    "courtesy_step": "Door courtesy / step lights",
    "trunk_cargo": "Trunk / cargo area light",
    "license_plate": "License plate lights",
    "door_light": "Door lights",
    "reading_light": "Rear reading lights",
}

# Quantities are estimated (marked estimated=1 on every item) because the
# bulb guides publish bulb sizes per position, not counts.
QTY_DEFAULT = {
    "map_light": 2,
    "dome_light": 1,
    "glove_box": 1,
    "vanity_mirror": 2,
    "courtesy_step": 2,
    "trunk_cargo": 1,
    "license_plate": 2,
    "door_light": 2,
    "reading_light": 2,
}

MAKE_DISPLAY = {
    "acura": "Acura", "alfa romeo": "Alfa Romeo", "audi": "Audi",
    "bentley": "Bentley", "bmw": "BMW", "buick": "Buick",
    "cadillac": "Cadillac", "chevrolet": "Chevrolet", "chrysler": "Chrysler",
    "dodge": "Dodge", "ferrari": "Ferrari", "fiat": "Fiat",
    "ford": "Ford", "genesis": "Genesis", "gmc": "GMC",
    "honda": "Honda", "hyundai": "Hyundai", "infiniti": "Infiniti",
    "jaguar": "Jaguar", "jeep": "Jeep", "kia": "Kia",
    "land rover": "Land Rover", "lexus": "Lexus", "lincoln": "Lincoln",
    "maserati": "Maserati", "mazda": "Mazda", "mercedes-benz": "Mercedes-Benz",
    "mini": "MINI", "mitsubishi": "Mitsubishi", "nissan": "Nissan",
    "porsche": "Porsche", "ram": "Ram", "subaru": "Subaru",
    "tesla": "Tesla", "toyota": "Toyota", "volkswagen": "Volkswagen",
    "volvo": "Volvo",
}

# Popularity ordering for the kit list (US sales mix).
MAKE_RANK = ["toyota", "honda", "ford", "chevrolet", "nissan", "jeep",
             "hyundai", "kia", "subaru", "gmc", "ram", "dodge", "tesla",
             "mazda", "volkswagen", "bmw", "mercedes-benz", "audi",
             "lexus", "acura", "infiniti", "chrysler", "lincoln",
             "cadillac", "buick", "volvo", "porsche", "genesis",
             "land rover", "jaguar", "mini", "mitsubishi",
             "alfa romeo", "ferrari", "maserati", "bentley", "fiat"]
MAKE_ORDER = {m: i for i, m in enumerate(MAKE_RANK)}


def norm_size(s):
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper() if ch.isalnum())


def make_key(m):
    return re.sub(r"\s+", " ", str(m or "").strip().lower())


def model_key(m):
    return re.sub(r"\s+", " ", str(m or "").strip().lower())


def trim_key(t):
    t = str(t or "").strip()
    return re.sub(r"\s+", " ", t.lower()) if t and t.lower() != "none" else ""


def display_make(m):
    return MAKE_DISPLAY.get(make_key(m), str(m or "").strip().title())


def display_model(m):
    return str(m or "").strip().title()


# ---------------------------------------------------------------- sources

def load_source(db_path, year_min=2019):
    """Return (vehicles, fitment_rows) dicts for one source DB."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        vcols = [r[1] for r in con.execute("PRAGMA table_info(vehicles)")]
        has_setup = "setup" in vcols
        vsel = "vehicle_id, year, make, model, trim" + (", setup" if has_setup else "")
        vehicles = {}
        for r in con.execute(
                f"SELECT {vsel} FROM vehicles WHERE year >= ?", (year_min,)):
            d = dict(r)
            vehicles[d["vehicle_id"]] = {
                "year": int(d["year"]),
                "make": str(d["make"]).strip(),
                "model": str(d["model"]).strip(),
                "trim": (str(d["trim"]).strip()
                         if d["trim"] not in (None, "", "None") else ""),
                "setup": (str(d["setup"]).strip()
                          if has_setup and d["setup"]
                          not in (None, "", "None") else None),
            }
        fitment = []
        for r in con.execute(
                "SELECT vehicle_id, position, position_raw, bulb_size_raw,"
                " bulb_size, note, hintnote FROM fitment"):
            vid = r["vehicle_id"]
            if vid not in vehicles:
                continue
            size = norm_size(r["bulb_size"])
            if not size:
                continue
            fitment.append({
                "vehicle_id": vid,
                "position": str(r["position"]).strip().lower(),
                "position_raw": str(r["position_raw"] or "").strip(),
                "bulb_size_raw": str(r["bulb_size_raw"] or "").strip(),
                "bulb_size": size,
                "note": str(r["note"] or "").strip(),
                "hintnote": str(r["hintnote"] or "").strip(),
            })
    finally:
        con.close()
    return vehicles, fitment


def load_setup_json(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}


# ---------------------------------------------------------------- main

def main():
    t0 = time.time()
    log("## Sources\n")
    legacy_veh, legacy_fit = load_source(
        os.path.join(BASE, "legacy_scrape.db"), 2019)
    lasfit_veh, lasfit_fit = load_source(
        os.path.join(BASE, "lasfit_guides.db"), 2019)
    sealight_veh, sealight_fit = load_source(
        os.path.join(BASE, "sealight_guides.db"), 2019)
    lasfit_setup = load_setup_json(
        os.path.join(REPO, "..", "marketplace-autoreply",
                     "lasfit_fitment.json"))
    sealight_setup = load_setup_json(
        os.path.join(REPO, "..", "marketplace-autoreply",
                     "sealight_fitment.json"))
    log(f"- legacy: {len(legacy_veh)} vehicles, {len(legacy_fit)} rows")
    log(f"- lasfit: {len(lasfit_veh)} vehicles, {len(lasfit_fit)} rows")
    log(f"- sealight: {len(sealight_veh)} vehicles, {len(sealight_fit)} rows")
    log(f"- lasfit setup json: {len(lasfit_setup)} keys")
    log(f"- sealight setup json: {len(sealight_setup)} keys")

    # -- union vehicles --------------------------------------------
    # priority: legacy > lasfit > sealight
    union = {}   # (year, make_key, model_key, trim_key) -> record
    for name, vehs in (("legacy", legacy_veh), ("lasfit", lasfit_veh),
                       ("sealight", sealight_veh)):
        for vid, v in vehs.items():
            key = (v["year"], make_key(v["make"]), model_key(v["model"]),
                   trim_key(v["trim"]))
            rec = union.get(key)
            if rec is None:
                union[key] = {
                    "year": v["year"],
                    "make": display_make(v["make"]),
                    "model": display_model(v["model"]),
                    "trim": v["trim"] or "",
                    "sources": {name: vid},
                    "setups": {},
                }
            else:
                rec["sources"][name] = vid
    log(f"\n## Union vehicles: {len(union)} distinct 2019+ "
        f"(year/make/model/trim)\n")

    # -- setup with priority ---------------------------------------
    setup_conflicts = 0
    for key, rec in union.items():
        setups = {}
        if "legacy" in rec["sources"]:
            vid = rec["sources"]["legacy"]
            s = legacy_veh[vid]["setup"]
            if s:
                setups["legacy"] = s
        if "lasfit" in rec["sources"]:
            s = lasfit_setup.get(
                f"{rec['year']}|{make_key(rec['make'])}|"
                f"{model_key(rec['model'])}")
            if s and s != "unknown":
                setups["lasfit"] = s
        if "sealight" in rec["sources"]:
            s = sealight_setup.get(
                f"{rec['year']}|{make_key(rec['make'])}|"
                f"{model_key(rec['model'])}")
            if s and s != "unknown":
                setups["sealight"] = s
        vals = [setups[k] for k in ("legacy", "lasfit", "sealight")
                if k in setups]
        if not vals:
            rec["setup"] = None
        elif len(set(vals)) == 1:
            rec["setup"] = vals[0]
        else:
            rec["setup"] = "mixed"
            setup_conflicts += 1
        rec["setups"] = setups
    log(f"- setup conflicts resolved to 'mixed': {setup_conflicts}\n")

    # -- union fitment rows ----------------------------------------
    by_veh_src = {"legacy": legacy_fit, "lasfit": lasfit_fit,
                  "sealight": sealight_fit}
    # vehicle source ids -> union key lookup
    src_to_union = {}
    for name, vehs in (("legacy", legacy_veh), ("lasfit", lasfit_veh),
                       ("sealight", sealight_veh)):
        for vid, v in vehs.items():
            key = (v["year"], make_key(v["make"]), model_key(v["model"]),
                   trim_key(v["trim"]))
            src_to_union[(name, vid)] = key

    rows_by_keypos = {}   # (ukey, position) -> {source: row}
    for name, rows in by_veh_src.items():
        for r in rows:
            ukey = src_to_union.get((name, r["vehicle_id"]))
            if ukey is None:
                continue
            k = (ukey, r["position"])
            rows_by_keypos.setdefault(k, {})
            # same source, same position: prefer first row, but flag extras
            rows_by_keypos[k].setdefault(name, r)

    fit_conflicts = []
    chosen = {}  # (ukey, position) -> (source, row)
    for k, srcs in rows_by_keypos.items():
        sizes = {s: srcs[s]["bulb_size"] for s in srcs}
        if len(set(sizes.values())) == 1:
            src = next(s for s in ("legacy", "lasfit", "sealight") if s in srcs)
            chosen[k] = (src, srcs[src])
        else:
            for src in ("legacy", "lasfit", "sealight"):
                if src in srcs:
                    chosen[k] = (src, srcs[src])
                    break
            ukey, pos = k
            fit_conflicts.append(
                (ukey[0], ukey[1], ukey[2], ukey[3], pos, sizes))
    log(f"## Fitment conflicts (sources disagree on size): "
        f"{len(fit_conflicts)}\n")
    log("- recorded in review table; higher-priority source kept\n")

    # -- write fitment_2019plus.db ---------------------------------
    out_path = os.path.join(BASE, "fitment_2019plus.db")
    if os.path.exists(out_path):
        os.remove(out_path)
    con = sqlite3.connect(out_path)
    con.execute("""CREATE TABLE vehicles(
        vehicle_id TEXT PRIMARY KEY, year INT, make TEXT, model TEXT,
        trim TEXT, setup TEXT, sources TEXT)""")
    con.execute("""CREATE TABLE fitment(
        fitment_id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT,
        position TEXT, position_raw TEXT, bulb_size_raw TEXT,
        bulb_size TEXT, note TEXT, source TEXT)""")
    con.execute("""CREATE TABLE review(
        kind TEXT, vehicle_id TEXT, year INT, make TEXT, model TEXT,
        detail TEXT)""")
    con.execute("CREATE INDEX idx_fit_vehicle ON fitment(vehicle_id)")

    # aliases: copy from legacy owner scrape
    lcon = sqlite3.connect(
        f"file:{os.path.join(BASE, 'legacy_scrape.db')}?mode=ro", uri=True)
    aliases = lcon.execute(
        "SELECT raw_cleaned, canonical, reason FROM bulb_size_aliases").fetchall()
    lcon.close()
    con.execute("""CREATE TABLE bulb_size_aliases(
        alias TEXT PRIMARY KEY, canonical TEXT, note TEXT)""")
    con.executemany("INSERT INTO bulb_size_aliases VALUES (?,?,?)", aliases)
    log(f"- bulb_size_aliases copied: {len(aliases)} rows\n")

    ukeys = sorted(union.keys())
    ukey_to_vid = {}
    for i, ukey in enumerate(ukeys, 1):
        vid = f"u19-{i:05d}"
        ukey_to_vid[ukey] = vid
        rec = union[ukey]
        con.execute(
            "INSERT INTO vehicles VALUES (?,?,?,?,?,?,?)",
            (vid, rec["year"], rec["make"], rec["model"], rec["trim"],
             rec["setup"], ",".join(sorted(rec["sources"]))))
    n_fit = 0
    for (ukey, pos), (src, r) in chosen.items():
        vid = ukey_to_vid[ukey]
        note = r["note"] or r["hintnote"] or ""
        con.execute(
            "INSERT INTO fitment(vehicle_id, position, position_raw,"
            " bulb_size_raw, bulb_size, note, source)"
            " VALUES (?,?,?,?,?,?,?)",
            (vid, pos, r["position_raw"], r["bulb_size_raw"],
             r["bulb_size"], note, src))
        n_fit += 1
    for y, mk, mo, tr, pos, sizes in fit_conflicts:
        vid = ukey_to_vid.get((y, mk, mo, tr), "")
        det = (f"position={pos} sizes={sizes} kept="
               f"{chosen[((y, mk, mo, tr), pos)][1]['bulb_size']}")
        con.execute(
            "INSERT INTO review VALUES (?,?,?,?,?,?)",
            ("fitment_conflict", vid, y, mk, mo, det))
    con.commit()
    con.close()
    log(f"- fitment_2019plus.db: {len(ukeys)} vehicles, {n_fit} fitment rows\n")

    # -- interior kits ----------------------------------------------
    log("## Interior kits\n")
    # eligible: 2019+ union vehicle with granular map_light + dome_light
    kit_candidates = []
    for ukey in ukeys:
        positions = {pos: (chosen[(ukey, pos)][0],
                           chosen[(ukey, pos)][1])
                     for pos in INTERIOR if (ukey, pos) in chosen}
        if "map_light" in positions and "dome_light" in positions:
            kit_candidates.append((ukey, positions))
    log(f"- candidates with map+dome data: {len(kit_candidates)}")

    # Aggregate by (year, make, model): kits are per model, not per trim.
    # Per position, keep the most frequent bulb size across trims; ties go
    # to the higher-priority source.
    SRC_RANK = {"legacy": 0, "lasfit": 1, "sealight": 2}
    by_model = {}
    for ukey, positions in kit_candidates:
        mkey = (ukey[0], ukey[1], ukey[2])
        agg = by_model.setdefault(mkey, {})
        for pos, (src, row) in positions.items():
            agg.setdefault(pos, []).append((src, row["bulb_size"]))
    kits = []
    for (year, mk, mo), agg in by_model.items():
        positions = {}
        for pos, opts in agg.items():
            counts = {}
            best_rank = 99
            for src, size in opts:
                counts[size] = counts.get(size, 0) + 1
                best_rank = min(best_rank, SRC_RANK.get(src, 99))
            top = max(counts.values())
            cands = [s for s, c in counts.items() if c == top]
            size = sorted(
                cands,
                key=lambda s: min(SRC_RANK.get(src, 99)
                                 for src, sz in opts if sz == s))[0]
            src = next(s for s, sz in opts if sz == size)
            positions[pos] = (src, {"bulb_size": size})
        kits.append(((year, mk, mo), positions))
    kits.sort(key=lambda c: (MAKE_ORDER.get(c[0][1], 999), c[0][2], -c[0][0]))
    kits = kits[:200]
    log(f"- kits after trim aggregation (cap 200): {len(kits)}\n")

    kits_path = os.path.join(BASE, "interior_kits.db")
    if os.path.exists(kits_path):
        os.remove(kits_path)
    kcon = sqlite3.connect(kits_path)
    kcon.execute("""CREATE TABLE interior_kit(
        kit_id TEXT PRIMARY KEY, year INT, make TEXT, model TEXT,
        sources TEXT, estimated INTEGER, note TEXT)""")
    kcon.execute("""CREATE TABLE interior_kit_items(
        item_id INTEGER PRIMARY KEY AUTOINCREMENT, kit_id TEXT,
        position TEXT, position_label TEXT, bulb_size TEXT, quantity INT,
        location_desc TEXT, estimated INTEGER, source TEXT)""")
    kits_json = []
    for (ukey, positions) in kits:
        year, mk, mo = ukey[0], ukey[1], ukey[2]
        slug_make = re.sub(r"[^a-z0-9]+", "-", mk).strip("-")
        slug_model = re.sub(r"[^a-z0-9]+", "-", mo).strip("-")
        kit_id = f"kit-{year}-{slug_make}-{slug_model}"
        make_d = display_make(mk)
        model_d = display_model(mo)
        sources = sorted({src for src, _ in positions.values()})
        kcon.execute(
            "INSERT INTO interior_kit VALUES (?,?,?,?,?,?,?)",
            (kit_id, year, make_d, model_d, ",".join(sources), 1,
             "Quantities are estimates based on common layouts; bulb sizes"
             " from bulb guides."))
        items = []
        total = 0
        for pos in INTERIOR:
            if pos not in positions:
                continue
            src, row = positions[pos]
            qty = QTY_DEFAULT[pos]
            total += qty
            kcon.execute(
                "INSERT INTO interior_kit_items(kit_id, position,"
                " position_label, bulb_size, quantity, location_desc,"
                " estimated, source) VALUES (?,?,?,?,?,?,?,?)",
                (kit_id, pos, POSITION_LABEL[pos], row["bulb_size"],
                 qty, LOCATION_DESC[pos], 1, src))
            items.append({
                "position": pos,
                "position_label": POSITION_LABEL[pos],
                "bulb_size": row["bulb_size"],
                "quantity": qty,
                "location_desc": LOCATION_DESC[pos],
                "estimated": True,
                "source": src,
            })
        kits_json.append({
            "kit_id": kit_id,
            "year": year,
            "make": make_d,
            "model": model_d,
            "sources": sources,
            "estimated": True,
            "total_bulbs": total,
            "items": items,
        })
    kcon.commit()
    kcon.close()
    log(f"- interior_kits.db: {len(kits_json)} kits written\n")

    data_dir = os.path.join(REPO, "website", "data")
    os.makedirs(data_dir, exist_ok=True)
    jpath = os.path.join(data_dir, "interior_kits.json")
    json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
               "kits": kits_json}, open(jpath, "w"), indent=2)
    log(f"- website/data/interior_kits.json: {len(kits_json)} kits\n")

    log(f"\nDone in {time.time() - t0:.1f}s. Outputs:")
    log("- fitment/fitment_2019plus.db (NEW; existing DBs untouched)")
    log("- fitment/interior_kits.db (NEW)")
    log("- website/data/interior_kits.json (committed with the site)")
    log_path = os.path.join(BASE, "fitment_2019plus_build_log.md")
    open(log_path, "w").write("\n".join(LOG) + "\n")
    print(f"build log -> {log_path}")


if __name__ == "__main__":
    main()
