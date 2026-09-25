"""Product catalog for Bravo LEDs — Phase 1.

Each product family carries its tier, price, warranty, available bulb sizes
(canonical trade numbers) and color temps. A purchasable SKU is
product x bulb_size x color_temp; price is per pair/kit as described.

Tiers (from the existing store) — 1-year warranty on ALL products:
  Basic    $25
  Plus     $35
  Premium  $70
  Platinum $100
  Pro/Ultra/Accessory priced individually.
"""

import json

CATEGORIES = {
    "led-bulbs": {
        "name": "LED Bulbs",
        "tagline": "High-output LED bulbs in H11, 9005, 9006 and other popular sizes — for off-road and fog light use.",
        "icon": "bulb",
        "requires_dot_disclaimer": True,
    },
    "fog": {
        "name": "LED Fog Light Kits",
        "tagline": "Cut through rain and fog with high-output LED fog bulbs.",
        "icon": "fog",
        "requires_dot_disclaimer": True,
    },
    "turn": {
        "name": "Turn Signal LEDs",
        "tagline": "Bright amber and switchback turn signal upgrades.",
        "icon": "turn",
        "requires_dot_disclaimer": True,
    },
    "brake": {
        "name": "Brake Light LEDs",
        "tagline": "Instant-on brake lights, including strobe safety options.",
        "icon": "brake",
        "requires_dot_disclaimer": True,
    },
    "reverse": {
        "name": "Reverse / Backup LEDs",
        "tagline": "Flood your backup camera with clean white light.",
        "icon": "reverse",
        "requires_dot_disclaimer": True,
    },
    "interior": {
        "name": "Interior & License LEDs",
        "tagline": "Dome, map, dash and license plate upgrades.",
        "icon": "interior",
        "requires_dot_disclaimer": True,
    },
    "pods": {
        "name": "LED Pods & Bars",
        "tagline": "Pods, bars and harnesses for trucks and off-road rigs.",
        "icon": "pods",
        "requires_dot_disclaimer": True,
    },
    "strips": {
        "name": "LED Strip Kits",
        "tagline": "Multicolor accent strips with remote control.",
        "icon": "strips",
        "requires_dot_disclaimer": True,
    },
    "accessories": {
        "name": "Decoders & Accessories",
        "tagline": "CANbus decoders and resistors for error-free installs.",
        "icon": "accessories",
        "requires_dot_disclaimer": True,
    },
    # ---------------- Track 2: new primary categories ----------------
    # requires_dot_disclaimer controls the DOT product-page disclaimer.
    # Lighting categories show it; dash cams / jump starters / the new
    # accessory lines do not.
    "hid-conversion-kits": {
        "name": "HID Conversion Kits",
        "tagline": "35W and 55W HID conversion kits in popular bulb sizes — for off-road use.",
        "icon": "hid",
        "requires_dot_disclaimer": True,
    },
    "factory-hid-bulbs": {
        "name": "Factory HID Bulbs",
        "tagline": "OEM-style D1S / D2S / D3S / D4S HID replacement bulbs — for off-road use.",
        "icon": "hid",
        "requires_dot_disclaimer": True,
    },
    "led-miniature-bulbs": {
        "name": "LED Miniature Bulbs",
        "tagline": "194, 921, festoon and other miniature LED bulbs — for off-road use.",
        "icon": "mini",
        "requires_dot_disclaimer": True,
    },
    "hid-accessories": {
        "name": "HID Accessories",
        "tagline": "Ballasts, relay harnesses and warning cancellers for HID installs.",
        "icon": "acc",
        "requires_dot_disclaimer": False,
    },
    "led-accessories": {
        "name": "LED Accessories",
        "tagline": "Decoders, flasher relays and harnesses for LED installs.",
        "icon": "acc",
        "requires_dot_disclaimer": False,
    },
    "dash-cams": {
        "name": "Dash Cams",
        "tagline": "Front, dual-channel and 4K dash cameras with parking mode.",
        "icon": "dashcam",
        "requires_dot_disclaimer": False,
    },
    "jump-starters": {
        "name": "Jump Starters",
        "tagline": "Portable lithium jump starters for cars, trucks and SUVs.",
        "icon": "jump",
        "requires_dot_disclaimer": False,
    },
}

