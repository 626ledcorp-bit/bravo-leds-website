# Competitor Research Notes — Fitment Databases & Interior LED Kits
Phase 1 research for Bravo LEDs e-commerce site. Researched 2026-09-28 via public web
search + page fetches only (no logins, no purchases, ~1 request per 3s, no 429/403 hit).
FACTS ONLY: year/make/model, position, bulb size, kit contents, prices. No marketing copy.

---

## PART 1 — COMPETITOR FITMENT DATABASES

### SEALIGHT (sealight-led.com)
- Per-model bulb size chart pages at sealight-led.com/automotive-bulb-finder/{make}/{model}
  (e.g. /gmc/envoy-xl, /chevrolet/cobalt, /hyundai/veracruz). Static server-rendered HTML
  tables — scrapable without JS.
- Table columns per model page: position -> one or more bulb sizes. Positions covered:
  front lamps (low/high), fog, backup/reverse, brake/tail, turn signal, license plate,
  DRL, interior. Example (2002-2006 GMC Envoy XL): front lamps H11/H8/H9 + 9005/HB3 +
  9006/HB4; fog 880, 881; backup/brake/turn/DRL 3156/3157; license + interior T10/194/168.
- Data is factual OEM bulb sizes per model-year range (not product marketing). Pages also
  carry blog-style per-model guides (e.g. 2007-2013 Toyota Tundra, 2007-2013 Chevy
  Silverado 1500) with the same factual sizes.
- https://sealight-led.com/api/vehicle returns {"status":0,"msg":null,"data":null} on GET,
  with and without query params (tried make/model/year and position variants). The on-page
  finder widget is a JS app (template shows result.vehicle.year/make/model/position and
  result.bulb_size), but the endpoint does not answer unauthenticated GET. NOT a usable
  public JSON API as of 2026-09-28.
- Verdict: no usable public API; the static per-model HTML chart pages ARE usable
  (scrape-friendly).

### LASFIT (lasfit.com)
- No public JSON fitment endpoint found. The nav-bar "Bulb Finder" (Year/Make/Model/
  Application) is JS-driven; no open API observed.
- HOWEVER the fitment database leaks structurally in two places:
  1. Per-vehicle "combo package" product pages at
     lasfit.com/collections/combo-package-by-vehicle/products/{slug} contain a static
     HTML table titled "{Model} Bulb Size Chart ({years})" with columns
     Location | Bulb Size | Recommend Product Series | Watt/Lumens | Key Features.
     Example — 2016-2018 Toyota RAV4 chart: fog H11; front turn 7443; rear turn 7443;
     brake/tail 7443; front side marker 168/194; inner tail 168/194; backup 921;
     map 168/194; license 168/194; vanity 168/194; dome DE3175/DE3021; trunk 168/194.
     Example — 2019-2021 Toyota RAV4 chart: fog H11; front/rear turn 7443;
     front side marker 168/194; backup 921; map 168/194; license 168/194;
     vanity 168/194; dome DE3021/DE3022; trunk 168/194.
     These tables are factual bulb sizes (usable).
  2. The same per-vehicle pages carry a "Model No" string encoding the kit's per-position
     sizes, e.g. 2019-2020 Hyundai Veloster:
     Pro-HK6+LAplus9005+T3-1157A+T3-1156A+L-T10A+L-T10R+L-28MM*3+L-T10*2+L-T15
     (2010-2013 Camaro example: LSplusH13+LDplus5202+D2-3157R*2+L-T10*3+L-T10A).
  3. Product URLs from the finder carry fitment params, e.g.
     ?rq=mk_honda~md_civic~yr_1997~na_led-bulbs~zq_hatchback~dz_1-low-high-forward-lighting
     (schema: mk=make, md=model, yr=year, na=application, zq=trim, dz=position) —
     confirms a structured YMM fitment DB exists behind the JS finder, but no public
     endpoint exposes it.
