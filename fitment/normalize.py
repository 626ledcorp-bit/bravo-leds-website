#!/usr/bin/env python3
"""
normalize.py — build the 626 LEDs fitment database from raw Sylvania crawl data.

Reads ~/workspace/626leds/fitment/raw/car_*.json
Writes:
  fitment.db          (sqlite: vehicles, fitment, bulb_size_aliases, review)
  vehicles.csv, fitment.csv, bulb_size_aliases.csv, review.csv
  logs/distinct_use_names.json, logs/distinct_oepn.json

Never fabricates: only records what the source returned; ambiguous items go
to the review table, never silently dropped.
"""
import csv, glob, json, os, re, sqlite3, collections

BASE = os.path.expanduser("~/workspace/626leds/fitment")
RAW = os.path.join(BASE, "raw")
LOGD = os.path.join(BASE, "logs")
SOURCE = "sylvania-automotive.com bulb finder (crawled 2026-09-24)"

# ---- canonical position map: raw use_name -> snake_case ----
POSITION_MAP = {
    "Back Up Light Bulb": "reverse_light",
    "Brake Light Bulb": "brake_light",
    "Center High Mount Stop Light Bulb": "center_high_mount_stop",
    "Daytime Running Light Bulb": "drl",
    "Fog Light Bulb Front": "fog_light",
    "Fog Light Bulb": "fog_light",
    "Fog Light Bulb Rear": "fog_light_rear",
    "Glove Box Light Bulb": "glove_box",
    "Headlight Bulb High Beam": "high_beam",
    "Headlight Bulb High Beam and Low Beam": "high_low_beam",
    "Headlight Bulb Low Beam": "low_beam",
    "License Plate Light Bulb": "license_plate",
    "License Plate Light": "license_plate",
    "Side Mirror Signal Light Bulb": "side_mirror_signal",
    "Parking Light Bulb": "parking_light",
    "Side Marker Light Bulb Front": "front_side_marker",
    "Side Marker Light Bulb Rear": "rear_side_marker",
    "Tail Light Bulb": "tail_light",
    "Trunk Light Bulb": "trunk_cargo",
    "Trunk or Cargo Area Light": "trunk_cargo",
    "Turn Signal Light Bulb Front": "front_turn_signal",
    "Turn Signal Light Bulb Rear": "rear_turn_signal",
    "Dome Light Bulb": "dome_light",
    "Interior Door Light Bulb": "door_light",
    "Courtesy Light Bulb": "courtesy_step",
    "Map Light Bulb": "map_light",
    "Vanity Mirror Light Bulb": "vanity_mirror",
    "Reading Light Bulb": "reading_light",
    "Cargo Light Bulb": "trunk_cargo",
    "Stepwell Light Bulb": "courtesy_step",
    "Footwell Light Bulb": "footwell_light",
    "Sun Visor Light Bulb": "sun_visor_light",
}
# Positions with no sellable bulb for an LED store (dash indicators etc.)
SKIP_POSITIONS = {
    "Turn Signal Indicator Light Bulb",
    "High Beam Indicator Light Bulb",
    "Ash Tray Light Bulb",
}

# ---- bulb size aliases: cleaned raw -> canonical US trade number ----
# Every entry here is a deliberate, logged decision (see bulb_size_aliases table).
BULB_ALIASES = {
    # ECE HBx -> US trade numbers
    "HB1": "9004", "HB2": "9003", "HB3": "9005", "HB4": "9006",
    "HB5": "9007", "H13": "9008",
    # H10 family (9140/9145/9155 share the same base; LED replacements use H10 size)
    "9140": "H10", "9145": "H10", "9155": "H10",
    # T-15 wedge family -> 921 (standard LED replacement size)
    "912": "921", "906": "921",
    # W2.1x9.5d miniature wedge family -> 194
    "168": "194", "2825": "194", "W5W": "194",
    # 3157 dual-filament family
    "4157": "3157", "4057": "3157",
    # ECE base codes -> US numbers
    "P21W": "1156", "P215W": "1157",   # P21/5W cleans to P215W
    "PY21W": "7507",                    # amber 21W
    "HIR1": "9011", "HIR2": "9012",
}
FACTORY_LED = {"LED"}
MISSING = {"", "NA", "N/A", "NONE", "NULL"}

