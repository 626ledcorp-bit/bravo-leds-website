#!/usr/bin/env python3
"""Focused test: loader unions the legacy owner scrape under the Sylvania
crawl with correct priority, and exposes per-vehicle setup.

  - scrape fills a vehicle the crawl lacks
  - crawl wins on a vehicle+position conflict
  - scrape-only positions survive on a shared vehicle
  - get_vehicle_setup() returns the scrape classification (None when unknown)

Run:  cd website && python3 test_legacy_union.py
"""

import os
import sqlite3
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

TMP = tempfile.mkdtemp(prefix="legacy_union_")
FIT_DIR = os.path.join(TMP, "fitment")
SEMA_DIR = os.path.join(TMP, "sema")
os.makedirs(FIT_DIR)
os.makedirs(SEMA_DIR)


def make_crawl_db():
    # NOTE: no `setup` column here — exercises the loader's read-safe
    # PRAGMA introspection for DBs without the extra column.
    p = os.path.join(FIT_DIR, "fitment.db")
    cx = sqlite3.connect(p)
    cx.execute("""CREATE TABLE vehicles(vehicle_id TEXT PRIMARY KEY, year INT,
                  make TEXT, model TEXT, trim TEXT, source TEXT)""")
    cx.execute("""CREATE TABLE fitment(vehicle_id TEXT, position TEXT,
                  position_raw TEXT, bulb_size_raw TEXT, bulb_size TEXT,
                  flag TEXT, note TEXT, hintnote TEXT)""")
    cx.executemany("INSERT INTO vehicles VALUES(?,?,?,?,?,?)", [
        ("c1", 2020, "Ford", "F-150", None, "crawl"),
        ("c2", 2021, "Toyota", "Camry", None, "crawl"),
    ])
    cx.executemany("INSERT INTO fitment VALUES(?,?,?,?,?,?,?,?)", [
        # conflict with the scrape below: crawl's H11 must win over 9005
        ("c1", "low_beam", "Headlight Bulb Low Beam", "H11", "H11",
         "ok", None, None),
        ("c2", "low_beam", "Headlight Bulb Low Beam", "H11", "H11",
         "ok", None, None),
    ])
    cx.commit()
    cx.close()


def make_legacy_db():
    p = os.path.join(FIT_DIR, "legacy_scrape.db")
    cx = sqlite3.connect(p)
    cx.execute("""CREATE TABLE vehicles(vehicle_id TEXT PRIMARY KEY, year INT,
                  make TEXT, model TEXT, trim TEXT, source TEXT, setup TEXT)""")
    cx.execute("""CREATE TABLE fitment(vehicle_id TEXT, position TEXT,
                  position_raw TEXT, bulb_size_raw TEXT, bulb_size TEXT,
                  flag TEXT, note TEXT, hintnote TEXT)""")
    cx.executemany("INSERT INTO vehicles VALUES(?,?,?,?,?,?,?)", [
        ("legacy:2020:Ford:F-150:", 2020, "Ford", "F-150", None,
         "scrape", "mixed"),
        ("legacy:2015:Honda:Civic:", 2015, "Honda", "Civic", None,
         "scrape", "halogen"),
    ])
    cx.executemany("INSERT INTO fitment VALUES(?,?,?,?,?,?,?,?)", [
        ("legacy:2020:Ford:F-150:", "low_beam", "Low Beam Headlight",
         "9005", "9005", "ok", None, None),
        # gap fill: position the crawl lacks for this vehicle
        ("legacy:2020:Ford:F-150:", "fog_light", "Front Fog Light",
         "H10", "H10", "ok", None, None),
        ("legacy:2015:Honda:Civic:", "low_beam", "Low Beam Headlight",
         "H11", "H11", "ok", None, None),
    ])
    cx.commit()
    cx.close()


def main():
    make_crawl_db()
    make_legacy_db()
    old_fit, old_sema = os.environ.get("FITMENT_DIR"), os.environ.get("SEMA_DIR")
    os.environ["FITMENT_DIR"] = FIT_DIR
    os.environ["SEMA_DIR"] = SEMA_DIR
    try:
        import fitment_loader
        fitment_loader.fitment_db.reload()
        db = fitment_loader.fitment_db

        years = db.get_years()
        assert 2015 in years and 2020 in years and 2021 in years, years

        # crawl wins on conflict
        rows = {r["position"]: r for r in
                db.get_fitment(2020, "Ford", "F-150")}
        assert rows["low_beam"]["bulb_size"] == "H11", rows["low_beam"]
        # scrape fills the gap
        assert rows["fog_light"]["bulb_size"] == "H10", rows.get("fog_light")
        # scrape-only vehicle
        rows = {r["position"]: r for r in
                db.get_fitment(2015, "Honda", "Civic")}
        assert rows["low_beam"]["bulb_size"] == "H11", rows.get("low_beam")

        # setup exposure
        assert db.get_vehicle_setup(2020, "Ford", "F-150") == "mixed"
        assert db.get_vehicle_setup(2015, "Honda", "Civic") == "halogen"
        assert db.get_vehicle_setup(2021, "Toyota", "Camry") is None
        assert db.get_vehicle_setup(1999, "Nope", "Nope") is None

        info = db.source_info()
        assert info["source"] == "live", info
        assert "legacy_scrape.db" in info["detail"], info["detail"]
        assert info["vehicles"] == 3, info
        print("detail:", info["detail"])
        print("ALL LEGACY UNION TESTS PASSED")
    finally:
        if old_fit is None:
            os.environ.pop("FITMENT_DIR", None)
        else:
            os.environ["FITMENT_DIR"] = old_fit
        if old_sema is None:
            os.environ.pop("SEMA_DIR", None)
        else:
            os.environ["SEMA_DIR"] = old_sema
        import fitment_loader
        fitment_loader.fitment_db.reload()


if __name__ == "__main__":
    main()
