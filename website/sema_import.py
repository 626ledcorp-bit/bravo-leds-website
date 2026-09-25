#!/usr/bin/env python3
"""SEMA Data import → website fitment database.

Reads SEMA-format fitment exports from an inbox directory and writes a
normalized SQLite DB with the SAME `vehicles` / `fitment` table shapes the
website's fitment_loader already reads — so the finder works unchanged.

Expected inbox layout (default: ~/workspace/626leds/sema/inbox/):

  aces_*.xml          ACES application data: BaseVehicleID -> manufacturer
                      part number (+ position/qualifier text when present).
                      This is the industry-standard fitment format SEMA Data
                      deals in; receivers get it via SEMA's XML/XLS exports.
  sema_fitment_*.csv  Flat-file fallback. Columns:
                      vcdb_vehicle_id (or base_vehicle_id),
                      year, make, model, trim (optional if vcdb_vehicles.csv
                      covers the IDs), position, part_number, note (optional).
  vcdb_vehicles.csv   BaseVehicleID -> year, make, model, trim mapping.
                      ACES keys vehicles by VCdb BaseVehicleID, which is just
                      a number — this file turns it back into Y/M/M(/trim).
                      Source it from your VCdb subscription export (or the
                      vehicle reference bundled in your SEMA Data download).

Part numbers → bulb sizes are resolved via ../part_number_map.json
(starter map — extend it with your real SKUs). Anything that cannot be
mapped (unknown part, unknown vehicle ID, unknown position text) goes to
the `review` table and is NEVER fabricated into the fitment tables.

Output (default: ~/workspace/626leds/sema/sema_fitment.db):

  vehicles(vehicle_id, year, make, model, trim, source,
           vcdb_vehicle_id, base_vehicle_id)
      vehicle_id is "sema:<BaseVehicleID>". vcdb_vehicle_id / base_vehicle_id
      are nullable and VCdb-ready for future ACES-native use; the existing
      crawl data has no such columns and keeps working (the loader selects
      explicit columns).
  fitment(vehicle_id, position, position_raw, bulb_size_raw, bulb_size,
          note, part_number, flag)
      bulb_size_raw holds the original manufacturer part number.
  review(kind, detail)
      unmapped_part / unmapped_vehicle / unmapped_position rows for QA.

Usage:
  python3 sema_import.py [--inbox DIR] [--out PATH] [--map PATH] [--vcdb PATH]

The import is a full refresh: tables are rebuilt on every run.
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fitment_loader import norm_size  # noqa: E402  (shared canonicalizer)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INBOX = Path(os.environ.get(
    "SEMA_DIR", BASE_DIR.parent / "sema")) / "inbox"
DEFAULT_OUT = Path(os.environ.get(
    "SEMA_DIR", BASE_DIR.parent / "sema")) / "sema_fitment.db"
DEFAULT_MAP = Path(os.environ.get(
    "SEMA_DIR", BASE_DIR.parent / "sema")) / "part_number_map.json"

SOURCE_TAG = "sema"

# Qualifier/position text -> canonical position (substring, case-insensitive,
# tried in order). Deliberately does NOT map bare "turn signal": without
# front/rear it is ambiguous and goes to the review queue instead of being
# guessed.
POSITION_KEYWORDS = [
    (("center high mount", "chmsl", "third brake", "3rd brake"), "center_high_mount_stop"),
    (("low beam",), "low_beam"),
    (("high beam",), "high_beam"),
    (("daytime running", " drl", "(drl"), "drl"),
    (("front turn", "front signal"), "front_turn_signal"),
    (("rear turn", "rear signal"), "rear_turn_signal"),
    (("front side marker",), "front_side_marker"),
    (("rear side marker",), "rear_side_marker"),
    (("fog", "driving light"), "fog_light"),
    (("license",), "license_plate"),
    (("reverse", "back-up", "backup"), "reverse_light"),
    (("brake", "stop lamp", "stop light"), "brake_light"),
    (("tail lamp", "tail light", "taillight"), "tail_light"),
    (("trunk", "cargo"), "trunk_cargo"),
    (("dome",), "dome_light"),
    (("map light",), "map_light"),
    (("glove",), "glove_box"),
    (("vanity",), "vanity_mirror"),
    (("courtesy", "puddle", "step well"), "courtesy_step"),
]


def log(msg):
    print(msg, flush=True)


def load_part_map(path):
    """Return a resolver part_number -> canonical bulb size (or None)."""
    data = json.loads(Path(path).read_text())
    exact = {str(k).upper(): v for k, v in data.get("exact", {}).items()}
    regex = [(re.compile(pat, re.IGNORECASE), repl)
             for pat, repl in data.get("regex", [])]

    def clean(pn):
        # strip pack suffixes / parentheticals: "H11ST(B2)" -> "H11ST"
        t = re.split(r"[\s\(\[]", str(pn).strip().upper())[0]
        return "".join(ch for ch in t if ch.isalnum())

    def resolve(part_number):
        key = clean(part_number)
        if not key:
            return None
        if key in exact:
            return norm_size(exact[key])
        for pat, repl in regex:
            if pat.match(key):
                return norm_size(pat.sub(repl, key))
        return None

    return resolve


def load_vcdb_map(path):
    """BaseVehicleID -> {year, make, model, trim}. Tolerates # comments."""
    mapping = {}
    if not path or not Path(path).exists():
        return mapping
    with open(path, newline="") as f:
        lines = (ln for ln in f if not ln.lstrip().startswith("#"))
        for row in csv.DictReader(lines):
            bid = (row.get("base_vehicle_id")
                   or row.get("BaseVehicleID")
                   or row.get("vcdb_vehicle_id") or "").strip()
            if not bid:
                continue
            try:
                year = int(row.get("year"))
            except (TypeError, ValueError):
                continue
            mapping[bid] = {
                "year": year,
                "make": (row.get("make") or "").strip(),
                "model": (row.get("model") or "").strip(),
                "trim": (row.get("trim") or row.get("submodel") or "").strip()
                        or None,
            }
    return mapping


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_aces(path):
    """Parse an ACES XML app file -> list of raw application dicts.

    Tolerant of the common shape variations (<ACES> vs <AcesApp> roots,
    <Part> vs <PartNumber>, <Position> text vs <Qualifiers> text).
    action="D" (delete) records are skipped.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    apps = root.findall("App") or root.findall(".//App")
    records = []
    skipped_deletes = 0
    for app in apps:
        if (app.get("action") or "A").upper() == "D":
            skipped_deletes += 1
            continue
        bv = app.find("BaseVehicle")
        base_id = (bv.get("id") if bv is not None else None) or ""
        fv = app.find("Vehicle")
        full_id = (fv.get("id") if fv is not None else None) or ""
        part_el = app.find("Part")
        if part_el is None:
            part_el = app.find("PartNumber")
        part = _text(part_el)
        pos_texts = []
        for tag in ("Position", "Qualifiers", "QualifierText"):
            el = app.find(tag)
            if el is not None and _text(el):
                pos_texts.append(_text(el))
        note = _text(app.find("Note")) or _text(app.find("FitmentNote"))
        records.append({
            "base_vehicle_id": base_id.strip(),
            "vcdb_vehicle_id": full_id.strip() or None,
            "part_number": part.strip(),
            "position_texts": pos_texts,
            "note": note,
            "source_file": Path(path).name,
        })
    return records, skipped_deletes


def parse_flat_csv(path):
    """Parse the flat CSV fallback -> raw application dicts."""
    records = []
    with open(path, newline="") as f:
        lines = (ln for ln in f if not ln.lstrip().startswith("#"))
        for row in csv.DictReader(lines):
            bid = (row.get("vcdb_vehicle_id")
                   or row.get("base_vehicle_id")
                   or row.get("BaseVehicleID") or "").strip()
            records.append({
                "base_vehicle_id": bid,
                "vcdb_vehicle_id": bid or None,
                "part_number": (row.get("part_number")
                                or row.get("Part") or "").strip(),
                "position_texts": [row.get("position") or ""],
                "note": (row.get("note") or "").strip(),
                "year": row.get("year"),
                "make": row.get("make"),
                "model": row.get("model"),
                "trim": row.get("trim"),
                "source_file": Path(path).name,
            })
    return records


def resolve_position(texts):
    """Map qualifier/position text -> canonical position (or None)."""
    for text in texts:
        low = f" {text.lower()} "
        for keywords, canonical in POSITION_KEYWORDS:
            if any(k in low for k in keywords):
                return canonical, text.strip()
    return None, " | ".join(t.strip() for t in texts if t.strip())


def run_import(inbox, out_path, map_path, vcdb_path=None):
    inbox = Path(inbox)
    resolve_part = load_part_map(map_path)
    vcdb = load_vcdb_map(vcdb_path or (inbox / "vcdb_vehicles.csv"))

    raw = []
    deletes = 0
    seen = set()
    xml_files = sorted(inbox.glob("aces_*.xml")) + sorted(inbox.glob("*.xml"))
    for xml_path in xml_files:
        key = xml_path.resolve()
        if key in seen:
            continue
        seen.add(key)
        recs, d = parse_aces(xml_path)
        raw.extend(recs)
        deletes += d
        log(f"  {xml_path.name}: {len(recs)} applications ({d} deletes skipped)")
    csv_files = (sorted(inbox.glob("sema_fitment_*.csv"))
                 + sorted(inbox.glob("*.csv")))
    for csv_path in csv_files:
        key = csv_path.resolve()
        if key in seen or csv_path.name == "vcdb_vehicles.csv":
            continue
        seen.add(key)
        recs = parse_flat_csv(csv_path)
        raw.extend(recs)
        log(f"  {csv_path.name}: {len(recs)} rows")

    vehicles = {}   # vehicle_id -> vehicle dict
    fitment = {}    # (vehicle_id, position, bulb_size) -> row (dedup)
    review = []

    for r in raw:
        bid = r["base_vehicle_id"]
        if not bid:
            review.append(("unmapped_vehicle",
                           f"empty BaseVehicleID part={r['part_number']} "
                           f"file={r['source_file']}"))
            continue
        vinfo = vcdb.get(bid)
        if vinfo is None and r.get("year") and r.get("make") and r.get("model"):
            try:
                vinfo = {"year": int(r["year"]), "make": r["make"].strip(),
                         "model": r["model"].strip(),
                         "trim": (r.get("trim") or "").strip() or None}
            except (TypeError, ValueError):
                vinfo = None
        if vinfo is None:
            review.append(("unmapped_vehicle",
                           f"BaseVehicleID={bid} part={r['part_number']} "
                           f"file={r['source_file']}"))
            continue
        size = resolve_part(r["part_number"])
        if not size:
            review.append(("unmapped_part",
                           f"part={r['part_number']} vehicle={bid} "
                           f"file={r['source_file']}"))
            continue
        position, pos_raw = resolve_position(r["position_texts"])
        if not position:
            review.append(("unmapped_position",
                           f"text={pos_raw!r} part={r['part_number']} "
                           f"vehicle={bid} file={r['source_file']}"))
            continue

        vid = f"sema:{bid}"
        if vid not in vehicles:
            vehicles[vid] = {
                "vehicle_id": vid, "year": vinfo["year"],
                "make": vinfo["make"], "model": vinfo["model"],
                "trim": vinfo["trim"], "source": SOURCE_TAG,
                "vcdb_vehicle_id": r.get("vcdb_vehicle_id") or bid,
                "base_vehicle_id": bid,
            }
        key = (vid, position, size)
        if key not in fitment:
            fitment[key] = {
                "vehicle_id": vid, "position": position,
                "position_raw": pos_raw,
                "bulb_size_raw": r["part_number"], "bulb_size": size,
                "note": r["note"], "part_number": r["part_number"],
                "flag": SOURCE_TAG,
            }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    con.execute("""CREATE TABLE vehicles(
        vehicle_id TEXT PRIMARY KEY, year INT, make TEXT, model TEXT,
        trim TEXT, source TEXT,
        vcdb_vehicle_id TEXT,      -- nullable; VCdb-ready, crawl data has NULLs
        base_vehicle_id TEXT       -- nullable; ACES BaseVehicleID
    )""")
    con.execute("""CREATE TABLE fitment(
        vehicle_id TEXT, position TEXT, position_raw TEXT,
        bulb_size_raw TEXT, bulb_size TEXT, note TEXT,
        part_number TEXT, flag TEXT
    )""")
    con.execute("CREATE INDEX idx_sema_fit_vehicle ON fitment(vehicle_id)")
    con.execute("CREATE INDEX idx_sema_fit_bulb ON fitment(bulb_size)")
    con.execute("CREATE TABLE review(kind TEXT, detail TEXT)")
    for v in vehicles.values():
        con.execute(
            "INSERT INTO vehicles VALUES (?,?,?,?,?,?,?,?)",
            (v["vehicle_id"], v["year"], v["make"], v["model"], v["trim"],
             v["source"], v["vcdb_vehicle_id"], v["base_vehicle_id"]))
    for f in fitment.values():
        con.execute(
            "INSERT INTO fitment VALUES (?,?,?,?,?,?,?,?)",
            (f["vehicle_id"], f["position"], f["position_raw"],
             f["bulb_size_raw"], f["bulb_size"], f["note"],
             f["part_number"], f["flag"]))
    for kind, detail in review:
        con.execute("INSERT INTO review VALUES (?, ?)", (kind, detail))
    con.commit()
    con.close()

    summary = {
        "vehicles": len(vehicles),
        "fitment_rows": len(fitment),
        "review_rows": len(review),
        "out": str(out_path),
    }
    log(f"done: {summary['vehicles']} vehicles, "
        f"{summary['fitment_rows']} fitment rows, "
        f"{summary['review_rows']} review rows -> {out_path}")
    return summary


def main():
    ap = argparse.ArgumentParser(
        description="Import SEMA-format fitment exports into the website DB.")
    ap.add_argument("--inbox", default=str(DEFAULT_INBOX))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--vcdb", default=None,
                    help="vcdb_vehicles.csv path (default: inbox/vcdb_vehicles.csv)")
    args = ap.parse_args()
    log(f"SEMA import: inbox={args.inbox}")
    run_import(args.inbox, args.out, args.map, args.vcdb)


if __name__ == "__main__":
    main()
