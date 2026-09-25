#!/usr/bin/env python3
"""
Sylvania bulb-finder crawler — 626 LEDs fitment database (Phase 2).

Chain per vehicle: makes(year) -> models(year,make) -> car_id -> positions -> notes(use_id)
Each notes entry carries oepn = OE bulb size (or "LED" = factory LED, non-serviceable).

Polite: serial requests, >=1.0s spacing, exponential backoff on 429/5xx,
checkpoint/resume via crawl_state.json, raw responses saved for audit.

Usage:
  python3 crawl.py --start-year 2025 --end-year 2005 [--makes Acura,Honda,...]
"""
import argparse, json, os, sys, time, random, urllib.request, urllib.parse, urllib.error

BASE_DIR = os.path.expanduser("~/workspace/626leds/fitment")
RAW_DIR = os.path.join(BASE_DIR, "raw")
LOG_DIR = os.path.join(BASE_DIR, "logs")
STATE_FILE = os.path.join(BASE_DIR, "crawl_state.json")
API = "https://www.sylvania-automotive.com/on/demandware.store/Sites-sylvaniaautomotive-Site/en_US/BulbFinder-GetDropdownValues"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# Non passenger-vehicle makes (powersports, marine, commercial, equipment).
EXCLUDE_MAKES = {
    "Aprilia", "Argo", "Blue Bird", "Can-Am", "Ducati", "Freightliner",
    "Harley-Davidson", "Husqvarna", "Indian", "Kawasaki", "KTM", "Peterbilt",
    "Piaggio", "Polaris", "Ski-Doo", "Triumph", "Yamaha", "Arctic Cat",
    "Beta", "Bobcat", "CFMOTO", "Cub Cadet", "Evobus",
}
# Suzuki Auto exited the US market after 2013; later "Suzuki" entries are powersports.
SUZUKI_AUTO_MAX_YEAR = 2013

_last_req = [0.0]

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, "crawl.log"), "a") as f:
        f.write(line + "\n")

def polite_wait():
    dt = time.monotonic() - _last_req[0]
    wait = 1.0 + random.uniform(0.0, 0.4) - dt
    if wait > 0:
        time.sleep(wait)

def api_get(params, retries=4):
    qs = urllib.parse.urlencode(params)
    url = API + "?" + qs
    backoff = 2.0
    for attempt in range(retries):
        polite_wait()
        _last_req[0] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
                if resp.status == 429:
                    raise urllib.error.HTTPError(url, 429, "rate limited", {}, None)
                return json.loads(body), url
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                log(f"HTTP {e.code} on {qs[:80]} — backoff {backoff:.0f}s (attempt {attempt+1})")
                time.sleep(backoff + random.uniform(0, 1))
                backoff *= 4
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                log(f"{type(e).__name__} on {qs[:80]} — backoff {backoff:.0f}s (attempt {attempt+1})")
                time.sleep(backoff + random.uniform(0, 1))
                backoff *= 4
                continue
            raise
    raise RuntimeError("exhausted retries: " + qs)

def save_raw(name, obj):
    path = os.path.join(RAW_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.rename(tmp, path)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"done_cars": [], "failed": [], "years_done": []}

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.rename(tmp, STATE_FILE)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2025)
    ap.add_argument("--end-year", type=int, default=2005)
    ap.add_argument("--makes", default="", help="comma-separated make names to limit to")
    ap.add_argument("--max-cars", type=int, default=0, help="0 = no limit (smoke test)")
    args = ap.parse_args()
    only_makes = {m.strip() for m in args.makes.split(",") if m.strip()}

    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    state = load_state()
    done = set(state["done_cars"])
    n_cars = 0
    t0 = time.time()

    for year in range(args.start_year, args.end_year - 1, -1):
        if year in state["years_done"]:
            log(f"year {year}: already complete, skipping")
            continue
        log(f"=== YEAR {year}: fetching makes ===")
        data, _ = api_get({"lookupType": "makes", "constructionYear": year})
        makes = data.get("response", [])
        save_raw(f"makes_{year}.json", {"year": year, "response": makes})
        auto_makes = []
        for m in makes:
            name = m["name"]
            if name in EXCLUDE_MAKES:
                continue
            if name == "Suzuki" and year > SUZUKI_AUTO_MAX_YEAR:
                continue
            if only_makes and name not in only_makes:
                continue
            auto_makes.append(m)
        log(f"year {year}: {len(makes)} makes total, {len(auto_makes)} passenger-auto")

        for mk in auto_makes:
            mid, mname = mk["id"], mk["name"]
            try:
                data, _ = api_get({"lookupType": "models", "constructionYear": year,
                                   "manufacturerId": mid})
            except Exception as e:
                log(f"FAILED models year={year} make={mname}: {e}")
                state["failed"].append({"stage": "models", "year": year, "make": mname})
                save_state(state)
                continue
            models = data.get("response", [])
            save_raw(f"models_{year}_{mid}.json",
                     {"year": year, "make": mname, "make_id": mid, "response": models})
            for mo in models:
                car_key = f"{year}|{mid}|{mo['id']}"
                if car_key in done:
                    continue
                try:
                    car_data, _ = api_get({"lookupType": "car_id", "constructionYear": year,
                                           "manufacturerId": mid, "modelId": mo["id"]})
                    car_id = car_data["response"][0]["id"]
                    pos_data, _ = api_get({"lookupType": "positions", "carId": car_id})
                    positions = pos_data.get("response", [])
                    notes = {}
                    for p in positions:
                        uid = p["use_id"]
                        n_data, _ = api_get({"lookupType": "notes", "carId": car_id,
                                             "useId": uid})
                        notes[uid] = n_data.get("response", [])
                    save_raw(f"car_{car_id}.json", {
                        "year": year, "make": mname, "make_id": mid,
                        "model": mo["name"], "model_id": mo["id"], "car_id": car_id,
                        "positions": positions, "notes": notes,
                    })
                except Exception as e:
                    log(f"FAILED car {car_key} ({year} {mname} {mo['name']}): {e}")
                    state["failed"].append({"stage": "car", "key": car_key,
                                            "year": year, "make": mname,
                                            "model": mo["name"], "err": str(e)[:200]})
                    save_state(state)
                    continue
                done.add(car_key)
                state["done_cars"] = sorted(done)
                n_cars += 1
                if n_cars % 25 == 0:
                    save_state(state)
                    el = time.time() - t0
                    log(f"progress: {n_cars} cars this run, {len(done)} total, "
                        f"{el/60:.1f} min elapsed ({el/max(n_cars,1):.1f}s/car)")
                if args.max_cars and n_cars >= args.max_cars:
                    save_state(state)
                    log(f"smoke limit reached ({args.max_cars} cars)")
                    return
        state["years_done"].append(year)
        save_state(state)
        log(f"=== YEAR {year} complete ===")

    save_state(state)
    log(f"DONE: {n_cars} cars this run, {len(done)} total cars")

if __name__ == "__main__":
    main()
