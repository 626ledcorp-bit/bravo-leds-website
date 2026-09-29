#!/usr/bin/env python3
"""
legacy_scrape_import.py — import the owner's legacy bulb-fitment scrape into
the website's fitment schema.

Reads:  ~/workspace/user/files/BULB_SIZE_FIT_ALL_2019.xlsx
        (the owner's own old bulb-tool scrape: ~527,859 data rows,
        model years 1985-2019, 119 makes; main sheet '2019-02-23--11-42-48')
Writes: ~/workspace/626leds/fitment/legacy_scrape.db
        tables: vehicles, fitment, bulb_size_aliases, review
        (same shapes as fitment.db)

Schema notes:
  vehicles: vehicle_id, year, make, model, trim, source, setup
    - trim comes from the scrape's Submodel column.
    - setup is a read-safe EXTRA column (fitment_loader only selects the
      columns it knows): per-vehicle front-bulb setup, one of
      halogen / xenon / factory_led / mixed, or NULL when the scrape has no
      headlight rows for that vehicle. Chosen over a separate table so the
      classification travels with the vehicle row through the loader union.
  fitment: vehicle_id, position, position_raw, bulb_size_raw, bulb_size,
           flag, note, hintnote
    - position: canonical snake_case (LEGACY_POSITION_MAP below).
    - bulb_size: canonicalized with normalize.py's canon_bulb(); factory-LED
      positions keep bulb_size='LED' (the literal scrape code) with
      flag='factory_led' and a non-serviceable note so they survive the
      loader and render honestly instead of being dropped.
    - note: the scrape's Qualifier text, e.g. "(w/HID headlamps)" — this is
      what distinguishes the multiple bulb options the scrape lists for one
      position; all distinct options are kept, never merged.

Setup classification (per year/make/model/trim, headlight rows only):
  qualifier-first, bulb-code fallback —
    qualifier matches \bHID\b                    -> xenon
    qualifier matches \bLED\b                    -> factory_led
    qualifier matches \bHALOGEN\b or SEALED BEAM  -> halogen
    ("(wo/..." qualifiers never vote; word boundaries throughout, so
    "SEALED" does not match "LED")
    bulb code ^D\\d[SR] (D1S/D2S/D1R/D2R/D3S/D4S/...) -> xenon
    bulb code literally "LED"     -> factory_led
    any other bulb code           -> halogen
  unanimous -> that setup; disagreement -> mixed.
  Sealed-LED inference: a vehicle with no front-bulb rows at all whose only
  headlamp evidence is an LED-headlamp trim qualifier on other rows
  (e.g. Bi-LED Corolla trims — the scrape lists every other position but
  no front-bulb rows because the LED units are sealed) -> factory_led.

The 51 MB workbook is streamed (openpyxl read_only); nothing is loaded
whole into memory.

Never fabricates: unmapped positions and unparseable bulb codes go to the
review table with a reason; skipped rows are counted and reported.
"""

import collections
import os
import re
import sqlite3
import sys

BASE = os.path.expanduser("~/workspace/626leds/fitment")
sys.path.insert(0, BASE)
from normalize import clean_raw, canon_bulb, BULB_ALIASES  # noqa: E402

XLSX = os.path.expanduser(
    "~/workspace/user/files/BULB_SIZE_FIT_ALL_2019.xlsx")
SHEET = "2019-02-23--11-42-48"
OUT_DB = os.path.join(BASE, "legacy_scrape.db")
SOURCE = ("owner legacy bulb-tool scrape "
          "(BULB_SIZE_FIT_ALL_2019.xlsx, exported 2019-02-23)")

