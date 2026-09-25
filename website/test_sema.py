#!/usr/bin/env python3
"""End-to-end + regression tests for the SEMA Data import path.

Scenario A — SEMA end-to-end (synthetic data only, clearly fake):
    importer parses the synthetic ACES sample -> loader prefers SEMA data ->
    finder cascade + fitment results page render it -> cart cycle works.
Scenario B — priority: SEMA data wins when crawl data is also present.
Scenario C — regression: with no live data anywhere, seed fallback still
    works (source == "seed", i.e. the "Demo fitment data" pill).

Run:  cd website && python3 test_sema.py
"""

import importlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

SAMPLE = os.path.join(WEB, "..", "sema", "sample_synthetic")
PART_MAP = os.path.join(WEB, "..", "sema", "part_number_map.json")

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))


def fresh_modules(sema_dir, fitment_dir):
    os.environ["SEMA_DIR"] = sema_dir
    os.environ["FITMENT_DIR"] = fitment_dir
    import fitment_loader
    importlib.reload(fitment_loader)
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
        appmod = sys.modules["app"]
    else:
        import app as appmod
    return fitment_loader, appmod


def run_importer(inbox, out, map_path=PART_MAP):
    r = subprocess.run(
        [sys.executable, os.path.join(WEB, "sema_import.py"),
         "--inbox", inbox, "--out", out, "--map", map_path],
        capture_output=True, text=True, cwd=WEB)
    return r


def scenario_a(tmp):
    print("Scenario A — SEMA import end-to-end (synthetic data)")
    inbox = os.path.join(tmp, "inbox")
    os.makedirs(inbox)
    shutil.copy(os.path.join(SAMPLE, "aces_sample.xml"), inbox)
    shutil.copy(os.path.join(SAMPLE, "vcdb_sample.csv"),
                os.path.join(inbox, "vcdb_vehicles.csv"))
    out = os.path.join(tmp, "sema_fitment.db")

    r = run_importer(inbox, out)
    check("importer exits 0", r.returncode == 0, r.stderr[-500:])
    con = sqlite3.connect(out)
    n_veh = con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
    n_fit = con.execute("SELECT COUNT(*) FROM fitment").fetchone()[0]
    n_rev = con.execute("SELECT COUNT(*) FROM review").fetchone()[0]
    kinds = sorted(k for k, in con.execute("SELECT DISTINCT kind FROM review"))
    has_vcdb_cols = any(
        r2[1] == "vcdb_vehicle_id"
        for r2 in con.execute("PRAGMA table_info(vehicles)"))
    con.close()
    check("2 vehicles imported", n_veh == 2, f"got {n_veh}")
    check("4 fitment rows imported", n_fit == 4, f"got {n_fit}")
    check("3 review rows (nothing fabricated)",
          n_rev == 3 and kinds == ["unmapped_part", "unmapped_position",
                                   "unmapped_vehicle"],
          f"got {n_rev} {kinds}")
    check("vcdb_vehicle_id column present", has_vcdb_cols)

    # Loader prefers SEMA data.
    sema_dir = os.path.join(tmp, "sema_live")
    os.makedirs(sema_dir)
    shutil.copy(out, os.path.join(sema_dir, "sema_fitment.db"))
    fitmod, appmod = fresh_modules(sema_dir, os.path.join(tmp, "no_crawl"))
    fdb = fitmod.FitmentDB()
    info = fdb.source_info()
    check("loader source is live", info["source"] == "live", str(info))
    check("detail names SEMA", "SEMA" in info["detail"], str(info))
    check("years == [2020, 2018]", fdb.get_years() == [2020, 2018])
    check("makes(2018) == ['DemoMake']",
          fdb.get_makes(2018) == ["DemoMake"])
    check("models resolve",
          fdb.get_models(2018, "DemoMake") == ["DemoModel-SYNTHETIC"])
    check("trims resolve", fdb.get_trims(2018, "DemoMake",
                                         "DemoModel-SYNTHETIC") == ["LE"])
    rows = fdb.get_fitment(2018, "DemoMake", "DemoModel-SYNTHETIC", "LE")
    sizes = sorted(r["bulb_size"] for r in rows)
    check("fitment sizes H11/9005/7443", sizes == ["7443", "9005", "H11"],
          str(sizes))

    # Flask app on SEMA data.
    client = appmod.app.test_client()
    check("GET / -> 200", client.get("/").status_code == 200)
    check("GET /shop -> 200", client.get("/shop").status_code == 200)
    check("GET /shop/led-bulbs -> 200",
          client.get("/shop/led-bulbs").status_code == 200)
    check("GET /shop/off-road -> 404 (renamed)",
          client.get("/shop/off-road").status_code == 404)
    check("GET /dot-compliance -> 200",
          client.get("/dot-compliance").status_code == 200)
    check("GET /shop/bogus -> 404",
          client.get("/shop/bogus").status_code == 404)
    import db as dbmod
    p0 = dbmod.list_products()[0]
    check("GET /product/<id> -> 200",
          client.get(f"/product/{p0['id']}").status_code == 200)
    check("GET /product/bogus -> 404",
          client.get("/product/bogus").status_code == 404)
    r = client.get("/api/fitment/years")
    check("GET /api/fitment/years -> 200 + JSON",
          r.status_code == 200 and 2018 in r.get_json())
    url = ("/fitment?year=2018&make=DemoMake&model=DemoModel-SYNTHETIC"
           "&trim=LE")
    r = client.get(url)
    body = r.get_data(as_text=True)
    check("fitment results page renders SEMA data",
          r.status_code == 200 and "H11" in body and "DemoMake" in body)
    check("results show matching products",
          'class="pos-prod"' in body or "pos-prod" in body)

    # Cart cycle.
    p0 = dbmod.get_product(p0["id"])
    vid = p0["variations"][0]["id"]
    r = client.post("/cart/add", data={"product_id": p0["id"],
                                       "variation_id": str(vid), "qty": "2"},
                    follow_redirects=True)
    body = r.get_data(as_text=True)
    check("cart add -> 200, product shown",
          r.status_code == 200 and p0["name"] in body)
    key = f"{p0['id']}::v{vid}"
    r = client.post("/cart/update", data={"key": key, "qty": "1"},
                    follow_redirects=True)
    check("cart update -> 200", r.status_code == 200)
    r = client.post("/cart/remove", data={"key": key}, follow_redirects=True)
    body = r.get_data(as_text=True)
    check("cart remove -> empty cart",
          r.status_code == 200 and p0["name"] not in body)
    return True