LEDBULB_SIZES = ["H11", "H7", "9005", "9006", "9012", "H13", "9004", "9007"]
FOG_SIZES = ["H11", "H8", "H10", "9145", "5202", "880", "881", "H16"]
TURN_SIZES = ["7440", "7443", "3157", "3156", "1156", "1157"]
BRAKE_SIZES = ["7440", "7443", "3157", "1157"]
REVERSE_SIZES = ["921", "7440", "912", "194"]

PRODUCTS = [
    # ---------------- LED Bulbs ----------------
    {
        "id": "basic-led-bulbs",
        "name": "Bravo Basic LED Bulbs",
        "category": "led-bulbs", "tier": "Basic",
        "price_cents": 2500, "warranty": "1-year warranty",
        "sizes": LEDBULB_SIZES, "color_temps": ["6000K", "8000K"],
        "badge": "Best Seller",
        "blurb": "Our entry LED conversion for off-road use. Twice the output of stock halogens, plug-and-play install in about 20 minutes.",
        "features": ["6000LM per pair", "Plug-and-play, no cutting", "Built-in compact driver", "1-year warranty"],
    },
    {
        "id": "plus-led-bulbs",
        "name": "Bravo Plus LED Bulbs",
        "category": "led-bulbs", "tier": "Plus",
        "price_cents": 3500, "warranty": "1-year warranty",
        "sizes": LEDBULB_SIZES, "color_temps": ["6000K", "8000K"],
        "blurb": "Step up in brightness and cooling over Basic — built for off-road use, with a 1-year warranty for daily drivers.",
        "features": ["8000LM per pair", "Upgraded heat dissipation", "Plug-and-play install", "1-year warranty"],
    },
    {
        "id": "premium-csp-led-bulbs",
        "name": "Bravo Premium CSP Series LED Bulbs — 60W",
        "category": "led-bulbs", "tier": "Premium",
        "price_cents": 7000, "warranty": "1-year warranty",
        "sizes": LEDBULB_SIZES, "color_temps": ["6000K", "8000K"],
        "badge": "Most Popular",
        "blurb": "CSP chip technology for a razor-sharp beam pattern with no glare. Our most popular bulbs for off-road use.",
        "features": ["60W, 12000LM per pair", "CSP LED chips — precise beam cutoff", "Aviation aluminum + turbo fan", "1-year warranty"],
    },
    {
        "id": "pro-zes-led-bulbs",
        "name": "Bravo Pro ZES Series LED Bulbs — 60W",
        "category": "led-bulbs", "tier": "Pro",
        "price_cents": 8000, "warranty": "1-year warranty",
        "sizes": LEDBULB_SIZES, "color_temps": ["6000K"],
        "blurb": "ZES chips tuned for projector housings — a precise beam for off-road use. Clean cutoff, zero flicker, CANbus friendly on most vehicles.",
        "features": ["60W ZES chips", "Optimized for projectors", "Anti-flicker driver", "1-year warranty"],
    },
    {
        "id": "ultra-u9-led-bulbs",
        "name": "Bravo Ultra U9 Series LED Bulbs — 90W",
        "category": "led-bulbs", "tier": "Ultra",
        "price_cents": 9000, "warranty": "1-year warranty",
        "sizes": LEDBULB_SIZES, "color_temps": ["6000K", "8000K"],
        "badge": "Brightest",
        "blurb": "90 watts of output for off-road use on rural roads and dark trails. Serious brightness, still plug-and-play.",
        "features": ["90W, 18000LM per pair", "Dual ball-bearing fan", "IP68 waterproof", "1-year warranty"],
    },
    {
        "id": "platinum-4070-led-bulbs",
        "name": "Bravo Platinum 4070 Series LED Bulbs — 130W",
        "category": "led-bulbs", "tier": "Platinum",
        "price_cents": 10000, "warranty": "1-year warranty",
        "sizes": LEDBULB_SIZES, "color_temps": ["6000K", "8000K"],
        "badge": "Flagship",
        "blurb": "Our flagship. 130W with 4070 automotive chips — the brightest LED bulbs we sell for off-road use.",
        "features": ["130W, 26000LM per pair", "4070-series LED chips", "Copper-core thermal design", "1-year warranty"],
    },
    # ---------------- Fog ----------------
    {
        "id": "basic-led-fog-kit",
        "name": "Bravo Basic LED Fog Light Kit",
        "category": "fog", "tier": "Basic",
        "price_cents": 2500, "warranty": "1-year warranty",
        "sizes": FOG_SIZES, "color_temps": ["6000K", "8000K", "3000K"],
        "blurb": "Affordable LED fog upgrade. 3000K golden yellow available for real foul-weather performance.",
        "features": ["Plug-and-play", "3000K yellow option", "1-year warranty"],
    },
    {
        "id": "plus-led-fog-kit",
        "name": "Bravo Plus LED Fog Light Kit",
        "category": "fog", "tier": "Plus",
        "price_cents": 3500, "warranty": "1-year warranty",
        "sizes": FOG_SIZES, "color_temps": ["6000K", "8000K", "3000K"],
        "blurb": "Brighter fog output with better thermal design than Basic.",
        "features": ["Higher lumen output", "3000K yellow option", "1-year warranty"],
    },
    {
        "id": "premium-led-fog-kit",
        "name": "Bravo Premium LED Fog Light Kit",
        "category": "fog", "tier": "Premium",
        "price_cents": 7000, "warranty": "1-year warranty",
        "sizes": FOG_SIZES, "color_temps": ["6000K", "8000K", "3000K"],
        "blurb": "CSP chips in a fog-specific beam. Wide, low, and glare-free.",
        "features": ["CSP chips", "Fog-specific beam pattern", "1-year warranty"],
    },
    {
        "id": "platinum-led-fog-kit",
        "name": "Bravo Platinum LED Fog Light Kit",
        "category": "fog", "tier": "Platinum",
        "price_cents": 10000, "warranty": "1-year warranty",
        "sizes": FOG_SIZES, "color_temps": ["6000K", "8000K", "3000K"],
        "blurb": "Maximum fog output we offer. Pairs perfectly with Platinum 4070 Series LED Bulbs.",
        "features": ["Flagship fog output", "3000K yellow option", "1-year warranty"],
    },
    {
        "id": "rav4-fog-upgrade-kit",
        "name": "Bravo Toyota RAV4 Fog Light LED Upgrade Kit (2006–2021)",
        "category": "fog", "tier": "Plus",
        "price_cents": 4500, "warranty": "1-year warranty",
        "sizes": ["H11"], "color_temps": ["6000K", "3000K"],
        "badge": "Vehicle Specific",
        "blurb": "Purpose-built for 2006–2021 RAV4 fog housings. Perfect fitment, no guesswork.",
        "features": ["RAV4-specific fitment", "OEM-style beam", "1-year warranty"],
    },
    # ---------------- Turn signal ----------------
    {
        "id": "basic-led-turn-signal",
        "name": "Bravo Basic LED Turn Signal Bulbs",
        "category": "turn", "tier": "Basic",
        "price_cents": 2500, "warranty": "1-year warranty",
        "sizes": TURN_SIZES, "color_temps": ["Amber", "6000K White"],
        "blurb": "Crisp instant-on turn signals. Add a resistor kit on older vehicles to prevent hyperflash.",
        "features": ["Instant on/off", "Amber or white", "1-year warranty"],
    },
    {
        "id": "premium-led-turn-signal",
        "name": "Bravo Premium LED Turn Signal Bulbs",
        "category": "turn", "tier": "Premium",
        "price_cents": 7000, "warranty": "1-year warranty",
        "sizes": TURN_SIZES, "color_temps": ["Amber", "6000K White"],
        "blurb": "High-output amber with built-in load resistance on most applications.",
        "features": ["High-output amber", "Built-in CANbus resistance (most apps)", "1-year warranty"],
    },
    {
        "id": "switchback-led-turn-signal",
        "name": "Bravo Premium Switchback LED Turn Signal (White/Amber)",
        "category": "turn", "tier": "Premium",
        "price_cents": 7500, "warranty": "1-year warranty",
        "sizes": ["7443", "3157"], "color_temps": ["Switchback"],
        "badge": "Staff Pick",
        "blurb": "Runs white as a DRL, switches to amber for turns. The clean modern front-end look.",
        "features": ["White DRL / amber turn", "No hyperflash on most vehicles", "1-year warranty"],
    },
    # ---------------- Brake ----------------
    {
        "id": "basic-led-brake",
        "name": "Bravo Basic LED Brake Light Bulbs",
        "category": "brake", "tier": "Basic",
        "price_cents": 2500, "warranty": "1-year warranty",
        "sizes": BRAKE_SIZES, "color_temps": ["Red"],
        "blurb": "LEDs light up 0.2s faster than incandescent — real stopping-distance safety.",
        "features": ["Instant-on safety", "Deep red output", "1-year warranty"],
    },
    {
        "id": "premium-led-brake",
        "name": "Bravo Premium LED Brake Light Bulbs",
        "category": "brake", "tier": "Premium",
        "price_cents": 7000, "warranty": "1-year warranty",
        "sizes": BRAKE_SIZES, "color_temps": ["Red"],
        "blurb": "Maximum red output for brake housings. Hard to miss, day or night.",
        "features": ["High-output red", "360° illumination", "1-year warranty"],
    },
    {
        "id": "strobe-led-brake",
        "name": "Bravo Premium Flash/Strobe LED Brake Light",
        "category": "brake", "tier": "Premium",
        "price_cents": 7500, "warranty": "1-year warranty",
        "sizes": ["7440", "7443", "3157"], "color_temps": ["Red"],
        "badge": "Safety Pick",
        "blurb": "Flashes 3x then goes solid when you brake. Proven to grab tailgaters' attention.",
        "features": ["Triple-flash then solid", "Plug-and-play", "1-year warranty"],
    },
    # ---------------- Reverse ----------------
    {
        "id": "basic-led-reverse",
        "name": "Bravo Basic LED Reverse Bulbs",
        "category": "reverse", "tier": "Basic",
        "price_cents": 2500, "warranty": "1-year warranty",
        "sizes": REVERSE_SIZES, "color_temps": ["6000K"],
        "blurb": "See — and be seen — in reverse. Big upgrade for backup cameras at night.",
        "features": ["Bright white output", "Backup-camera friendly", "1-year warranty"],
    },
    {
        "id": "premium-led-reverse",
        "name": "Bravo Premium LED Reverse Bulbs",
        "category": "reverse", "tier": "Premium",
        "price_cents": 7000, "warranty": "1-year warranty",
        "sizes": REVERSE_SIZES, "color_temps": ["6000K"],
        "blurb": "Flood-pattern reverse LEDs that turn night into day behind you.",
        "features": ["Flood beam pattern", "6000K white", "1-year warranty"],
    },
    {
        "id": "ultra-led-reverse",
        "name": "Bravo Ultra LED Reverse Bulbs",
        "category": "reverse", "tier": "Ultra",
        "price_cents": 8500, "warranty": "1-year warranty",
        "sizes": REVERSE_SIZES, "color_temps": ["6000K"],
        "badge": "Brightest",
        "blurb": "The brightest reverse bulbs we make. For trucks, trailers and dark driveways.",
        "features": ["Maximum reverse output", "Projector lens design", "1-year warranty"],
    },
    # ---------------- Interior ----------------
    {
        "id": "interior-t10-kit",
        "name": "Bravo Interior LED Kit — T10 / 194 / 168 (10-pack)",
        "category": "interior", "tier": "Plus",
        "price_cents": 1800, "warranty": "1-year warranty",
        "sizes": ["T10", "194", "168"], "color_temps": ["6000K", "8000K"],
        "blurb": "Do the whole interior in one shot: dome, map, trunk, glove box and more.",
        "features": ["10 bulbs", "Fits most interiors", "1-year warranty"],
    },
    {
        "id": "festoon-dome-kit",
        "name": "Bravo Festoon Dome / Map LED Kit — 31 / 36 / 42mm",
        "category": "interior", "tier": "Plus",
        "price_cents": 2000, "warranty": "1-year warranty",
        "sizes": ["31mm", "36mm", "42mm"], "color_temps": ["6000K"],
        "blurb": "Rigid festoon replacements for dome and map lights. No hot spots, even glow.",
        "features": ["Even 360° glow", "Multiple lengths", "1-year warranty"],
    },
    {
        "id": "license-plate-kit",
        "name": "Bravo License Plate LED Kit",
        "category": "interior", "tier": "Basic",
        "price_cents": 1200, "warranty": "1-year warranty",
        "sizes": ["194", "T10"], "color_temps": ["6000K"],
        "blurb": "Clean white plate lighting to match the rest of your LED swap.",
        "features": ["Pair included", "Error-free", "1-year warranty"],
    },
    # ---------------- Pods ----------------
    {
        "id": "led-pod-single",
        "name": "Bravo LED Pod — Single",
        "category": "pods", "tier": "Pro",
        "price_cents": 3500, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": ["6000K"],
        "blurb": "Compact 3\" cube pod. Ditch lights, bumpers, roof racks — mounts anywhere.",
        "features": ["Spot or flood", "IP68 waterproof", "1-year warranty"],
    },
    {
        "id": "led-pod-pair",
        "name": "Bravo LED Pod — Pair",
        "category": "pods", "tier": "Pro",
        "price_cents": 6000, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": ["6000K"],
        "badge": "Value",
        "blurb": "A pair of pods at a bundle price. The classic A-pillar ditch light setup.",
        "features": ["Two pods", "Mounting brackets included", "1-year warranty"],
    },
    {
        "id": "led-pod-harness",
        "name": "Bravo LED Pod Wiring Harness",
        "category": "pods", "tier": "Accessory",
        "price_cents": 1500, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Relay harness with switch for one or two pods. Fuse-protected, plug-and-play.",
        "features": ["40A relay", "In-cab switch", "Fuse protected"],
    },
    # ---------------- Strips ----------------
    {
        "id": "strip-kit-small",
        "name": "Bravo Multicolor LED Strip Kit — Small",
        "category": "strips", "tier": "Plus",
        "price_cents": 3000, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": ["RGB"],
        "blurb": "Footwells, grilles, interiors — millions of colors with remote control.",
        "features": ["Remote + app control", "Music sync mode", "1-year warranty"],
    },
    {
        "id": "strip-kit-large",
        "name": "Bravo Multicolor LED Strip Kit — Large",
        "category": "strips", "tier": "Plus",
        "price_cents": 4500, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": ["RGB"],
        "blurb": "Full underglow-length kit. Enough strip for a complete install.",
        "features": ["Extended length", "Remote + app control", "1-year warranty"],
    },
    # ---------------- Accessories ----------------
    {
        "id": "led-decoder",
        "name": "Bravo LED Decoder — CANbus Anti-Flicker (Pair)",
        "category": "accessories", "tier": "Accessory",
        "price_cents": 1500, "warranty": "1-year warranty",
        "sizes": ["H11", "9005", "9006", "H7", "9012"], "color_temps": [],
        "blurb": "Kills flicker and 'bulb out' warnings on CANbus vehicles. Plugs inline in seconds.",
        "features": ["Anti-flicker capacitors", "Clears bulb-out errors", "Plug-and-play"],
    },
    {
        "id": "resistor-kit",
        "name": "Bravo Load Resistor Kit for LED Turn Signals",
        "category": "accessories", "tier": "Accessory",
        "price_cents": 1200, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Stops hyperflash when upgrading to LED turn signals on older vehicles.",
        "features": ["50W 6-ohm", "Quick-splice taps included", "Mounts to metal"],
    },
    # ---------------- Track 2: HID Conversion Kits (all DRAFT) ----------------
    {
        "id": "hid-35w-kit-h11",
        "name": "Bravo 35W HID Conversion Kit (H11)",
        "category": "hid-conversion-kits", "tier": "HID",
        "price_cents": 8999, "warranty": "1-year warranty",
        "sizes": ["H11", "H7", "9005", "9006", "9012", "H10", "880"],
        "color_temps": ["6000K", "8000K", "3000K"],
        "blurb": "Complete 35W HID conversion for off-road use: slim digital ballasts, OEM-grade wiring and a pair of xenon bulbs in your size.",
        "features": ["35W slim digital ballasts", "IP67 waterproof", "Plug-and-play wiring", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "hid-55w-kit-9005",
        "name": "Bravo 55W HID Conversion Kit (9005)",
        "category": "hid-conversion-kits", "tier": "HID",
        "price_cents": 9999, "warranty": "1-year warranty",
        "sizes": ["9005", "9006", "H11", "H7", "9012"],
        "color_temps": ["6000K", "8000K"],
        "blurb": "High-output 55W HID conversion for off-road use. Roughly 40% brighter than 35W kits — best for projector housings.",
        "features": ["55W fast-start ballasts", "40% brighter than 35W", "Heavy-gauge wiring", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "hid-slim-kit-h7",
        "name": "Bravo Slim Ballast HID Conversion Kit (H7)",
        "category": "hid-conversion-kits", "tier": "HID",
        "price_cents": 8499, "warranty": "1-year warranty",
        "sizes": ["H7", "H1", "H3"],
        "color_temps": ["6000K", "8000K"],
        "blurb": "Ultra-slim ballasts that tuck into tight European housings. A clean HID upgrade for off-road use.",
        "features": ["Ultra-slim ballast design", "CANbus-ready", "Plug-and-play", "1-year warranty"],
        "draft": True,
    },
    # ---------------- Track 2: Factory HID Bulbs (all DRAFT) ----------------
    {
        "id": "factory-hid-d2s",
        "name": "Bravo Factory HID Bulb D2S",
        "category": "factory-hid-bulbs", "tier": "OEM",
        "price_cents": 4999, "warranty": "1-year warranty",
        "sizes": ["D2S"], "color_temps": ["4300K", "6000K", "8000K"],
        "blurb": "OEM-spec D2S xenon replacement bulb for off-road use. Matches factory output and fitment exactly.",
        "features": ["OEM D2S specification", "Pair included", "Direct factory replacement", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "factory-hid-d1s",
        "name": "Bravo Factory HID Bulb D1S",
        "category": "factory-hid-bulbs", "tier": "OEM",
        "price_cents": 4999, "warranty": "1-year warranty",
        "sizes": ["D1S"], "color_temps": ["4300K", "6000K", "8000K"],
        "blurb": "OEM-spec D1S xenon replacement bulb for off-road use. Built-in igniter, just like the factory part.",
        "features": ["OEM D1S specification", "Built-in igniter", "Direct factory replacement", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "factory-hid-d3s",
        "name": "Bravo Factory HID Bulb D3S",
        "category": "factory-hid-bulbs", "tier": "OEM",
        "price_cents": 5499, "warranty": "1-year warranty",
        "sizes": ["D3S"], "color_temps": ["4300K", "6000K"],
        "blurb": "OEM-spec D3S mercury-free xenon replacement bulb for off-road use.",
        "features": ["OEM D3S specification", "Mercury-free", "Direct factory replacement", "1-year warranty"],
        "draft": True,
    },
    # ---------------- Track 2: LED Miniature Bulbs (all DRAFT) ----------------
    {
        "id": "led-mini-194-t10",
        "name": "Bravo LED Miniature Bulb 194/T10",
        "category": "led-miniature-bulbs", "tier": "Basic",
        "price_cents": 1299, "warranty": "1-year warranty",
        "sizes": ["194", "T10", "168", "2825"], "color_temps": ["6000K", "Amber", "Red"],
        "blurb": "Direct LED replacement for 194/T10 miniature bulbs for off-road use. Dome, map, license, trunk and side markers.",
        "features": ["10-pack available in-store", "CANbus error-free (most apps)", "360° illumination", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "led-mini-921",
        "name": "Bravo LED Miniature Bulb 921",
        "category": "led-miniature-bulbs", "tier": "Basic",
        "price_cents": 1499, "warranty": "1-year warranty",
        "sizes": ["921", "912"], "color_temps": ["6000K"],
        "blurb": "High-output 921 LED replacement for off-road use. Popular for reverse, trunk and cargo lights.",
        "features": ["High-output 921 base", "Plug-and-play", "6000K white", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "led-mini-festoon",
        "name": "Bravo DE3175 Festoon Mini LED",
        "category": "led-miniature-bulbs", "tier": "Plus",
        "price_cents": 1499, "warranty": "1-year warranty",
        "sizes": ["DE3175", "DE3022", "6418"], "color_temps": ["6000K"],
        "blurb": "Rigid festoon LED for dome and map housings, for off-road use. Even glow with no hot spots.",
        "features": ["Fits DE3175/DE3022 housings", "Even 360° glow", "Cool-running", "1-year warranty"],
        "draft": True,
    },
    # ---------------- Track 2: HID Accessories (all DRAFT) ----------------
    {
        "id": "hid-ballast-35w",
        "name": "Bravo HID Ballast 35W",
        "category": "hid-accessories", "tier": "Accessory",
        "price_cents": 2999, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Replacement 35W slim digital ballast for HID conversion kits. Sealed and waterproof, plugs into standard HID wiring.",
        "features": ["35W digital ballast", "IP67 waterproof", "Universal HID connector", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "hid-relay-harness",
        "name": "Bravo HID Relay Wiring Harness",
        "category": "hid-accessories", "tier": "Accessory",
        "price_cents": 1999, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Powers HID ballasts straight from the battery so your factory wiring never sees the startup surge. Includes fuse and relay.",
        "features": ["40A fused relay", "Battery-direct power", "Plug-and-play connectors", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "hid-warning-canceller",
        "name": "Bravo HID Capacitor / Warning Canceller",
        "category": "hid-accessories", "tier": "Accessory",
        "price_cents": 1499, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Clears flicker and dashboard 'bulb out' warnings on CANbus vehicles running HID kits. Plugs inline in seconds.",
        "features": ["Anti-flicker capacitors", "Clears bulb-out errors", "Pair included", "1-year warranty"],
        "draft": True,
    },
    # ---------------- Track 2: LED Accessories (all DRAFT) ----------------
    {
        "id": "led-decoder-resistor-kit",
        "name": "Bravo LED Decoder / Resistor Kit",
        "category": "led-accessories", "tier": "Accessory",
        "price_cents": 1899, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Universal decoder and resistor kit that stops hyperflash and bulb-out warnings when switching to LEDs.",
        "features": ["Hyperflash fix", "CANbus compatible", "Quick-splice taps included", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "led-flasher-relay",
        "name": "Bravo LED Flasher Relay (CF13)",
        "category": "led-accessories", "tier": "Accessory",
        "price_cents": 1599, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Electronic flasher relay for LED turn signals. Replaces the stock relay — no resistors or splicing needed.",
        "features": ["No resistors needed", "OEM plug fitment", "Correct flash rate", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "led-extension-harness",
        "name": "Bravo LED Extension Wiring Harness",
        "category": "led-accessories", "tier": "Accessory",
        "price_cents": 1299, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Weatherproof extension harness for LED installs that need extra reach. Sealed connectors, 16-gauge wire.",
        "features": ["16-gauge wire", "Sealed connectors", "1-year warranty"],
        "draft": True,
    },
    # ---------------- Track 2: Dash Cams (all DRAFT) ----------------
    {
        "id": "dash-cam-dual-1080p",
        "name": "Bravo Dash Cam — Dual Channel 1080p",
        "category": "dash-cams", "tier": "Electronics",
        "price_cents": 12999, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Front + rear 1080p recording with parking mode, G-sensor incident lock and loop recording. Universal windshield mount.",
        "features": ["Front + rear 1080p", "Parking mode", "G-sensor incident lock", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "dash-cam-4k",
        "name": "Bravo Dash Cam — 4K Front + 1080p Rear",
        "category": "dash-cams", "tier": "Electronics",
        "price_cents": 19999, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Flagship 4K front camera with 1080p rear, GPS logging and Wi-Fi app playback. Reads plates day and night.",
        "features": ["4K front / 1080p rear", "Built-in GPS", "Wi-Fi app playback", "1-year warranty"],
        "draft": True,
    },
    # ---------------- Track 2: Jump Starters (all DRAFT) ----------------
    {
        "id": "jump-starter-2000a",
        "name": "Bravo Jump Starter 2000A",
        "category": "jump-starters", "tier": "Power",
        "price_cents": 8999, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "2000A peak lithium jump starter for gas engines up to 8.0L and diesel up to 6.0L. USB-C fast charging and LED work light built in.",
        "features": ["2000A peak current", "Gas to 8.0L / diesel to 6.0L", "USB-C fast charge", "1-year warranty"],
        "draft": True,
    },
    {
        "id": "jump-starter-3000a-pro",
        "name": "Bravo Jump Starter 3000A Pro",
        "category": "jump-starters", "tier": "Power",
        "price_cents": 12999, "warranty": "1-year warranty",
        "sizes": ["Universal"], "color_temps": [],
        "blurb": "Pro-grade 3000A jump starter for trucks, SUVs and diesels. Smart-clamp safety protection and a 100-lumen work light.",
        "features": ["3000A peak current", "Smart-clamp protection", "100-lumen work light", "1-year warranty"],
        "draft": True,
    },
]


# ---------------------------------------------------------------- sale pricing + bundles (Track B)
# Sale pricing lives in the DB column `sale_price_cents`, added idempotently
# by content.ensure_content_schema(). None (NULL) means "not on sale" —
# no product ships with a sale price; the owner sets them later.

SALE_PRICE_DEFAULT = None


def product_sale_price(product, default=SALE_PRICE_DEFAULT):
    """Effective sale price in cents, or None when the product isn't on sale."""
    return product.get("sale_price_cents", default)


def is_on_sale(product):
    return product_sale_price(product) is not None


def bundle_products(bundle, lookup):
    """Resolve a bundle dict's product_ids via a lookup callable
    (e.g. db.get_product). Missing ids are skipped, never fabricated."""
    return [p for pid in json.loads(bundle["product_ids"])
            if (p := lookup(pid))]


def bundle_regular_total(bundle, lookup):
    """Sum of the member products' regular prices, in cents."""
    return sum(p["price_cents"] for p in bundle_products(bundle, lookup))


def bundle_savings(bundle, lookup):
    """How much cheaper the bundle is than buying members separately."""
    return max(0, bundle_regular_total(bundle, lookup) - bundle["price_cents"])
