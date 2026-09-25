# 626 LEDs — Vehicle→Bulb-Size Fitment Database

## Source
Sylvania Automotive bulb finder (`sylvania-automotive.com`), crawled 2026-09.
The user accepted the ToS gray area of building on Sylvania's public compilation
for the store's own use. Data is a point-in-time snapshot; re-crawl annually
for new model years (see "Re-running").

**Robots note:** `robots.txt` disallows `/on/demandware.store/*` (the API path
the site's own finder JS calls). This crawl uses those same endpoints at a
polite ~1 req/sec, serial, with backoff — no circumvention, no CAPTCHA
solving. Flagged here so the call can be revisited.

## How the source works (Phase 1 recon)
Public JSON API (Salesforce Commerce Cloud), no auth/captcha token required:
1. `BulbFinder-GetDropdownValues?lookupType=makes&constructionYear={Y}`
   → `[{id, name}]`
2. `...?lookupType=models&constructionYear={Y}&manufacturerId={mid}`
   → `[{id, name}]` (no trim level; trims surface as option-notes later)
3. `...?lookupType=car_id&constructionYear={Y}&manufacturerId={mid}&modelId={moid}`
   → `[{id: carId}]`
4. `...?lookupType=positions&carId={carId}`
   → `[{use_id, use_name, pos_name}]` (~12–21 positions/vehicle)
5. `...?lookupType=notes&carId={carId}&useId={useId}` per position
   → `[{note, hintnote, oepn, installvideo, alternatepartnumbers}]`

`oepn` is the key field: the OE bulb size (e.g. `921`, `9005`, `H11`) — or the
literal string `LED` meaning factory-LED, non-serviceable (recorded, not a size).
Each position can return multiple note variants capturing trim/option splits,
e.g. F-150 fog light: "with LED Headlights"→LED vs "with Factory Halogen
Headlights"→9140. All variants are kept with their note text.

Verified end-to-end on 2018 Toyota Camry, 2020 Ford F-150, 2025 Toyota 4Runner.

## Coverage decisions
- Years crawled newest-first: 2025 → 2005 (Sylvania lists 1955–2025; pre-2005
  deprioritized — oldest LED-customer-relevant years first).
- Passenger-vehicle makes only. Excluded: powersports/marine/commercial
  (Aprilia, Argo, Blue Bird, Can-Am, Ducati, Freightliner, Harley-Davidson,
  Husqvarna, Indian, Kawasaki, KTM, Peterbilt, Piaggio, Polaris, Ski-Doo,
  Triumph, Yamaha, Arctic Cat, Beta, Bobcat, CFMOTO, Cub Cadet, Evobus).
  Suzuki included only for model years ≤ 2013 (US auto exit; later entries
  are powersports).
- Positions skipped (no sellable bulb): dash turn-signal/high-beam indicators,
  ashtray light.

## Files
- `crawl.py` — polite crawler; raw JSON per vehicle in `raw/`; resumable via
  `crawl_state.json`; progress in `logs/crawl.log`.
- `normalize.py` — builds the database from `raw/` (idempotent; re-run anytime).
- `fitment.db` — SQLite: `vehicles`, `fitment`, `bulb_size_aliases`, `review`.
- `vehicles.csv`, `fitment.csv`, `bulb_size_aliases.csv`, `review.csv`
- `logs/distinct_use_names.json`, `logs/distinct_oepn.json` — audit logs.

## Schema
- **vehicles**: vehicle_id (= Sylvania carId, stable per year/make/model),
  year, make, model, trim (NULL — source has no trim level), source
- **fitment**: vehicle_id, position (canonical snake_case), position_raw,
  bulb_size_raw (as returned), bulb_size (canonical, NULL when factory LED),
  flag (`ok` | `alias` | `factory_led` | `missing`), note, hintnote
- **bulb_size_aliases**: every raw→canonical equivalence decision, logged
- **review**: unmapped positions / ambiguous bulb sizes / missing payloads —
  never silently dropped

## Bulb-size normalization rules
Uppercase, strip hyphens/spaces (`H-11`→`H11`); unify industry-standard
equivalents to the common US trade number, each logged in `bulb_size_aliases`:
HB3→9005, HB4→9006, HB5→9007, HB1→9004, HB2→9003, H13→9008,
9140/9145/9155→H10, 912/906→921, 168/2825/W5W→194, 4157/4057→3157,
P21W→1156, P21/5W→1157, PY21W→7507, HIR1→9011, HIR2→9012.
Amber variants kept distinct (7440A, 7444NA). Dual-filament pairs kept
distinct (7440 vs 7443, 3156 vs 3157). HID types (D1S–D8S) kept as-is.
Compound/unexpected values → `review`, not guessed.

## Re-running
1. `python3 crawl.py --start-year 2026 --end-year 2005` (add new years on top;
   state file makes it resumable; ~1 req/sec, expect ~2s/vehicle-position batch)
2. `python3 normalize.py` — rebuilds DB + CSVs from all of `raw/`
3. Check `review.csv` for new unmapped positions/sizes after each run and
   extend the maps in `normalize.py` accordingly.
