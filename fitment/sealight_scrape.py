#!/usr/bin/env python3
"""Scrape SEALIGHT's vehicle bulb-finder API into sealight_guides.db.

Extracts FACTUAL data only: year, make, model, position, bulb size.
No marketing copy, no product recommendations, no prices, no images.

Polite by design: max 1 request per 2.5 seconds, sequential, normal
browser User-Agent. Aborts immediately on 429/403.

Classification subtlety: SEALIGHT sells LED upgrades, so a position named
"LED Headlight Bulbs" does NOT mean factory LED. The factory setup comes
from the bulb SIZE codes (H11/9005/9006 = halogen; D1S/D2S/... = xenon,
same ^D\\d[SR] rule as the existing classifier). A vehicle whose positions
include no headlight entries at all is inferred as factory LED (flagged in
the review file for audit); a vehicle with no position data at all is
"unknown" and excluded from the bot cache.

Output:
  ~/workspace/626leds/fitment/sealight_guides.db   (vehicles/fitment tables,
      same shapes as lasfit_guides.db)
  ~/workspace/marketplace-autoreply/sealight_fitment.json  ("year|make|model"
      -> setup, for the bot's classify_sealight)
  ~/workspace/626leds/fitment/sealight_review.json  (counts, conflicts vs
      LASFIT, inferred factory-LED list, suspicious/unmapped)
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = os.path.expanduser("~/workspace/626leds/fitment")
DB = os.path.join(BASE, "sealight_guides.db")
BOT_JSON = os.path.expanduser("~/workspace/marketplace-autoreply/sealight_fitment.json")
REVIEW_JSON = os.path.join(BASE, "sealight_review.json")
LASFIT_BOT_JSON = os.path.expanduser("~/workspace/marketplace-autoreply/lasfit_fitment.json")
CACHE_DIR = os.path.join(BASE, "sealight_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
API = "https://sealight-led.com/api/vehicle"
DELAY = 2.5  # seconds between live requests; cached hits are free

# Phase 1: gap makes (years 2019-2025; legacy xlsx covers pre-2019).
GAP_MAKES = ["Acura", "Alfa Romeo", "Bentley", "Cadillac", "Chrysler",
             "Ferrari", "Genesis", "INFINITI", "Jaguar", "Lincoln",
             "Maserati", "Porsche"]
PHASE1_YEARS = list(range(2019, 2026))

XENON_RE = re.compile(r"^D\d[SR]")

POS_MAP = {
    "led headlight bulbs": "headlight",
    "hid headlight bulbs": "headlight",
    "fog light bulbs": "fog_light",
    "backup / reverse lights": "reverse_light",
    "brake / tail lights": "tail_light",
    "turn signal lights": "turn_signal",
    "license plate lights": "license_plate",
    "drl lights": "drl",
    "interior lights": "interior",
}


class Blocked(Exception):
    pass


def api(action, **params):
    """Cached GET against the SEALIGHT vehicle API. Aborts on 429/403."""
    q = "action=" + action + "".join(
        "&%s=%s" % (k, urllib.parse.quote(str(v)))
        for k, v in sorted(params.items()))
    path = os.path.join(CACHE_DIR, hashlib.sha1(q.encode()).hexdigest() + ".json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)["resp"]
    time.sleep(DELAY)
    req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (429, 403):
            raise Blocked("SEALIGHT returned HTTP %s; aborting" % e.code)
        raise
    with open(path, "w") as fh:
        json.dump({"q": q, "resp": data}, fh)
    return data


def split_sizes(raw):
    """Split compound size strings like 'H11/H8/H9' into canonical parts."""
    return [p.strip().upper()
            for p in re.split(r"[/\\,]", raw or "") if p.strip()]


def classify_size(size):
    s = (size or "").upper()
    if XENON_RE.match(s):
        return "xenon"
    if "LED" in s:
        return "factory_led"
    return "halogen"


def norm_pos(raw):
    key = (raw or "").strip().lower()
    if key in POS_MAP:
        return POS_MAP[key]
    return re.sub(r"[^a-z0-9]+", "_", key).strip("_") or "other"


def classify_vehicle(positions):
    """positions: list of (raw_name, [size_raw]). -> (setup, evidence str)."""
    if not positions:
        return "unknown", "no position data from API"
    hl = []
    for raw, sizes in positions:
        if "headlight" in raw.lower():
            for s in sizes:
                hl.extend(split_sizes(s))
    if not hl:
        return "factory_led", "inferred: positions listed, none for headlights"
    uniq = sorted(set(hl))
    classes = {classify_size(s) for s in uniq}
    ev = ",".join(uniq)
    if classes == {"halogen"}:
        return "halogen", ev
    if classes == {"xenon"}:
        return "xenon", ev
    if classes == {"factory_led"}:
        return "factory_led", ev
    return "mixed", ev


def init_db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS vehicles("
                "vehicle_id TEXT PRIMARY KEY, year INT, make TEXT, "
                "model TEXT, trim TEXT, source TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS fitment("
                "vehicle_id TEXT, position TEXT, position_raw TEXT, "
                "bulb_size_raw TEXT, bulb_size TEXT, flag TEXT, "
                "note TEXT, hintnote TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_fit_vehicle "
                "ON fitment(vehicle_id)")
    return con


def store_vehicle(con, stored, year, make, model, positions):
    """Insert vehicle + fitment rows. Returns (vehicle_id, inserted_bool)."""
    vid = "sealight:%s:%s:%s" % (make.strip().lower(),
                                 model.strip().lower(), year)
    if vid in stored:
        return vid, False
    stored.add(vid)
    con.execute("INSERT OR IGNORE INTO vehicles VALUES (?,?,?,?,?,?)",
                (vid, year, make, model, None, "sealight"))
    for raw, sizes in positions:
        pos = norm_pos(raw)
        for sraw in sizes:
            for s in split_sizes(sraw):
                note = ("sealight position: %s" % raw
                        if "headlight" in raw.lower() else "")
                con.execute(
                    "INSERT INTO fitment VALUES (?,?,?,?,?,?,?,?)",
                    (vid, pos, raw, sraw, s, classify_size(s), note, ""))
    con.commit()
    return vid, True


def get_positions(year, make, model):
    d = api("getPositions", year=year, make=make, model=model)
    rows = d.get("data") or []
    return [(x.get("name", ""), [s.get("name", "") for s in x.get("sizes", [])])
            for x in rows]


def phase1(con, stored, bot, review, only_year=None, only_make=None):
    """Gap makes, years 2019-2025."""
    gap = {m.lower() for m in GAP_MAKES}
    st = {"years": [], "makes": sorted(GAP_MAKES), "vehicles": 0,
          "fitment_rows": 0, "setup_counts": {}}
    for y in PHASE1_YEARS:
        if only_year and y != only_year:
            continue
        st["years"].append(y)
        makes = api("getMakes", year=y).get("data") or []
        for mk in makes:
            if only_make and mk.lower() != only_make.lower():
                continue
            if mk.lower() not in gap:
                continue
            models = api("getModels", year=y, make=mk).get("data") or []
            for mo in models:
                positions = get_positions(y, mk, mo)
                setup, ev = classify_vehicle(positions)
                vid, inserted = store_vehicle(con, stored, y, mk, mo, positions)
                if inserted:
                    st["vehicles"] += 1
                    st["fitment_rows"] += sum(
                        len(split_sizes(s)) for _, ss in positions for s in ss)
                key = "%d|%s|%s" % (y, mk.strip().lower(), mo.strip().lower())
                if setup != "unknown":
                    bot[key] = setup
                st["setup_counts"][setup] = st["setup_counts"].get(setup, 0) + 1
                if setup == "factory_led" and ev.startswith("inferred"):
                    review["inferred_factory_led"].append({
                        "key": key, "n_positions": len(positions),
                        "positions": [p for p, _ in positions]})
                if setup == "xenon":
                    review["suspicious"].append(
                        {"key": key, "setup": setup, "evidence": ev,
                         "note": "xenon-only 2019+ is rare; eyeball"})
    return st


def match_models(lasfit_model, sealight_models):
    """Map a (possibly combined) LASFIT model to SEALIGHT model names."""
    lm = lasfit_model.strip().lower()
    exact = [m for m in sealight_models if m.strip().lower() == lm]
    if exact:
        return exact
    toks = [t for t in re.split(r"[^a-z0-9]+", lm) if t]
    out = []
    for m in sealight_models:
        fm = re.sub(r"[^a-z0-9]+", "", m.lower())
        for t in toks:
            if len(t) >= 2 and (t == fm or t in fm or fm in t):
                out.append(m)
                break
    return out


def phase2(con, stored, bot, review):
    """Overlap double-check: same vehicles as the LASFIT guides, cap 2025."""
    with open(LASFIT_BOT_JSON) as fh:
        lasfit = json.load(fh)
    groups = {}
    for key, lsetup in lasfit.items():
        y, mk, mo = key.split("|", 2)
        y = int(y)
        if y > 2025:
            continue
        groups.setdefault((y, mk), []).append((mo, lsetup, key))
    st = {"lasfit_keys": len(lasfit), "lasfit_keys_checked": 0,
          "sealight_vehicles": 0, "agreements": 0,
          "conflicts": [], "unmapped": []}
    for (y, mk), items in sorted(groups.items()):
        makes = api("getMakes", year=y).get("data") or []
        smk = next((m for m in makes if m.lower() == mk.lower()), None)
        if not smk:
            st["unmapped"].append({"year": y, "make": mk,
                                   "reason": "make not in SEALIGHT"})
            continue
        models = api("getModels", year=y, make=smk).get("data") or []
        for lmo, lsetup, lkey in items:
            st["lasfit_keys_checked"] += 1
            cands = match_models(lmo, models)
            if not cands:
                st["unmapped"].append(
                    {"lasfit_key": lkey, "reason": "no SEALIGHT model match",
                     "sealight_models": models})
                continue
            for c in cands:
                positions = get_positions(y, smk, c)
                setup, ev = classify_vehicle(positions)
                vid, inserted = store_vehicle(con, stored, y, smk, c, positions)
                skey = "%d|%s|%s" % (y, smk.strip().lower(), c.strip().lower())
                if inserted:
                    st["sealight_vehicles"] += 1
                if setup == "unknown":
                    st["unmapped"].append(
                        {"lasfit_key": lkey, "sealight_key": skey,
                         "reason": "SEALIGHT returned no position data"})
                    continue
                bot[skey] = setup
                if setup == lsetup:
                    st["agreements"] += 1
                else:
                    st["conflicts"].append({
                        "lasfit_key": lkey, "lasfit_setup": lsetup,
                        "sealight_key": skey, "sealight_setup": setup,
                        "sealight_evidence": ev})
    return st


def main():
    args = set(sys.argv[1:])
    con = init_db()
    stored = {r[0] for r in con.execute("SELECT vehicle_id FROM vehicles")}
    bot = {}
    if os.path.exists(BOT_JSON):
        with open(BOT_JSON) as fh:
            bot = json.load(fh)
    review = {"inferred_factory_led": [], "suspicious": [],
              "phase1": None, "phase2": None}
    try:
        if "--phase2-only" not in args:
            oy = om = None
            for a in sys.argv[1:]:
                if a.startswith("--year="):
                    oy = int(a.split("=", 1)[1])
                if a.startswith("--make="):
                    om = a.split("=", 1)[1]
            review["phase1"] = phase1(con, stored, bot, review, oy, om)
        if "--phase1-only" not in args:
            review["phase2"] = phase2(con, stored, bot, review)
    except Blocked as e:
        review["blocked"] = str(e)
        print("BLOCKED:", e, file=sys.stderr)
    with open(BOT_JSON, "w") as fh:
        json.dump(bot, fh, indent=1, sort_keys=True)
    with open(REVIEW_JSON, "w") as fh:
        json.dump(review, fh, indent=1)
    print(json.dumps({"phase1": review["phase1"], "phase2": review["phase2"],
                      "bot_entries": len(bot),
                      "inferred_factory_led": len(review["inferred_factory_led"]),
                      "suspicious": len(review["suspicious"]),
                      "blocked": review.get("blocked")}, indent=1))


if __name__ == "__main__":
    main()
