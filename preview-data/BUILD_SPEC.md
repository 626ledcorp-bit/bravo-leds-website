# 626 LEDs storefront — static preview build spec

## What this is
A shareable, clickable static preview of the 626 LEDs automotive LED storefront
(Phase 1). The production codebase is a Flask app at `~/workspace/626leds/website/`;
this preview mirrors its look, data and flows but simulates server behavior in
the browser (localStorage cart). Nothing here is real commerce.

## Data (embed at build time — read these files)
- `~/workspace/626leds/preview-data/products.json` — 30 product families:
  id, name, category, tier, price_cents, warranty, sizes[], color_temps[],
  badge, blurb, features[], icon
- `~/workspace/626leds/preview-data/vehicles.json` — 10 demo vehicles, each with
  fitment rows: position, label, size (e.g. "H11"), note, categories[]

Categories (9): headlight, fog, turn, brake, reverse, interior, pods, strips,
accessories. Price tiers: Basic $25 / Plus $35 / Premium $70 / Platinum $100 /
Ultra-Pro $40–70.

## Views (single-page app with client-side routing is fine)
1. **Home** — dark hero with amber/orange glow headline "Brighter Nights Start
   Here", vehicle fitment finder (Year → Make → Model → optional Trim cascading
   selects driven by vehicles.json, "Find My Bulbs" button), category cards,
   "Customer Favorites" product grid (badge items), 4 value props
   (Fast Shipping 1–2 days / 30-Day Returns / Real Warranties / Local Experts Rosemead).
2. **Shop** — all products + category filter; product cards with tier, name,
   sizes, warranty, price.
3. **Product detail** — tier, name, price, warranty line, blurb, bulb-size pills,
   color-temp pills, quantity stepper, Add to Cart, feature checklist, trust row,
   related products.
4. **Fitment results** — vehicle banner ("2018 Toyota Camry"), one block per
   lighting position showing the required bulb size chip + matching product tiers
   (products whose sizes[] include that size and category matches the row's
   categories[]); show a "Demo fitment data — full database coming soon" pill.
5. **Cart** — line items with size/color/qty steppers and remove, order summary,
   free-shipping-over-$99 note; cart persists in localStorage.
6. **Checkout** — "Secure Checkout Coming Soon" placeholder.
7. **Info** — Shipping (1–2 business days, USPS/UPS, free over $99), Returns
   (30 days unused; Basic 1 mo / Plus 3 mo / Premium+ 12 mo), Contact (Rosemead, CA).

## Design
Dark automotive-aftermarket theme: near-black background (#0a0b0d), orange/amber
accent (#ffa21a → #ff6b1a), glowing logo mark "626", card-based layout,
mobile-first responsive (2-col grids on phones, 3–4 on desktop). Product images:
draw simple glowing line-icon SVGs per category (bulb, fog, signal, brake,
backup, dome, pod, strip, plug) — placeholders for real photos.

## Hero image
If a hero photo is needed, generate a dark cinematic AI image: sports car front
with glowing amber LED headlights at night. Otherwise a CSS gradient glow is fine.
