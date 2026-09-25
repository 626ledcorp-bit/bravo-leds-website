"""Fitment data loader — single swap point between seed data and live DBs.

Data sources, checked in priority order:

  1. SEMA Data import (``SEMA_DIR`` env var, default ``../sema``):
     ``sema_fitment.db`` (written by sema_import.py), else ``fitment.db`` /
     ``fitment.json`` / ``vehicles.csv`` + ``fitment.csv`` in that dir.
     Preferred over crawl data when present.
  2. Sylvania crawl output (``FITMENT_DIR`` env var, default ``../fitment``):
     ``fitment.json``, ``fitment.db`` / ``fitment.sqlite``,
     ``vehicles.csv`` + ``fitment.csv``.
  3. Seed data (``seed_fitment.py``) — demo fallback.

Accepted shapes per directory:
  1. fitment.json   — {"vehicles": [...], "fitment": [...]}
  2. fitment.db / fitment.sqlite / sema_fitment.db — tables: vehicles, fitment
  3. vehicles.csv + fitment.csv  — headers matching the schema below

Schema (both tables/files):
  vehicles: vehicle_id, year, make, model, trim
            (+ optional vcdb_vehicle_id, base_vehicle_id — read-safe)
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
  source_info() -> {"source": "live"|"seed", "detail": str,
                    "vehicles": int, "rows": int}
"""

import csv
import json
import os
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
        vehicles = [dict(r) for r in con.execute(
            "SELECT vehicle_id, year, make, model, trim FROM vehicles")]
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
    for v in vehicles:
        vehs.append({
            "vehicle_id": str(v.get("vehicle_id")),
            "year": int(v.get("year")),
            "make": str(v.get("make")).strip(),
            "model": str(v.get("model")).strip(),
            "trim": (str(v.get("trim")).strip()
                     if v.get("trim") not in (None, "", "None") else None),
        })
    by_key = {}
    for f in fitment:
        vid = str(f.get("vehicle_id"))
        veh = next((v for v in vehs if v["vehicle_id"] == vid), None)
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
    """Lazily-loaded fitment database: SEMA import -> crawl -> seed fallback."""

    def __init__(self):
        self._vehicles = None
        self._by_key = None
        self._source = None
        self._detail = ""

    def _ensure(self):
        if self._vehicles is not None:
            return
        # 1) SEMA Data import — preferred when present.
        hit = _try_dir(_sema_dir(), "SEMA Data import",
                       extra_names=("sema_fitment.db",))
        if hit:
            self._vehicles, self._by_key, self._detail = hit
            self._source = "live"
            return
        # 2) Sylvania crawl output.
        hit = _try_dir(_fitment_dir(), "live crawl data")
        if hit:
            self._vehicles, self._by_key, self._detail = hit
            self._source = "live"
            return
        # 3) Demo seed fallback.
        self._vehicles, self._by_key = _load_seed()
        self._source = "seed"
        self._detail = "demo seed data (10 popular vehicles)"

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

    def get_fitment(self, year, make, model, trim=None):
        self._ensure()
        key = (int(year), make, model, trim or "")
        rows = self._by_key.get(key, [])
        # label + category enrichment for templates
        out = []
        for r in rows:
            out.append({
                **r,
                "label": POSITION_LABELS.get(r["position"],
                                             r["position"].replace("_", " ").title()),
                "categories": POSITION_CATEGORIES.get(r["position"], []),
            })
        return out

    def source_info(self):
        self._ensure()
        return {
            "source": self._source,
            "detail": self._detail,
            "vehicles": len(self._vehicles),
            "rows": sum(len(r) for r in self._by_key.values()),
        }

    def reload(self):
        """Force re-detection (e.g. after the crawl lands new data)."""
        self._vehicles = None
        self._by_key = None
        self._source = None
        self._detail = ""


fitment_db = FitmentDB()