- lasfit.com is Shopify: /products.json and /search/suggest.json are public, but they
  return catalog/product data, not the fitment DB itself.
- Verdict: usable factual data = the static per-model Bulb Size Chart tables on the
  combo-package pages (HTML scraping OK). No public fitment JSON API.

### AUXITO (auxito.com)
- Shopify store. The homepage "Shop by Vehicle" (Year/Make/Model) selector is NOT a
  structured fitment API: it maps the selection to a Shopify search query of the form
  q=led_bulbs_{make}_{model}_{year} (e.g. auxito.com/pages/search-result?q=led_bulbs_gmc_yukon_2011),
  which fuzzy-matches a per-vehicle full-kit PRODUCT. Verified live:
  q=led_bulbs_honda_accord_2018 returns the product "2018-2021 Honda Accord LED Bulb
  Replacement" as the top hit.
- The real fitment data lives in per-vehicle kit products. Each carries a structured
  product_type field like "LED for Honda > 2018-2021 Honda Accord" and a "Package
  Includes"/"Package Included" HTML section with factual per-position bulb sizes.
  Example — 2018-2021 Honda Accord kit: high beam 9005/HB3; rear turn BAU15S/7507;
  reverse 912/921/T15; dome/map T10/194. Notes which positions are already factory LED
  (low beam, fog, front turn, marker, brake, license) and need no bulb.
  Example — 2011-2016 Toyota Highlander kit: high 9005, low H11, fog H11 (2014-2016),
  brake 7443 red, front turn 7443 or 3157 amber/white by year split, rear turn 7440
  amber, backup T15, license/dome/map 6x T10.
  Example — 2011-2014 Hyundai Sonata kit: high H7, low H7 (or H11 hybrid), fog H11,
  backup T15, front turn 1157 white/amber, rear turn 1156 amber, brake/tail 1157 red,
  license 2x T10, map/dome 4x 31mm, trunk 2x 36mm.
- Public JSON available: /products.json (full catalog incl. product_type taxonomy and
  body_html with the per-position sizes) and /search/suggest.json — both answered 200
  with JSON on 2026-09-28. So the vehicle->sizes mapping is extractable via public
  Shopify endpoints, keyed off the per-vehicle kit products (coverage = whichever
  vehicles have a kit product; popular US models 2000s-2020s).
- Verdict: no dedicated fitment API, but fitment data is fully public as structured
  per-vehicle kit products via Shopify JSON endpoints. Data is factual.

### FAHREN (fahrenled.com / Amazon store)
- No fitment database published anywhere. Fahren sells per-bulb-size on Amazon and
  relies on Amazon's own ConfirmFit/Amazon Garage for vehicle matching. The domain
  fahrenled.org is a third-party review/affiliate site, not an official store; its
  "select your car model in the filter system" refers to Amazon's filter.
- Verdict: nothing usable.

### ALLA LIGHTING (allalighting.com)
- Has a "Shop By Year, Make, Model, Application" Vehicle Bulb Finder page at
  allalighting.com/pages/bulb-finder. The year/make/model/application dropdowns render
  via JS (fetched HTML shows only the page shell) — no public JSON endpoint found.
- Publishes factual per-model bulb-size lists in its YouTube install-video descriptions
  (static text, usable). Examples captured:
  - 2007-2013 Toyota Tundra: DRL 3157; door mirror 168; fog 9145; front side marker
    168; front turn 3157; high beam 9005; low beam H11; step-well 74; backup 921;
    brake 3157; center high-mount stop 921; license 168; parking 3157;
    rear side marker 3157; rear turn 3157; tail 3157.
  - 2018-2020 Ford Expedition: high beam (halogen) H9; low beam (halogen) 9005/HB3;
    fog H8; front park/turn 7444NAK; rear tail 3157; rear stop/turn 3057;
    backup 3057; license 168.
  - 2011-2019 Toyota Highlander: high beam/DRL 9005/HB3; low beam H11/H11LL;
    front turn/parking 3457NA (2011-2013) or 7444NA (2014-2018); fog PSX26W
    (2011-2013) or H16 (2014-2018); backup 921/W16W; brake/tail/rear side marker
    7443/7443-SCK; rear turn 7440NA/WY21W; license 168; dome 168; map 168;
    interior door 168; vanity 175; trunk DE3175.
