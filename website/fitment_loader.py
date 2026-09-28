"""Fitment data loader — single swap point between seed data and live DBs.

Data sources, checked in priority order:

  1. SEMA Data import (``SEMA_DIR`` env var, default ``../sema``):
     ``sema_fitment.db`` (written by sema_import.py), else ``fitment.db`` /
     ``fitment.json`` / ``vehicles.csv`` + ``fitment.csv`` in that dir.
     Preferred over crawl data when present.
  2. Sylvania crawl output (``FITMENT_DIR`` env var, default ``../fitment``):
     ``fitment.json``, ``fitment.db`` / ``fitment.sqlite``,
     ``vehicles.csv`` + ``fitment.csv`` — unioned UNDER it with the legacy
     owner scrape (``legacy_scrape.db``, same dir). On a vehicle+position
     conflict the crawl row wins (2026 snapshot); the scrape fills the
     119-make / 1985-2019 gaps the crawl lacks.
  3. Seed data (``seed_fitment.py``) — demo fallback.

Accepted shapes per directory:
  1. fitment.json   — {"vehicles": [...], "fitment": [...]}
  2. fitment.db / fitment.sqlite / sema_fitment.db — tables: vehicles, fitment
  3. vehicles.csv + fitment.csv  — headers matching the schema below

Schema (both tables/files):
  vehicles: vehicle_id, year, make, model, trim
            (+ optional vcdb_vehicle_id, base_vehicle_id — read-safe)
            (+ optional setup — read-safe; per-vehicle front-bulb setup
             halogen/xenon/factory_led/mixed from the legacy owner scrape)
  fitment:  vehicle_id, position, bulb_size_raw, bulb_size, note
            (+ optional extras — read-safe)

If no valid live data is found, it falls back to seed_fitment.py (demo data).

Public interface (stable — templates and routes only use these):
  get_years()                      -> [2020, 2019, ...]
  get_makes(year)                  -> ["Ford", "Toyota", ...]
  get_models(year, make)           -> ["Camry", ...]
  get_trims(year, make, model)     -> ["LE", ...] (may be [])
  get_fitment(year, make, model, trim=None)
      -> [{"position", "bulb_size_raw", "bulb_size", "note"}]
  get_vehicle_setup(year, make, model, trim=None)
      -> "halogen" | "xenon" | "factory_led" | "mixed" | None
      (front-bulb setup from the legacy owner scrape; None when unknown)
  source_info() -> {"source": "live"|"seed", "detail": str,
                    "vehicles": int, "rows": int}
"""

import csv
import json
import os
import re
import sqlite3
from pathlib import Path

from seed_fitment import SEED_VEHICLES

FITMENT_DIR = Path(os.environ.get(
    "FITMENT_DIR",
    Path(__file__).resolve().parent.parent / "fitment",
))
SEMA_DIR = Path(os.environ.get(
    "SEMA_DIR",
    Path(__file__).resolve().parent.parent / "sema",
))


def _fitment_dir():
    """Resolve at call time so env changes (and tests) take effect."""
    return Path(os.environ.get("FITMENT_DIR", FITMENT_DIR))


def _sema_dir():
    return Path(os.environ.get("SEMA_DIR", SEMA_DIR))

POSITION_LABELS = {
    "low_beam": "Low Beam",
    "high_beam": "High Beam",
    "high_low_beam": "High / Low Beam",
    # SEALIGHT-sourced rows use a combined "headlight" position for low+high;
    # never render the word "headlight" (brand rule).
    "headlight": "High / Low Beam",
    "fog_light": "Fog Light",
    "drl": "Daytime Running Light",
    "front_turn_signal": "Front Turn Signal",
    "rear_turn_signal": "Rear Turn Signal",
    "front_side_marker": "Front Side Marker",
    "rear_side_marker": "Rear Side Marker",
    "brake_light": "Brake Light",
    "tail_light": "Tail Light",
    "center_high_mount_stop": "3rd Brake Light",
    "reverse_light": "Reverse / Backup",
    "license_plate": "License Plate",
    "trunk_cargo": "Trunk / Cargo",
    "dome_light": "Dome / Map Light",
    "map_light": "Map Light",
    "glove_box": "Glove Box",
    "vanity_mirror": "Vanity Mirror",
    "courtesy_step": "Courtesy / Step",
}