# Scrape "Application" -> site canonical snake_case position.
LEGACY_POSITION_MAP = {
    "Parking Light": "parking_light",
    "License Plate": "license_plate",
    "Front Turn Signal": "front_turn_signal",
    "Brake": "brake_light",
    "Rear Turn Signal": "rear_turn_signal",
    "High Beam and Low Beam Headlight": "high_low_beam",
    "Tail Light": "tail_light",
    "Front Side Marker": "front_side_marker",
    "Low Beam Headlight": "low_beam",
    "Back Up Light": "reverse_light",
    "High Beam Headlight": "high_beam",
    "Dome Light": "dome_light",
    "Courtesy": "courtesy_step",
    "Map Light": "map_light",
    "Trunk": "trunk_cargo",
    "Rear Side Marker": "rear_side_marker",
    "Front Fog Light": "fog_light",
    "Glove Box": "glove_box",
    "Center High Mount Stop Light": "center_high_mount_stop",
    "Luggage Compartment": "trunk_cargo",
    "Interior Door": "door_light",
    "Vanity Mirror Light": "vanity_mirror",
    "Stepwell": "courtesy_step",
    "Daytime Running Light": "drl",
    "Front Inner Turn Signal": "front_turn_signal",
    "Inner Tail Light": "tail_light",
    "Rear Reading Light": "reading_light",
    "Front Outer Turn Signal": "front_turn_signal",
    "Outer Tail Light": "tail_light",
    "Inner Back Up Light": "reverse_light",
    "Outer Back Up Light": "reverse_light",
    "Upper Tail Light": "tail_light",
    "Lower Tail Light": "tail_light",
    "Inner Brake": "brake_light",
    "Outer Brake": "brake_light",
    "Rear Fog Light": "fog_light_rear",
}
# Positions with no sellable bulb for an LED store (dash indicators,
# compartment novelties, etc.). Logged in review, not imported.
# QA NOTE — judgment calls, please review:
#   "Courtesy" -> courtesy_step (could arguably be door_light)
#   "Rear Fog Light" -> fog_light_rear (site has no label/category for it yet)
#   "Engine Compartment Light", "Cornering", "Roof Marker",
#   "Door Mirror Illumination Light", "Floor Console Compartment" skipped
#   (no site position/category); easy to add if the owner wants them.
SKIP_POSITIONS = {
    "Instrument Panel", "Ash Tray", "High Beam Indicator",
    "Turn Signal Indicator", "Parking Brake Indicator", "Seat Belt",
    "Automatic Transmission Indicator", "Check Engine", "Radio Display",
    "Clock Light", "Ignition Light", "Cruise Control Indicator",
    "Electronic Traction Control Indicator", "HVAC Temperature Control Bulb",
    "Engine Compartment Light", "Cornering", "Door Mirror Illumination Light",
    "Roof Marker", "Floor Console Compartment",
}

# Extra aliases used ONLY by this importer (not added to normalize.py, so the
# Sylvania crawl pipeline is untouched). Same class of deliberate decision as
# BULB_ALIASES: industry-standard equivalences for LED-replacement lookup.
LEGACY_EXTRA_ALIASES = {
    "H4": "9003",   # ECE code for HB2/9003 — same physical bulb
    "H8": "H11",    # same PGJ19 base as H11; H11 is the LED replacement size
    "H9": "H11",    # same PGJ19 base as H11; H11 is the LED replacement size
}

HEADLIGHT_APPS = {"Low Beam Headlight", "High Beam Headlight",
                  "High Beam and Low Beam Headlight"}
_XENON_RE = re.compile(r"^D\d[SR]")
_QUAL_HID = re.compile(r"\bHID\b")
_QUAL_LED = re.compile(r"\bLED\b")
_QUAL_HALOGEN = re.compile(r"\bHALOGEN\b|SEALED BEAM")
_QUAL_WITHOUT = re.compile(r"\(?\s*WO/")
_QUAL_LED_HEADLAMP = re.compile(r"\bLED\b.*HEADLAMP|HEADLAMP.*\bLED\b")
_WATT_SUFFIX = re.compile(r"-\d{2,3}W$", re.IGNORECASE)


def _clean_text(s):
    if s is None:
        return ""
    return " ".join(str(s).split())


def _setup_from_qualifier(qual):
    """Setup from the scrape's Qualifier text.

    Word-boundary matching throughout: naive substring checks misfire
    ("SEALED" contains "LED"). "(wo/..." qualifiers describe what the
    vehicle does NOT have and never vote.
    """
    if not qual:
        return None
    u = qual.upper()
    if _QUAL_WITHOUT.match(u):
        return None
    if _QUAL_HID.search(u):
        return "xenon"
    if _QUAL_LED.search(u):
        return "factory_led"
    if _QUAL_HALOGEN.search(u):
        return "halogen"
    return None