- Site carries the standard disclaimer that the size guide is reference-only and may
  vary by trim.
- Verdict: no public API; usable factual data = the per-model size lists published in
  video descriptions (manual collection, not bulk-scrapable). Finder itself is
  JS-locked.

### DIODE DYNAMICS (diodedynamics.com)
- Magento-based site. Has a "Vehicle Finder" (Year/Make/Model) widget site-wide and
  per-vehicle landing pages at diodedynamics.com/by-vehicle/{make}/{model}.html and
  deeper URLs like /by-vehicle/subaru/wrx/2015-2021-subaru-wrx-led-lighting-upgrades.html.
  The per-vehicle pages list compatible products per position with prices
  (e.g. 2015-2021 WRX/STI page lists "Interior LED Conversion Kit",
  "Vanity Light LEDs (pair)", "Dome Light LEDs", "Map Light LEDs (pair)", etc.).
  Pages are JS-rendered; fetched raw HTML returned mostly chrome (some pages indexed
  with content in search snippets). No public JSON API found.
- Verdict: fitment data exists but is locked behind JS rendering; no public endpoint.
  Per-position product lists on vehicle pages confirm which positions they cover, but
  bulb SIZE facts are not published per position (they sell by application name).

---

## PART 2 — VEHICLE-SPECIFIC INTERIOR LED KITS

### Xotic Tech (xotictech.com) — Shopify; dedicated "vehicle-specific-product" collection
Page structure: title = "[N]x ... Kit ... Compatible with {Make} {Model} {years}";
body = Description ("Pure White N-lights LED package deal for {years} {Make} {Model}"),
"Package includes" bullet list with per-position counts, Features (6000K bright white,
360-degree output, 50,000 hr, plug-and-play, error-free), Installation note (LED
polarity: flip 180 deg if not lighting). Variants: White/Blue color options on some
listings. Install tool included on most listings ("FREE Installation Tool" /
"Installation tool" line item). Prices are per-kit (see below). Bulb SIZES are NOT
listed per position (they ship "the correct bulbs for the year/model of this listing").

Kits documented:
- Honda Accord 2013-2020, 12x, $13.99: 2x map, 1x dome, 4x vanity mirror/visor,
  2x door, 1x trunk, 2x license plate + install tool.
- Honda Accord 2013-2017, 14x (adds reverse): 2x map, 1x dome, 4x vanity, 2x door,
  1x trunk, 2x license plate, 2x backup reverse (Canbus) + install tool.
- Honda Accord 2018-2020, 8x, $11.00: 2x map, 1x dome, 2x vanity, 2x door, 1x trunk.
- Honda Accord 2013-2020 (alt listing), 12-14 pcs, $23.99: map, dome, vanity, door,
  cargo/trunk, license + install tool.
- Honda Accord 2013-2016, 10x: 2x map, 1x dome, 4x door, 1x trunk, 2x license;
  White/Blue variants.
- Honda Accord 1998-2002, 12x: 2x map, 1x dome, 4x door, 1x trunk, 2x license plate,
  2x reverse backup + free install tool.
- Toyota Camry 2007-2014 (with sunroof), 12x, $12.49: 3x map, 2x dome, 2x door,
  2x license plate, 1x trunk, 2x spare.
- Toyota Corolla 2003-2016, 8x, $12.99: 2x map, 1x dome, 1x trunk/cargo, 2x license.
- Toyota Corolla 2008-2013, 6x, $10.78: 2x map, 1x dome, 2x license, 1x trunk;
  White/Blue variants.
