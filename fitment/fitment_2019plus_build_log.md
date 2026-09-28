# fitment_2019plus build log

Started: 2026-09-28 17:56:38 UTC

## Sources

- legacy: 8 vehicles, 190 rows
- lasfit: 223 vehicles, 1096 rows
- sealight: 228 vehicles, 3009 rows
- lasfit setup json: 147 keys
- sealight setup json: 354 keys

## Union vehicles: 443 distinct 2019+ (year/make/model/trim)

- setup conflicts resolved to 'mixed': 1

## Fitment conflicts (sources disagree on size): 16

- recorded in review table; higher-priority source kept

- bulb_size_aliases copied: 21 rows

- fitment_2019plus.db: 443 vehicles, 2118 fitment rows

## Interior kits

- candidates with map+dome data: 52
- kits after trim aggregation (cap 200): 21

- interior_kits.db: 21 kits written

- website/data/interior_kits.json: 21 kits


Done in 1.2s. Outputs:
- fitment/fitment_2019plus.db (NEW; existing DBs untouched)
- fitment/interior_kits.db (NEW)
- website/data/interior_kits.json (committed with the site)