def _canon_part(part):
    """Canonicalize a bulb code, recovering unambiguous compounds.

    Returns (canonical|None, status, detail); status is ok | alias |
    factory_led | missing | review. Compound codes whose parts all resolve
    to the same canonical size (e.g. "9004/HB1" -> 9004, "H11/H8/H9" -> H11,
    "P21/5W" -> 1157) import as alias; genuinely ambiguous ones stay review.
    A trailing hyphenated wattage suffix ("H3-55W" -> H3) is stripped before
    canonicalizing; the original code is always kept as bulb_size_raw.
    """
    raw = (part or "").strip()
    base = _WATT_SUFFIX.sub("", raw)
    key = clean_raw(base)
    if key in LEGACY_EXTRA_ALIASES:
        return (LEGACY_EXTRA_ALIASES[key], "alias",
                f"{raw}->{LEGACY_EXTRA_ALIASES[key]}")
    canon, status, detail = canon_bulb(base)
    if status in ("ok", "alias", "factory_led", "missing"):
        if base != raw:
            detail = f"{raw}->{canon} (wattage suffix stripped)"
        return canon, status, detail
    noslash = re.sub(r"[\s\-_/]", "", base.upper())
    if noslash in BULB_ALIASES:
        return BULB_ALIASES[noslash], "alias", f"{raw}->{BULB_ALIASES[noslash]}"
    if noslash in LEGACY_EXTRA_ALIASES:
        return (LEGACY_EXTRA_ALIASES[noslash], "alias",
                f"{raw}->{LEGACY_EXTRA_ALIASES[noslash]}")
    if "/" in raw:
        canons = set()
        ok = True
        for piece in raw.split("/"):
            c, s, _ = canon_bulb(piece)
            if s == "factory_led" or s not in ("ok", "alias"):
                ok = False
                break
            # extra aliases apply per-piece too (H4->9003, H8/H9->H11)
            c = LEGACY_EXTRA_ALIASES.get(clean_raw(piece), c)
            canons.add(c)
        if ok and len(canons) == 1:
            only = next(iter(canons))
            return only, "alias", f"compound {raw!r}->{only}"
    return None, "review", detail


def _setup_from_part(part, canon):
    p = clean_raw(part or "")
    if not p:
        return None
    if _XENON_RE.match(p) or (canon and _XENON_RE.match(canon)):
        return "xenon"
    if p == "LED" or canon == "LED":
        return "factory_led"
    return "halogen"