- Mazda 3 / Mazda 3 Sport 2004-2009, 9x, $12.99: 2x map, 1x dome, 2x trunk/cargo,
  2x license plate, 2x reverse backup.
- Toyota 4Runner 2003-2021, 15x: 2x map, 1x dome, 2x vanity, 4x door, 2x trunk,
  2x license plate, 2x backup reverse (Canbus).
- Toyota RAV4 2016-2020, 10x, $14.98: 2x map, 1x dome, 2x vanity, 1x trunk,
  2x license plate, 2x backup reverse (Canbus) + free install tool.
- Subaru XV Crosstrek 2013+, 4x, $16.99: 2x map, 1x dome, 1x trunk/cargo;
  White/Blue variants.
Price band: roughly $11-24 per vehicle kit. Shopify storefront = /products.json public.

### Diode Dynamics — Interior LED Conversion Kits (Stage 1 / Stage 2)
Sold on diodedynamics.com and through dealers. Per-vehicle SKUs (examples: DD0262,
DD0266, DD0263 = WRX variants; DD0604 = 2019+ Ford Ranger; DD0615 = 2009-2013
Subaru Forester).
Page structure: title = "Interior LED Conversion Kit for {years} {Make} {Model}";
"What's Included" / "In the Box" list with exact per-position bulb sizes and counts;
Stage 1 vs Stage 2 = two brightness tiers (Stage 1 slight increase over stock,
Stage 2 significant increase); color options cool white 6000K, blue, red (varies by
SKU); "Two plastic trim removal tools are included" (Ranger listing); 3-year warranty
cited on dealer pages.
Kits documented:
- Subaru WRX/STI 2015-2021: 2x 28mm vanity, 2x 31mm map, 2x 194 license plate,
  1x 194 trunk, 1x 31mm dome. DD price $42.26-$79.16 (reg $46.95-$87.95);
  dealer example DD0262 Stage 1 Blue $48.95.
- Chevrolet Tahoe 2007-2014 Stage 1: 2x 194 license plate, 2x 39mm front map,
  2x 39mm dome, 2x 921 cargo, 4x 28mm vanity, 2x 921 puddle.
- 2009-2013 Subaru Forester (DD0615) Stage 1 cool white: $52.95 list.
Fitment table on dealer pages lists Years/Make/Model explicitly.
Note: bulb SIZES per position ARE published (unlike Xotic Tech).

### PrecisionLED (precisionled.com)
Per-vehicle "Premium LED Interior Lights Kit" pages at
precisionled.com/{make}-{model}-premium-led-interior-package-{years}.html.
Page structure: title = "{years} {Make} {Model} Premium LED Interior Lights Kit";
a one-line "The {Make} {Model} ... Kit Includes:" summary in the format
"Map (2) + Dome (2) + Trunk (1)"; two product tiers (Premium 360 LEDs vs standard
5050 SMD); every kit includes professional trim removal tools + step-by-step install
videos + email/phone support. Bulb sizes per position NOT published (vehicle-specific
selection done for you).
Kits documented (Base kit contents):
- Chevrolet Trailblazer 2002-2009: Map (2) + Dome (2) + Trunk (1).
- Toyota Land Cruiser 1998-2007: Map (2) + Dome (3) + Courtesy (4) + Trunk (1).
- Audi S6 C5 Avant 1999-2003: Map (2) + Dome (4) + Courtesy (12) + Trunk (3).
- Dodge Durango 2004-2009: Map (4) + Dome (4) + Courtesy (4) + Trunk (2).