def scenario_b(tmp):
    print("Scenario B — SEMA preferred over crawl data")
    sema_dir = os.path.join(tmp, "sema_live_b")
    os.makedirs(sema_dir, exist_ok=True)
    inbox = os.path.join(tmp, "inbox_b")
    os.makedirs(inbox)
    shutil.copy(os.path.join(SAMPLE, "aces_sample.xml"), inbox)
    shutil.copy(os.path.join(SAMPLE, "vcdb_sample.csv"),
                os.path.join(inbox, "vcdb_vehicles.csv"))
    r = run_importer(inbox, os.path.join(sema_dir, "sema_fitment.db"))
    assert r.returncode == 0, r.stderr[-500:]

    crawl_dir = os.path.join(tmp, "crawl")
    os.makedirs(crawl_dir)
    con = sqlite3.connect(os.path.join(crawl_dir, "fitment.db"))
    con.execute("CREATE TABLE vehicles(vehicle_id TEXT PRIMARY KEY, year INT,"
                " make TEXT, model TEXT, trim TEXT, source TEXT)")
    con.execute("CREATE TABLE fitment(vehicle_id TEXT, position TEXT,"
                " bulb_size_raw TEXT, bulb_size TEXT, note TEXT)")
    con.execute("INSERT INTO vehicles VALUES ('c1',2015,'CrawlMake','CrawlModel',NULL,'crawl')")
    con.execute("INSERT INTO fitment VALUES ('c1','low_beam','H11','H11','')")
    con.commit()
    con.close()

    fitmod, _ = fresh_modules(sema_dir, crawl_dir)
    fdb = fitmod.FitmentDB()
    info = fdb.source_info()
    check("SEMA wins over crawl", "SEMA" in info["detail"], str(info))
    check("crawl vehicle not visible",
          fdb.get_makes(2015) == [] and fdb.get_makes(2018) == ["DemoMake"])
    return True


def scenario_c(tmp):
    print("Scenario C — seed fallback regression (demo pill)")
    fitmod, appmod = fresh_modules(os.path.join(tmp, "e1"),
                                   os.path.join(tmp, "e2"))
    fdb = fitmod.FitmentDB()
    info = fdb.source_info()
    check("source falls back to seed", info["source"] == "seed", str(info))
    check("seed has 10 vehicles", info["vehicles"] == 10,
          str(info["vehicles"]))
    check("seed cascade works", 2018 in fdb.get_years()
          and len(fdb.get_makes(2018)) > 0)
    client = appmod.app.test_client()
    r = client.get("/")
    check("GET / -> 200 on seed", r.status_code == 200)
    r = client.get("/fitment?year=2018&make=Toyota&model=Camry")
    check("seed fitment page renders",
          r.status_code == 200 and "Demo fitment data" in r.get_data(as_text=True))
    return True


def main():
    tmp = tempfile.mkdtemp(prefix="sema_test_")
    try:
        scenario_a(tmp)
        scenario_b(tmp)
        scenario_c(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL)
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