def main():
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb[SHEET]

    vehicles = {}          # vehicle_id -> dict
    fit_rows = []
    review = []
    setup_votes = collections.defaultdict(set)  # vehicle_id -> {setups}
    led_trim_only = set()  # vehicle_ids whose only headlamp evidence is an
                           # LED-headlamp qualifier on non-front-bulb rows
    seen_fit = set()
    counts = collections.Counter()
    unmapped_apps = collections.Counter()

    for row in ws.iter_rows(min_row=2, values_only=True):
        counts["sheet_rows"] += 1
        year, make, model = row[0], row[1], row[2]
        if year is None or make is None or model is None:
            continue  # blank grid row past the real data
        try:
            year = int(year)
        except (TypeError, ValueError):
            counts["bad_year"] += 1
            continue
        make = _clean_text(make)
        model = _clean_text(model)
        submodel = _clean_text(row[3])
        trim = submodel or None
        qual = _clean_text(row[5])
        app = _clean_text(row[6])
        part = _clean_text(row[7]) or _clean_text(row[8])  # Part-Revised, else Part
        counts["data_rows"] += 1

        if not app:
            counts["no_application"] += 1
            continue
        canon_pos = LEGACY_POSITION_MAP.get(app)
        if canon_pos is None:
            unmapped_apps[app] += 1
            if app not in SKIP_POSITIONS:
                review.append({"kind": "unmapped_position",
                               "vehicle_id": None, "year": year,
                               "make": make, "model": model,
                               "detail": f"application={app!r}"})
            else:
                counts["skipped_position"] += 1
            continue
        if not part:
            counts["no_part"] += 1
            continue

        canon, status, detail = _canon_part(part)
        if status in ("missing", "review"):
            counts[f"bulb_{status}"] += 1
            review.append({"kind": "ambiguous_bulb", "vehicle_id": None,
                           "year": year, "make": make, "model": model,
                           "detail": f"position={app!r} {detail}"})
            continue
        # factory_led keeps the literal "LED" as its bulb_size so the row
        # survives the loader and renders with its non-serviceable note.
        bulb_size = "LED" if status == "factory_led" else canon

        vid = f"legacy:{year}:{make}:{model}:{trim or ''}"
        if vid not in vehicles:
            vehicles[vid] = {"vehicle_id": vid, "year": year, "make": make,
                             "model": model, "trim": trim, "source": SOURCE,
                             "setup": None}
        key = (vid, canon_pos, part, qual)
        if key in seen_fit:
            counts["dupes_skipped"] += 1
            continue
        seen_fit.add(key)
        note = qual or None
        if status == "factory_led":
            note = ("Sealed factory LED unit — not replaceable with an "
                    "aftermarket bulb")
        fit_rows.append({
            "vehicle_id": vid, "position": canon_pos, "position_raw": app,
            "bulb_size_raw": part, "bulb_size": bulb_size,
            "flag": status, "note": note, "hintnote": None,
        })
        counts["fitment_rows"] += 1

        if app in HEADLIGHT_APPS:
            vote = (_setup_from_qualifier(qual)
                    or _setup_from_part(part, canon))
            if vote:
                setup_votes[vid].add(vote)
        elif qual and _QUAL_LED_HEADLAMP.search(qual.upper()) \
                and not _QUAL_WITHOUT.match(qual.upper()):
            # LED-headlamp trim marker on a non-front-bulb row (e.g. the
            # Bi-LED Corolla rows): the scrape lists every other position
            # but no front-bulb rows for that trim because the LED units
            # are sealed.
            led_trim_only.add(vid)

    # per-vehicle setup classification
    setup_counts = collections.Counter()
    for vid, votes in setup_votes.items():
        setup = next(iter(votes)) if len(votes) == 1 else "mixed"
        vehicles[vid]["setup"] = setup
        setup_counts[setup] += 1
    # Sealed-LED inference: vehicles with no front-bulb rows at all whose
    # only headlamp evidence is an LED-headlamp trim qualifier. The scrape
    # lists every other position for those trims but no front-bulb rows
    # because the LED units are sealed (e.g. Bi-LED Corolla trims).
    for vid in led_trim_only:
        if vid in vehicles and not vehicles[vid]["setup"]:
            vehicles[vid]["setup"] = "factory_led"
            setup_counts["factory_led"] += 1
    setup_counts["none"] = sum(1 for v in vehicles.values()
                               if not v["setup"])

    # ---- write sqlite ----
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    cx = sqlite3.connect(OUT_DB)
    cx.execute("""CREATE TABLE vehicles(vehicle_id TEXT PRIMARY KEY, year INT,
                  make TEXT, model TEXT, trim TEXT, source TEXT, setup TEXT)""")
    cx.execute("""CREATE TABLE fitment(vehicle_id TEXT, position TEXT,
                  position_raw TEXT, bulb_size_raw TEXT, bulb_size TEXT,
                  flag TEXT, note TEXT, hintnote TEXT)""")
    cx.execute("CREATE INDEX idx_fit_vehicle ON fitment(vehicle_id)")
    cx.execute("CREATE INDEX idx_fit_bulb ON fitment(bulb_size)")
    cx.execute("""CREATE TABLE bulb_size_aliases(raw_cleaned TEXT PRIMARY KEY,
                  canonical TEXT, reason TEXT)""")
    cx.execute("""CREATE TABLE review(kind TEXT, vehicle_id TEXT, year INT,
                  make TEXT, model TEXT, detail TEXT)""")
    cx.executemany(
        "INSERT INTO vehicles VALUES(:vehicle_id,:year,:make,:model,:trim,"
        ":source,:setup)", vehicles.values())
    cx.executemany(
        "INSERT INTO fitment VALUES(:vehicle_id,:position,:position_raw,"
        ":bulb_size_raw,:bulb_size,:flag,:note,:hintnote)", fit_rows)
    cx.executemany(
        "INSERT INTO bulb_size_aliases VALUES(?,?,?)",
        [(k, v, "industry-standard equivalent trade number")
         for k, v in BULB_ALIASES.items()])
    cx.executemany(
        "INSERT INTO review VALUES(:kind,:vehicle_id,:year,:make,:model,"
        ":detail)", review)
    cx.commit()

    nv = cx.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
    nf = cx.execute("SELECT COUNT(*) FROM fitment").fetchone()[0]
    nr = cx.execute("SELECT COUNT(*) FROM review").fetchone()[0]
    yrs = cx.execute("SELECT MIN(year), MAX(year) FROM vehicles").fetchone()
    nm = cx.execute("SELECT COUNT(DISTINCT make) FROM vehicles").fetchone()[0]
    cx.close()

    print(f"vehicles={nv} fitment_rows={nf} review_items={nr}")
    print(f"year_range={yrs} makes={nm}")
    print("setup classification:", dict(setup_counts))
    print("counters:", dict(counts))
    if unmapped_apps:
        print("unmapped applications:",
              dict(unmapped_apps.most_common(10)))


if __name__ == "__main__":
    main()