### iJDMTOY (store.ijdmtoy.com)
Vehicle-specific "Direct Fit"/"Exact Fit" interior packages. Page structure: title =
"The Brightest Exact Fit {N}-LED Interior Light Package For {Make} {Model}";
compatibility year list; "This package includes" with piece counts and LED-count
specs per piece (e.g. "90-SMD-3020 Exact Fit LED panel"); color options
(Xenon White default, Blue/Red on request); per-vehicle install guides published.
Bulb sizes given as product specs (T10, panel) rather than OEM cross-reference.
Kits documented:
- Scion FR-S 2013-2016 / Subaru BRZ 2013-2021 / Toyota 86 2017-2021, $20.99
  (SKU DF29-White): 1x 90-SMD-3020 panel (map/dome) + 1x 20-SMD-3020 T10 (trunk).
- Chevrolet Camaro 2010-2015: 1x 42-SMD panel (map/reading) + 3x 5-SMD T10
  (1x trunk, 2x license plate).
- Lexus IS300 2001-2005 (5-light set): 2x map/dome, 1x rear dome, 2x side door
  courtesy.
- VW MK4 platform (New Beetle 1999-2010, Bora 1999-2005, Golf/GTI/R32 1998-2006,
  Jetta 1999-2006, Passat B5 1996-2005): OE-fit full-LED rear reading/map/dome
  assembly pair, $30.99 (SKU 75-271-White), replaces OEM 3B0947291BY20.

### Auxito — per-vehicle FULL kits (interior + exterior in one product)
Not interior-only, but the per-vehicle kit structure is the closest comparable to a
"complete kit" program. "Package Includes" lists with per-position sizes; prices are
per kit (examples: "2018-2021 Honda Accord LED Bulb Replacement" price range
$21.99-$59.99 across variants).
- 2018-2021 Honda Accord: 2x 9005 (high beam), 2x BAU15S amber (rear turn),
  2x T15 white (backup), 4x T10 white (dome/map).
- 2011-2014 Hyundai Sonata: 2x H7 (high), 2x H7 (low, SE/GL trims) or 2x 9005
  (low, hybrid), 2x H11 (fog), 2x T15 (backup), 1157 white/amber (front turn),
  1156 amber (rear turn), 1157 red (brake/tail), 2x T10 (license),
  4x 31mm (map/dome), 2x 36mm (trunk).

### LASFIT — per-vehicle COMBO packages (full vehicle, interior + exterior)
Collection: lasfit.com/collections/combo-package-by-vehicle. "Combo Package Included"
lists with per-position counts; bulb sizes encoded in the Model No string; per-model
Bulb Size Chart tables (see Part 1). Example — 2019-2020 Hyundai Elantra:
2x 9005, 2x front turn, 2x rear turn, 2x backup, 2x license plate, 2x brake/tail,
2x front side marker, 2x dome, 2x map, 2x trunk, 2x vanity mirror.

### Alla Lighting / Fahren — interior kits
- Alla Lighting: no vehicle-specific interior kits found; sells per-bulb only.
- Fahren: no kits of any kind; per-bulb-size Amazon listings only.

---

## SUMMARY FOR PHASE 1
Usable fitment-data sources (factual, public):
1. SEALIGHT per-model HTML chart pages (scrape-friendly static tables).
2. LASFIT per-vehicle combo pages — static Bulb Size Chart tables (Location -> size).
3. Auxito per-vehicle kit products via public Shopify JSON (/products.json,
   /search/suggest.json) — product_type taxonomy + Package Includes sizes.
4. Alla Lighting per-model size lists in YouTube video descriptions (manual, factual).
Not usable as bulk sources: SEALIGHT /api/vehicle (dead to GET), Diode Dynamics
finder/vehicle pages (JS-rendered, no public API), LASFIT finder (JS, no public API),
Alla Lighting finder page (JS), Fahren (no data at all).
Interior-kit competitors with per-position structures: Xotic Tech (counts only, no
sizes, $11-24, tool included), Diode Dynamics (counts + sizes, Stage 1/2 tiers,
$43-88, trim tools included), PrecisionLED (counts only, 2 tiers, tools + videos),
iJDMTOY (piece specs, $21-31, install guides), Auxito + LASFIT (full-vehicle combos,
not interior-only).