# Which product categories serve each bulb position on the results page.
# Track 2: dome/map positions now point at the LED Miniature Bulbs category;
# low/high beam and fog also surface HID options. Dash cams and jump
# starters are universal-fit and deliberately appear in NO position mapping,
# so the finder can never suggest them.
POSITION_CATEGORIES = {
    "low_beam": ["led-bulbs", "hid-conversion-kits", "factory-hid-bulbs"],
    "high_beam": ["led-bulbs", "hid-conversion-kits", "factory-hid-bulbs"],
    "high_low_beam": ["led-bulbs", "hid-conversion-kits", "factory-hid-bulbs"],
    "headlight": ["led-bulbs", "hid-conversion-kits", "factory-hid-bulbs"],
    "fog_light": ["fog", "hid-conversion-kits"],
    "drl": ["led-bulbs"],
    "front_turn_signal": ["turn"],
    "rear_turn_signal": ["turn"],
    "front_side_marker": ["turn"],
    "rear_side_marker": ["turn"],
    "brake_light": ["brake"],
    "tail_light": ["brake"],
    "center_high_mount_stop": ["brake"],
    "reverse_light": ["reverse"],
    "license_plate": ["interior"],
    "trunk_cargo": ["interior"],
    "dome_light": ["led-miniature-bulbs"],
    "map_light": ["led-miniature-bulbs"],
    "glove_box": ["interior"],
    "vanity_mirror": ["interior"],
    "courtesy_step": ["interior"],
}


# Which of the three vehicle category pages a position belongs to.
POSITION_GROUP = {
    # Forward Lighting
    "low_beam": "forward", "high_beam": "forward",
    "high_low_beam": "forward", "headlight": "forward",
    "fog_light": "forward", "fog_light_rear": "forward",
    "drl": "forward", "front_turn_signal": "forward",
    "front_side_marker": "forward", "parking_light": "forward",
    "turn_signal": "forward",
    # Exterior Rear Lighting
    "rear_turn_signal": "rear", "rear_side_marker": "rear",
    "brake_light": "rear", "tail_light": "rear",
    "center_high_mount_stop": "rear", "reverse_light": "rear",
    "license_plate": "rear",
    # Interior Lighting
    "dome_light": "interior", "map_light": "interior",
    "glove_box": "interior", "vanity_mirror": "interior",
    "courtesy_step": "interior", "trunk_cargo": "interior",
    "door_light": "interior", "reading_light": "interior",
    "interior": "interior",
}

FIT_GROUPS = ("forward", "rear", "interior")
FIT_GROUP_LABELS = {
    "forward": "Forward Lighting",
    "rear": "Exterior Rear Lighting",
    "interior": "Interior Lighting",
}
FIT_GROUP_DESCS = {
    "forward": "Low and high beams, fog lights, turn signals, side markers and daytime running lights.",
    "rear": "Tail lights, brake lights, reverse lights and license plate lights.",
    "interior": "Dome, map, trunk and courtesy lights — or the complete kit for your vehicle.",
}


def norm_size(s):
    """Canonicalize a bulb size for matching: 'H-11' -> 'H11'."""
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper() if ch.isalnum())


def _load_json(path):
    data = json.loads(path.read_text())
    vehicles = data.get("vehicles", [])
    fitment = data.get("fitment", [])
    if not vehicles or not fitment:
        return None
    return _normalize(vehicles, fitment)