def clean_raw(raw):
    if raw is None:
        return ""
    return re.sub(r"[\s\-_]", "", raw.strip().upper())

def canon_bulb(raw):
    """-> (canonical|None, status, detail)
    status: ok | factory_led | missing | review | alias"""
    if raw is None:
        return None, "missing", "null oepn"
    s = clean_raw(raw)
    if s in MISSING:
        return None, "missing", f"oepn={raw!r}"
    if s in FACTORY_LED:
        return None, "factory_led", f"oepn={raw!r} (factory LED, non-serviceable)"
    if "/" in s or "\\" in s or "(" in s or "," in s:
        return None, "review", f"compound oepn={raw!r}"
    if s in BULB_ALIASES:
        return BULB_ALIASES[s], "alias", f"{s}->{BULB_ALIASES[s]}"
    if not re.fullmatch(r"[A-Z0-9]+", s):
        return None, "review", f"unexpected chars oepn={raw!r}"
    return s, "ok", ""

def main():
    os.makedirs(LOGD, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RAW, "car_*.json")))
    print(f"{len(files)} raw car files")
    vehicles = {}   # car_id -> dict
    fit_rows = []   # dicts
    review = []     # dicts
    use_names = collections.Counter()
    oepns = collections.Counter()
    seen_fit = set()
    dupes = 0

    for path in files:
        with open(path) as f:
            car = json.load(f)
        car_id = str(car["car_id"])
        vehicles[car_id] = {
            "vehicle_id": car_id, "year": car["year"], "make": car["make"],
            "model": car["model"], "trim": None, "source": SOURCE,
        }
        notes_by_use = car.get("notes", {})
        for p in car.get("positions", []):
            use_id = str(p["use_id"])
            use_name = p.get("use_name", "")
            pos_group = p.get("pos_name", "")
            use_names[use_name] += 1
            if use_name in SKIP_POSITIONS:
                continue
            canon_pos = POSITION_MAP.get(use_name)
            if canon_pos is None:
                review.append({"kind": "unmapped_position", "vehicle_id": car_id,
                               "year": car["year"], "make": car["make"], "model": car["model"],
                               "detail": f"use_name={use_name!r} group={pos_group!r}"})
                continue
            for n in notes_by_use.get(use_id, []) or []:
                oepn = n.get("oepn")
                oepns[(oepn or "")] += 1
                canon, status, detail = canon_bulb(oepn)
                if status == "review":
                    review.append({"kind": "ambiguous_bulb", "vehicle_id": car_id,
                                   "year": car["year"], "make": car["make"], "model": car["model"],
                                   "detail": f"position={use_name!r} {detail}"})
                    continue
                key = (car_id, canon_pos, canon, status, (n.get("note") or "").strip())
                if key in seen_fit:
                    dupes += 1
                    continue
                seen_fit.add(key)
                fit_rows.append({
                    "vehicle_id": car_id, "position": canon_pos,
                    "position_raw": use_name,
                    "bulb_size_raw": oepn, "bulb_size": canon,
                    "flag": status,  # ok | alias | factory_led | missing
                    "note": (n.get("note") or "").strip() or None,
                    "hintnote": (n.get("hintnote") or "").strip() or None,
                })
        # positions with zero notes entries -> flag
        pos_use_ids = {str(p["use_id"]) for p in car.get("positions", [])}
        for uid in pos_use_ids - set(notes_by_use.keys()):
            review.append({"kind": "missing_notes", "vehicle_id": car_id,
                           "year": car["year"], "make": car["make"], "model": car["model"],
                           "detail": f"use_id={uid} returned no notes payload"})

    # distinct-value audit logs
    with open(os.path.join(LOGD, "distinct_use_names.json"), "w") as f:
        json.dump(use_names.most_common(), f, indent=1)
    with open(os.path.join(LOGD, "distinct_oepn.json"), "w") as f:
        json.dump(oepns.most_common(), f, indent=1)

    # ---- write sqlite ----
    db = os.path.join(BASE, "fitment.db")
    if os.path.exists(db):
        os.remove(db)
    cx = sqlite3.connect(db)
    cx.execute("""CREATE TABLE vehicles(vehicle_id TEXT PRIMARY KEY, year INT, make TEXT,
                  model TEXT, trim TEXT, source TEXT)""")
    cx.execute("""CREATE TABLE fitment(vehicle_id TEXT, position TEXT, position_raw TEXT,
                  bulb_size_raw TEXT, bulb_size TEXT, flag TEXT, note TEXT, hintnote TEXT)""")
    cx.execute("CREATE INDEX idx_fit_vehicle ON fitment(vehicle_id)")
    cx.execute("CREATE INDEX idx_fit_bulb ON fitment(bulb_size)")
    cx.execute("""CREATE TABLE bulb_size_aliases(raw_cleaned TEXT PRIMARY KEY,
                  canonical TEXT, reason TEXT)""")
    cx.execute("""CREATE TABLE review(kind TEXT, vehicle_id TEXT, year INT, make TEXT,
                  model TEXT, detail TEXT)""")
    cx.executemany(
        "INSERT INTO vehicles VALUES(:vehicle_id,:year,:make,:model,:trim,:source)",
        vehicles.values())
    cx.executemany(
        "INSERT INTO fitment VALUES(:vehicle_id,:position,:position_raw,:bulb_size_raw,"
        ":bulb_size,:flag,:note,:hintnote)", fit_rows)
    cx.executemany(
        "INSERT INTO bulb_size_aliases VALUES(?,?,?)",
        [(k, v, "industry-standard equivalent trade number") for k, v in BULB_ALIASES.items()])
    cx.executemany(
        "INSERT INTO review VALUES(:kind,:vehicle_id,:year,:make,:model,:detail)", review)
    cx.commit()

    # ---- CSVs ----
    def write_csv(name, rows, fields):
        with open(os.path.join(BASE, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
    write_csv("vehicles.csv", vehicles.values(),
              ["vehicle_id", "year", "make", "model", "trim", "source"])
    write_csv("fitment.csv", fit_rows,
              ["vehicle_id", "position", "position_raw", "bulb_size_raw",
               "bulb_size", "flag", "note", "hintnote"])
    write_csv("bulb_size_aliases.csv",
              [{"raw_cleaned": k, "canonical": v,
                "reason": "industry-standard equivalent trade number"}
               for k, v in BULB_ALIASES.items()],
              ["raw_cleaned", "canonical", "reason"])
    write_csv("review.csv", review,
              ["kind", "vehicle_id", "year", "make", "model", "detail"])

    # ---- summary ----
    nv = cx.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
    nf = cx.execute("SELECT COUNT(*) FROM fitment").fetchone()[0]
    nr = cx.execute("SELECT COUNT(*) FROM review").fetchone()[0]
    yrs = cx.execute("SELECT MIN(year), MAX(year) FROM vehicles").fetchone()
    nm = cx.execute("SELECT COUNT(DISTINCT make) FROM vehicles").fetchone()[0]
    print(f"vehicles={nv} fitment_rows={nf} review_items={nr} dupes_skipped={dupes}")
    print(f"year_range={yrs} makes={nm}")
    print("top bulb sizes:")
    for size, c in cx.execute(
            "SELECT bulb_size, COUNT(*) FROM fitment WHERE bulb_size IS NOT NULL "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"):
        print(f"  {size}: {c}")
    print("positions:")
    for pos, c in cx.execute(
            "SELECT position, COUNT(*) FROM fitment GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {pos}: {c}")
    print("flags:")
    for fl, c in cx.execute("SELECT flag, COUNT(*) FROM fitment GROUP BY 1"):
        print(f"  {fl}: {c}")
    print("review kinds:")
    for k, c in cx.execute("SELECT kind, COUNT(*) FROM review GROUP BY 1"):
        print(f"  {k}: {c}")
    cx.close()

if __name__ == "__main__":
    main()