def _load_sqlite(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        vcols = [r[1] for r in con.execute("PRAGMA table_info(vehicles)")]
        vselect = "vehicle_id, year, make, model, trim"
        if "setup" in vcols:  # legacy owner scrape: read-safe extra column
            vselect += ", setup"
        vehicles = [dict(r) for r in con.execute(
            f"SELECT {vselect} FROM vehicles")]
        fitment = [dict(r) for r in con.execute(
            "SELECT vehicle_id, position, bulb_size_raw, bulb_size, note "
            "FROM fitment")]
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if not vehicles or not fitment:
        return None
    return _normalize(vehicles, fitment)


def _load_csv(vpath, fpath):
    with open(vpath, newline="") as f:
        vehicles = list(csv.DictReader(f))
    with open(fpath, newline="") as f:
        fitment = list(csv.DictReader(f))
    if not vehicles or not fitment:
        return None
    return _normalize(vehicles, fitment)


def _normalize(vehicles, fitment):
    """Normalize any source shape into (vehicles, by_key) structures."""
    vehs = []
    by_id = {}
    for v in vehicles:
        rec = {
            "vehicle_id": str(v.get("vehicle_id")),
            "year": int(v.get("year")),
            "make": str(v.get("make")).strip(),
            "model": str(v.get("model")).strip(),
            "trim": (str(v.get("trim")).strip()
                     if v.get("trim") not in (None, "", "None") else None),
            "setup": (str(v.get("setup")).strip()
                      if v.get("setup") not in (None, "", "None") else None),
        }
        vehs.append(rec)
        by_id[rec["vehicle_id"]] = rec
    by_key = {}
    for f in fitment:
        veh = by_id.get(str(f.get("vehicle_id")))
        if veh is None:
            continue
        key = (veh["year"], veh["make"], veh["model"], veh["trim"] or "")
        row = {
            "position": str(f.get("position")).strip().lower(),
            "bulb_size_raw": str(f.get("bulb_size_raw")
                                 or f.get("bulb_size") or "").strip(),
            "bulb_size": norm_size(f.get("bulb_size")
                                   or f.get("bulb_size_raw") or ""),
            "note": str(f.get("note") or "").strip(),
        }
        if not row["bulb_size"]:
            continue
        by_key.setdefault(key, []).append(row)
    # drop vehicles with no usable rows
    vehs = [v for v in vehs
            if (v["year"], v["make"], v["model"], v["trim"] or "") in by_key]
    return vehs, by_key


def _try_dir(directory, label, extra_names=()):
    """Try the accepted shapes in one directory.

    Returns (vehicles, by_key, detail) on success, None otherwise.
    """
    loaders = ([(n, _load_sqlite) for n in extra_names] + [
        ("fitment.json", _load_json),
        ("fitment.db", _load_sqlite),
        ("fitment.sqlite", _load_sqlite),
    ])
    for name, fn in loaders:
        path = directory / name
        if path.exists():
            try:
                result = fn(path)
            except Exception:
                result = None
            if result:
                return result[0], result[1], f"{label} ({name})"
    v_csv, f_csv = directory / "vehicles.csv", directory / "fitment.csv"
    if v_csv.exists() and f_csv.exists():
        try:
            result = _load_csv(v_csv, f_csv)
        except Exception:
            result = None
        if result:
            return result[0], result[1], f"{label} (CSV)"
    return None


def _load_legacy(directory):
    """Load ONLY the legacy owner scrape (legacy_scrape.db).

    Never falls through to fitment.db — the crawl is loaded separately so
    the two can be unioned with explicit priority.

    Prefers fitment_live.db — a slim committed copy of the scrape's
    vehicles/fitment tables. The full legacy_scrape.db is gitignored and
    only exists on the dev machine, so production (Render) would otherwise
    fall back to seed data.
    """
    for name in ("fitment_live.db", "legacy_scrape.db"):
        path = directory / name
        if not path.exists():
            continue
        try:
            result = _load_sqlite(path)
        except Exception:
            result = None
        if result:
            return result[0], result[1], f"legacy owner scrape ({name})"
    return None


def _load_2019plus(directory):
    """Load ONLY the audited 2019+ union database (fitment_2019plus.db).

    443 vehicles (2019-2026) merged from SEALIGHT/LASFIT/legacy sources with
    conflicts pre-resolved. Same vehicles/fitment table shape as the others.
    """
    path = directory / "fitment_2019plus.db"
    if not path.exists():
        return None
    try:
        result = _load_sqlite(path)
    except Exception:
        result = None
    if not result:
        return None
    return result[0], result[1], "audited 2019+ union (fitment_2019plus.db)"


def _vehicle_key(v):
    return (v["year"], v["make"], v["model"], v["trim"] or "")


def _merge_union(primary, secondary):
    """Union two (vehicles, by_key, detail) loads; primary wins per position.

    primary = Sylvania crawl (2026 snapshot), secondary = legacy owner
    scrape. For a vehicle present in both, positions the crawl covers use
    the crawl rows; positions only in the scrape are kept. Vehicles only in
    one source are kept as-is. Per-vehicle setup comes from the scrape
    (the crawl carries none).
    """
    p_vehs, p_by, p_detail = primary
    s_vehs, s_by, s_detail = secondary
    by_key = {k: list(rows) for k, rows in s_by.items()}
    for key, rows in p_by.items():
        if key not in by_key:
            by_key[key] = list(rows)
            continue
        p_positions = {r["position"] for r in rows}
        by_key[key] = ([r for r in by_key[key]
                        if r["position"] not in p_positions]
                       + list(rows))
    veh_by_key = {_vehicle_key(v): v for v in s_vehs}
    for v in p_vehs:
        key = _vehicle_key(v)
        if key in veh_by_key and not v.get("setup"):
            v = {**v, "setup": veh_by_key[key].get("setup")}
        veh_by_key[key] = v
    vehicles = sorted(veh_by_key.values(),
                      key=lambda v: (v["year"], v["make"], v["model"],
                                     v["trim"] or ""))
    return vehicles, by_key, f"{p_detail} + {s_detail}"


def _load_seed():
    vehicles = []
    by_key = {}
    for v in SEED_VEHICLES:
        vehicles.append({
            "vehicle_id": v["vehicle_id"],
            "year": v["year"], "make": v["make"],
            "model": v["model"], "trim": v.get("trim"),
        })
        key = (v["year"], v["make"], v["model"], v.get("trim") or "")
        by_key[key] = [{
            "position": r["position"],
            "bulb_size_raw": r["bulb_size"],
            "bulb_size": norm_size(r["bulb_size"]),
            "note": r.get("note", ""),
        } for r in v["fitment"]]
    return vehicles, by_key


class FitmentDB:
    """Fitment database: SEMA import -> crawl+scrape+2019+ -> seed.

    Vehicle metadata (~26k records) lives in memory; the ~440k fitment
    rows stay in SQLite and are queried per vehicle. A full in-memory
    load costs ~370MB per worker — too much for the Starter plan.
    """

    def __init__(self):
        self._vehicles = None       # merged vehicle dicts (small)
        self._veh_sources = None    # key -> [(source_idx, vehicle_id)], priority order
        self._db_paths = None       # sqlite paths, priority order
        self._by_key = None         # only for the SEMA/seed in-memory paths
        self._setup_by_key = None
        self._source = None
        self._detail = ""
        self._row_count = 0

    def _ensure(self):
        if self._vehicles is not None:
            return
        # 1) SEMA Data import — preferred when present (in-memory legacy path).
        hit = _try_dir(_sema_dir(), "SEMA Data import",
                       extra_names=("sema_fitment.db",))
        if hit:
            self._vehicles, self._by_key, self._detail = hit
            self._setup_by_key = {_vehicle_key(v): v.get("setup")
                                  for v in self._vehicles}
            self._source = "live"
            self._row_count = sum(len(r) for r in self._by_key.values())
            return
        # 2) SQLite sources, highest priority first: audited 2019+ union,
        #    then the Sylvania crawl, then the legacy owner scrape.
        d = _fitment_dir()
        sources = []  # (label, path)
        plus = d / "fitment_2019plus.db"
        if plus.exists():
            sources.append(("audited 2019+ union (fitment_2019plus.db)", plus))
        crawl = d / "fitment.db"
        if crawl.exists():
            sources.append(("live crawl data (fitment.db)", crawl))
        for name in ("fitment_live.db", "legacy_scrape.db"):
            leg = d / name
            if leg.exists():
                # fitment_live.db is the slim committed copy of the scrape;
                # the full legacy_scrape.db is gitignored (dev machines only).
                sources.append((f"legacy owner scrape ({name})", leg))
                break
        if sources:
            self._init_sql_sources(sources)
            return
        # 3) Demo seed fallback.
        self._vehicles, self._by_key = _load_seed()
        self._setup_by_key = {_vehicle_key(v): v.get("setup")
                              for v in self._vehicles}
        self._detail = "demo seed data (10 popular vehicles)"
        self._source = "seed"
        self._row_count = sum(len(r) for r in self._by_key.values())

    def _init_sql_sources(self, sources):
        """Register SQLite sources; load vehicle metadata only."""
        self._db_paths = [str(p) for _, p in sources]
        self._detail = " + ".join(label for label, _ in sources)
        self._source = "live"
        veh_by_key = {}
        self._veh_sources = {}
        self._row_count = 0
        for idx, (_, path) in enumerate(sources):
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                vcols = [r[1] for r in
                         con.execute("PRAGMA table_info(vehicles)")]
                vselect = "v.vehicle_id, v.year, v.make, v.model, v.trim"
                if "setup" in vcols:
                    vselect += ", v.setup"
                # Only vehicles that actually have fitment rows, mirroring
                # the old in-memory _normalize() filter.
                for r in con.execute(
                        f"SELECT {vselect} FROM vehicles v WHERE EXISTS "
                        "(SELECT 1 FROM fitment f "
                        "WHERE f.vehicle_id = v.vehicle_id)"):
                    rec = {
                        "vehicle_id": str(r[0]),
                        "year": int(r[1]),
                        "make": str(r[2]).strip(),
                        "model": str(r[3]).strip(),
                        "trim": (str(r[4]).strip()
                                 if r[4] not in (None, "", "None") else None),
                        "setup": (str(r[5]).strip()
                                  if len(r) > 5 and
                                  r[5] not in (None, "", "None") else None),
                    }
                    key = (rec["year"], rec["make"], rec["model"],
                           rec["trim"] or "")
                    self._veh_sources.setdefault(key, []).append(
                        (idx, rec["vehicle_id"]))
                    if key not in veh_by_key:
                        veh_by_key[key] = rec
                    elif (not veh_by_key[key].get("setup")
                          and rec.get("setup")):
                        veh_by_key[key] = {**veh_by_key[key],
                                           "setup": rec["setup"]}
                self._row_count += con.execute(
                    "SELECT COUNT(*) FROM fitment").fetchone()[0]
            finally:
                con.close()
        self._vehicles = sorted(
            veh_by_key.values(),
            key=lambda v: (v["year"], v["make"], v["model"], v["trim"] or ""))
        self._setup_by_key = {_vehicle_key(v): v.get("setup")
                              for v in self._vehicles}

    def _sql_fitment(self, year, make, model, trim):
        """Fitment rows for one vehicle, merged across sources by priority.

        Mirrors the old in-memory _merge_union(): a higher-priority source
        wins per position, but multiple rows for the same position *within*
        one source (e.g. 7440 and 7440NA turn signals) are all kept.
        """
        key = (int(year), make, model, trim or "")
        srcs = (self._veh_sources or {}).get(key)
        if not srcs:
            return []
        per_source = []  # [(positions, rows)] in priority order
        for idx, vid in srcs:
            con = sqlite3.connect(f"file:{self._db_paths[idx]}?mode=ro",
                                  uri=True)
            try:
                con.row_factory = sqlite3.Row
                rows = []
                for f in con.execute(
                        "SELECT position, bulb_size_raw, bulb_size, note "
                        "FROM fitment WHERE vehicle_id = ?", (vid,)):
                    row = {
                        "position": str(f["position"]).strip().lower(),
                        "bulb_size_raw": str(f["bulb_size_raw"]
                                             or f["bulb_size"] or "").strip(),
                        "bulb_size": norm_size(f["bulb_size"]
                                               or f["bulb_size_raw"] or ""),
                        "note": str(f["note"] or "").strip(),
                    }
                    if not row["bulb_size"]:
                        continue
                    rows.append(row)
            finally:
                con.close()
            per_source.append(({r["position"] for r in rows}, rows))
        merged = []
        for i in range(len(per_source) - 1, -1, -1):
            positions, rows = per_source[i]
            higher = set().union(*(p for p, _ in per_source[:i])) if i else set()
            merged.extend(r for r in rows if r["position"] not in higher)
        return merged

    # -- public interface -------------------------------------------------
    def get_years(self):
        self._ensure()
        return sorted({v["year"] for v in self._vehicles}, reverse=True)

    def get_makes(self, year):
        self._ensure()
        return sorted({v["make"] for v in self._vehicles
                       if v["year"] == int(year)})

    def get_models(self, year, make):
        self._ensure()
        return sorted({v["model"] for v in self._vehicles
                       if v["year"] == int(year) and v["make"] == make})

    def get_trims(self, year, make, model):
        self._ensure()
        return sorted({v["trim"] for v in self._vehicles
                       if v["year"] == int(year) and v["make"] == make
                       and v["model"] == model and v["trim"]})

    def iter_vehicles(self):
        """Distinct (year, make, model) dicts across the whole database."""
        self._ensure()
        seen = set()
        for v in self._vehicles:
            key = (v["year"], v["make"], v["model"])
            if key not in seen:
                seen.add(key)
                yield {"year": v["year"], "make": v["make"],
                       "model": v["model"]}

    def get_fitment(self, year, make, model, trim=None):
        self._ensure()
        if self._by_key is not None:
            rows = self._by_key.get((int(year), make, model, trim or ""), [])
        else:
            rows = self._sql_fitment(year, make, model, trim)
        # label + category enrichment for templates
        out = []
        for r in rows:
            # Brand rule: the word "headlight" must never reach the site.
            # Scrub it from source notes ("sealight position: LED Headlight
            # Bulbs" -> "sealight position: LED Bulbs").
            note = re.sub(r"(?i)\s*headlights?", "",
                          r.get("note") or "").strip()
            note = re.sub(r"\s{2,}", " ", note)
            out.append({
                **r,
                "note": note,
                "label": POSITION_LABELS.get(r["position"],
                                             r["position"].replace("_", " ").title()),
                "categories": POSITION_CATEGORIES.get(r["position"], []),
                "group": POSITION_GROUP.get(r["position"], "forward"),
            })
        return out

    def get_vehicle_setup(self, year, make, model, trim=None):
        """Front-bulb setup for a vehicle: halogen/xenon/factory_led/mixed.

        Comes from the legacy owner scrape; None when the vehicle isn't in
        the scrape or the scrape has no front-bulb rows for it.
        """
        self._ensure()
        try:
            key = (int(year), make, model, trim or "")
        except (TypeError, ValueError):
            return None
        return self._setup_by_key.get(key)

    def source_info(self):
        self._ensure()
        return {
            "source": self._source,
            "detail": self._detail,
            "vehicles": len(self._vehicles),
            "rows": self._row_count,
        }

    def reload(self):
        """Force re-detection (e.g. after the crawl lands new data)."""
        self._vehicles = None
        self._veh_sources = None
        self._db_paths = None
        self._by_key = None
        self._setup_by_key = None
        self._source = None
        self._detail = ""
        self._row_count = 0


fitment_db = FitmentDB()
