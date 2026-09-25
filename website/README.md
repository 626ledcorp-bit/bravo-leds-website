# Bravo LEDs — Phase 1 Storefront

Flask + SQLite storefront with a vehicle fitment finder. Phase 1: catalog,
vehicle → bulb-size → matching products, session cart. No live payments yet
(`/checkout` is a "coming soon" page wired for Stripe Checkout in Phase 2).

## Run locally

```bash
cd website
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py        # http://127.0.0.1:5000
```

Production-style: `gunicorn --bind 0.0.0.0:8000 app:app`

## Layout

- `app.py` — routes: home, shop, product, fitment API + results, cart, checkout(soon), info pages
- `catalog.py` — product catalog (~50 families across 16 categories;
  19 new-category products are DRAFT — see below)
- `db.py` — SQLite product store; prices always re-looked-up server-side
- `fitment_loader.py` — **fitment data swap point** (see below)
- `seed_fitment.py` — demo fitment data (10 popular vehicles, unverified)
- `templates/` + `static/` — Jinja templates, dark automotive CSS, finder JS
- `Dockerfile`, `render.yaml` — Render deployment config

## Fitment data: seed vs live

`fitment_loader.py` auto-detects real crawl output in `../fitment/`
(override with the `FITMENT_DIR` env var):

1. `fitment.json` → `{"vehicles": [...], "fitment": [...]}`
2. `fitment.db` / `fitment.sqlite` → tables `vehicles`, `fitment`
3. `vehicles.csv` + `fitment.csv`

Schema: `vehicles(vehicle_id, year, make, model, trim)`,
`fitment(vehicle_id, position, bulb_size_raw, bulb_size, note)`.

If none is found it falls back to `seed_fitment.py` (demo data — the results
page shows a "Demo fitment data" pill). Call `fitment_db.reload()` after the
crawl lands to pick up new data without restarting.

## SEMA Data (plug-and-play for later)

The site is ready for a SEMA Data subscription with no code changes.
Per SEMA's public docs, a subscriber (reseller/receiver) gets manufacturer
data in industry-standard **ACES** (fitment: which part fits which vehicle)
and **PIES** (product info) formats, via XML/XLS exports (about 500 brands,
~4.5M SKUs). The importer below turns those exports into the same
`vehicles`/`fitment` tables the finder already reads.

Day-one steps:

1. In SEMA Data, export ACES application data for the lighting brands you
   carry (ACES XML preferred; an XLS export works via the flat CSV path —
   save it as `sema_fitment_*.csv` with the columns below).
2. Drop the files into `~/workspace/626leds/sema/inbox/`:
   - `aces_*.xml` — ACES app records (`BaseVehicleID` → part number,
     plus position/qualifier text when present)
   - `vcdb_vehicles.csv` — `base_vehicle_id,year,make,model,trim`.
     ACES keys vehicles by VCdb BaseVehicleID (just a number); this file
     turns it back into year/make/model. Source it from a VCdb subscription
     export, or from the vehicle reference bundled in your SEMA download.
   - `sema_fitment_*.csv` (optional fallback) — columns:
     `vcdb_vehicle_id,year,make,model,trim,position,part_number,note`
     (`year/make/model` may be omitted if `vcdb_vehicles.csv` covers the ID)
3. Extend `~/workspace/626leds/sema/part_number_map.json` with your real
   SKUs. Run the importer once, read the `review` table for
   `unmapped_part` entries, add them under `exact` (safest), re-run.
4. Run the import:
   ```bash
   cd website && python3 sema_import.py
   # → writes ~/workspace/626leds/sema/sema_fitment.db
   ```
   Options: `--inbox DIR --out PATH --map PATH --vcdb PATH`.
5. The site picks it up automatically — SEMA data is **preferred over
   crawl data** when present (`fitment_db.reload()` or restart). The
   "Demo fitment data" pill disappears because the source becomes `live`.

How part-number → bulb-size mapping works (`sema_import.py`):

- `exact` dict first (case-insensitive, punctuation stripped), then
  `regex` rules in order. Starter map covers obvious bulb-size-embedded
  SKUs (`H11ST` → `H11`, `9005CB` → `9005`, `9145ST` → `H10`, …).
- Position comes from ACES `<Position>` / `<Qualifiers>` text mapped to
  canonical positions (`low_beam`, `high_beam`, …). Bare "turn signal"
  (no front/rear) is deliberately left unmapped.
- **Anything unmapped — unknown part, unknown vehicle ID, unknown
  position — lands in the `review` table and is never fabricated into
  the fitment tables.** Resolve via QA, extend the map, re-run.
- The `vehicles` table carries nullable `vcdb_vehicle_id` /
  `base_vehicle_id` columns (VCdb-ready); existing crawl data without
  them keeps working.

What couldn't be verified from public docs (no subscription): the exact
column layout of SEMA Data's XLS receiver exports (hence the documented
flat-CSV fallback alongside ACES XML), whether their exports bundle a
vehicle reference table (hence the `vcdb_vehicles.csv` step), and receiver
API details. If the real export differs, adjust `parse_aces` /
`parse_flat_csv` in `sema_import.py` — the schema and loader need no
changes.

## Env vars

| Var | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | `626leds-dev-secret` | Flask session signing — **set in production** |
| `FITMENT_DIR` | `../fitment` | Where the crawl writes its output |
| `SEMA_DIR` | `../sema` | SEMA import dir (`sema_fitment.db` preferred over crawl data) |
| `STORE_DB` | `website/data/store.db` | SQLite path |
| `SQUARE_ACCESS_TOKEN` | (none — set via /admin Settings) | Square POS API token; env overrides the saved setting |
| `SQUARE_LOCATION_ID` | (none — set via /admin Settings) | Square location ID; env overrides the saved setting |

## Square POS inventory sync

The website is the catalog master (little item data lives in Square today).
`/admin/square` (linked from the dashboard and Settings):

- **Push catalog to Square** — creates Square catalog items + item variations
  from active website products (Catalog API batch-upsert), matched on SKU:
  a variation that already has a stored Square ID is updated in place, an
  existing Square SKU is adopted, otherwise it is created. Inventory tracking
  is enabled on every created variation.
- **Two-way counts** — when a website order is paid and the auto-sync toggle
  is on, the post-sale counts are pushed to Square (Inventory API
  `batch-change` with physical counts). Square-side sales hit
  `/webhooks/square`, whose HMAC-SHA1 signature is verified before
  `inventory.count.updated` events update website stock by object-ID mapping.
- **Conflict policy** — last-write-wins; every change is logged with old →
  new in the sync log; counts never go below 0 (clamps are flagged).
- Manual per-product Push/Pull buttons, a connection test, and the sync log
  viewer live on the same page. Secrets are stored server-side only, never
  rendered unmasked, never logged.

Square setup for the owner: in the Square Developer Dashboard create an app,
copy the access token (sandbox first), paste it plus the location ID in
/admin Settings, run Test connection, then Push catalog. For the webhook,
create a webhook subscription for `inventory.count.updated` pointing at
`https://<your-domain>/webhooks/square` and paste its signature key in
Settings.

## Product photography — image specs (owner to shoot)

Product photos are currently placeholders (`static/img/ph-*.svg`). When the
owner shoots real photos, export them at these specs:

| Slot | File pattern | Size | Notes |
|---|---|---|---|
| Product card (shop / home / related) | `static/img/products/<product-id>-card.jpg` | 800 × 800 px, JPG ≤ 200 KB | Square, product centered on neutral background |
| Product detail (main) | `static/img/products/<product-id>-main.jpg` | 1200 × 1200 px, JPG ≤ 400 KB | Same angle as card, higher res for zoom |
| Product detail (alt angles) | `static/img/products/<product-id>-2.jpg`, `-3.jpg` | 1200 × 1200 px, JPG ≤ 400 KB | Optional: packaging, beam shot, size comparison |
| Hero | `static/img/hero.jpg` | 1920 × 800 px, JPG ≤ 500 KB | Dark background; keep left third low-contrast for headline text |

Keep filenames lowercase, matching the product `id` in the catalog
(e.g. `premium-csp-led-bulbs-card.jpg`). Alt text should name the product
and tier, never the word "headlight".

## New product categories (Track 2) — all products DRAFT

Seven primary categories were added to `catalog.py`, matching the product
lines the store already carries (mirroring ledlightstreet.com's lineup).
"LED Headlight Kits" maps to the existing **LED Bulbs** category — it was
deliberately **not** created as a separate category.

| Category (slug) | Products seeded | DOT disclaimer on product pages |
|---|---|---|
| HID Conversion Kits (`hid-conversion-kits`) | 3: 35W H11, 55W 9005, slim H7 | yes |
| Factory HID Bulbs (`factory-hid-bulbs`) | 3: D2S, D1S, D3S | yes |
| LED Miniature Bulbs (`led-miniature-bulbs`) | 3: 194/T10, 921, DE3175 festoon | yes |
| HID Accessories (`hid-accessories`) | 3: 35W ballast, relay harness, warning canceller | no |
| LED Accessories (`led-accessories`) | 3: decoder/resistor kit, flasher relay, extension harness | no |
| Dash Cams (`dash-cams`) | 2: dual 1080p, 4K front + 1080p rear | no |
| Jump Starters (`jump-starters`) | 2: 2000A, 3000A Pro | no |

Details:

- Every seeded product has `draft=True` in `catalog.py` and is seeded with
  `draft = 1` in the `products` table (column added idempotently by
  `db.init_db()`; existing orders/newsletter/reviews data is untouched).
- **Draft products are invisible publicly**: excluded from `/shop`, all
  `/shop/<category>` pages, `/product/<id>` (404), the homepage featured
  list, and the fitment finder. They appear in `/admin` → Products with a
  **"DRAFT — needs real SKU/price/photo"** badge and a Publish/Unpublish
  toggle. Publishing sets `draft = 0`; re-seeds never overwrite the
  admin's publish choice.
- The DOT disclaimer ("For off-road and fog light use only…", linked to
  `/dot-compliance`) renders on product pages only for categories flagged
  `requires_dot_disclaimer: True` in `catalog.py` (all lighting categories;
  not dash cams, jump starters, or the two accessory lines). The footer
  disclaimer and the fitment-results disclaimer are unchanged.
- Fitment: dome/map positions now suggest `led-miniature-bulbs`; low/high
  beam also surface HID conversion kits and factory HID bulbs. Dash cams
  and jump starters are universal-fit — mapped to no position, so the
  finder can never suggest them.
- Product photos are the new `ph-hid.svg`, `ph-mini.svg`, `ph-acc.svg`,
  `ph-dashcam.svg`, `ph-jump.svg` placeholders.

### What the owner must supply before publishing each draft

Real SKU/part number, real price (seed prices are sensible placeholders),
real product photos (see specs above), and confirmation of the size/color
options. Then hit **Publish** in `/admin` → Products.

## Phase 2 (later)

Stripe Checkout, order records, admin panel, inventory, optional Square/POS
integration. Needed from the owner: real product photos, final logo/brand,
final SKU list + prices, Stripe keys, hosting account + token, domain/DNS,
shipping/tax settings.

## Phase 2 setup (Stripe checkout, orders, admin, email)

Phase 2 is implemented and live behind env-var configuration. All secrets
come from environment variables — never from the repo.

### Environment variables

| Var | Required? | Default | Purpose |
|---|---|---|---|
| `STRIPE_SECRET_KEY` | to take payments | — | Stripe secret key (`sk_test_…` for test mode). Without it, `/checkout` shows a graceful "payments not configured" message instead of crashing. |
| `STRIPE_PUBLISHABLE_KEY` | no (documented) | — | Publishable key (`pk_test_…`); not needed server-side since buyers are redirected to Stripe's hosted payment page. Listed here so deploy config keeps the pair together. |
| `STRIPE_WEBHOOK_SECRET` | for webhooks | — | Signing secret (`whsec_…`) for the `/stripe/webhook` endpoint. Without it, webhook events are rejected (fail closed). |
| `ADMIN_PASSWORD` | for admin | — | Password for `/admin/login`. **If unset, all admin access is denied (fail closed).** Successful login sets `session["admin_authed"] = True`; every admin route checks `session.get("admin_authed")`. |
| `SMTP_HOST` | to send email | — | SMTP hostname (e.g. `smtp.gmail.com`). If unset, all email is skipped gracefully (logged, no crash). |
| `SMTP_PORT` | no | `587` | `465` uses implicit TLS; anything else uses STARTTLS. |
| `SMTP_USER` / `SMTP_PASS` | for auth | — | SMTP login. `SMTP_FROM` defaults to `SMTP_USER` if unset. |
| `SMTP_FROM` | no | `SMTP_USER` | From address on outgoing mail. |
| `ORDER_NOTIFY_EMAIL` | no | `zoltar.works@gmail.com` | Owner notification address for every paid order. **The default is a test address** — the owner plans to create a dedicated orders email later and swap it in via this env var. |
| `SECRET_KEY` | yes (prod) | `626leds-dev-secret` | Flask session signing — **set in production**. |
| `STORE_DB` | no | `website/data/store.db` | SQLite path. `orders` and `inventory` tables are created with `CREATE TABLE IF NOT EXISTS`; existing data is never deleted or reseeded. |

### How to get Stripe test keys

1. Create a Stripe account at https://dashboard.stripe.com/register (no card required for test mode).
2. In the dashboard, make sure **Test mode** is toggled on (top-right).
3. Go to **Developers → API keys**: copy the **Publishable key** (`pk_test_…`) and reveal/copy the **Secret key** (`sk_test_…`).
4. Set them as `STRIPE_PUBLISHABLE_KEY` / `STRIPE_SECRET_KEY` in the deploy environment.

### Webhook endpoint to register

1. In the Stripe dashboard go to **Developers → Webhooks → Add endpoint**.
2. URL: `https://<your-domain>/stripe/webhook` (must be the public HTTPS URL of the deployed site; Stripe cannot reach `localhost` — use the Stripe CLI's `stripe listen --forward-to localhost:5000/stripe/webhook` for local testing).
3. Select event: `checkout.session.completed`.
4. Copy the endpoint's **Signing secret** (`whsec_…`) into `STRIPE_WEBHOOK_SECRET`.

The handler verifies the signature on every event, marks the order `paid`
exactly once (duplicate deliveries are no-ops, deduped by Stripe session
id), decrements inventory once per order, and fires the confirmation +
owner-notification emails.

### Admin

- Login: `/admin/login` (password = `ADMIN_PASSWORD`). Dashboard at `/admin`:
  order list with status actions (mark paid / shipped / cancelled), sales
  summary (revenue, counts by status, revenue by day), and inventory with
  low-stock flags.
- Marking an order **shipped** sends the customer a shipped email including
  the tracking number you enter.
- Inventory starts at placeholder defaults (25 units, low-stock threshold 5
  — marked `PLACEHOLDER_` in `db.py`); edit real counts in the dashboard.

### Key handoff

The owner will supply the real Stripe keys (and the final orders email
address) via the secure vault later — nothing secret goes in this repo or
in chat. Until then, test mode (`sk_test_…`) is safe to exercise end to
end: no real money moves.

## Landing Pages & Promo Codes (Track 3)

Ad landing pages live at `/go/<slug>` — slim, distraction-free pages built
for paid-ad traffic: a product hero, headline/subhead, benefit bullets, an
optional YouTube embed, an "easy install" section, spec highlights, the
real customer-review system (with an honest "no reviews yet" empty state),
a sticky buy CTA, a countdown timer, and a promo code that auto-applies
when the visitor arrives.

### How to create a landing page

1. Log in at `/admin/login`, open **Admin → Landing Pages → + New Landing Page**.
2. Slug: lowercase letters/numbers/dashes — the page URL is `/go/<slug>`.
3. Pick the product (a dropdown of the real catalog), write the headline,
   subhead, and up to 8 benefit bullets (one per line).
4. Video: paste any YouTube URL (`watch?v=…`, `youtu.be/…`, `/embed/…`,
   `/shorts/…` — all normalized to an embed). Leave blank or write a
   `PLACEHOLDER …` note to show a clearly-labeled placeholder box.
5. Testimonial: only paste **real** customer quotes. Leave the
   `PLACEHOLDER …` note until you have one — we never invent testimonials.
6. Countdown: **evergreen** (N minutes per visitor, 5–120) or **fixed**
   (an end date/time in UTC; past ends are rejected).
7. Default promo code: picked from your existing codes, auto-applied on
   visit. A `?code=OTHER` URL parameter overrides it when the code is
   valid (e.g. `/go/premium-led?code=WELCOME10`).
8. Publish (unpublished pages return 404 — handy for staging).

The seeded example is `/go/premium-led` for the Premium CSP Series LED
Bulbs (code `PREMIUM10`, 10% off, 30-minute evergreen timer). Video and
testimonial are labeled placeholders for you to replace. Seeds are
insert-if-missing: re-running never overwrites your edits.

### Promo codes

**Admin → Promo Codes.** Percent-off (1–100%) or fixed amount-off
($0.01–$5,000, always capped at the cart subtotal so a code can never make
the total negative). Optional expiry (UTC) and max-uses cap. Codes apply
to the whole cart: a form on `/cart`, auto-apply on landing visits, or
`?code=` overrides. The session stores the code; every cart/checkout read
re-validates it, so expired, deactivated, or maxed-out codes are dropped
automatically and never silently honored.

Orders snapshot the code + discount amount (`orders.promo_code`,
`orders.discount_cents`), and usage counts **once per paid order** — the
Stripe webhook and the manual mark-paid admin action both increment, and
both are guarded so duplicate events never double-count.

Stripe: when `STRIPE_SECRET_KEY` is set, the code is attached to the
Checkout Session as a real Stripe Coupon (created once per code id,
`promo_<code>`, percent or amount, one-time) so buyers see the discount on
Stripe's payment page. When keys are absent, the coupon step is skipped
silently and the discount still applies to local totals — never a crash.
One caveat: coupon parameters are fixed at first creation, so if you
change a code's value later, **rename the code** instead of reusing it.

### Evergreen honesty note

The evergreen countdown is per-visitor by design: the timer starts when a
visitor first opens the page and is stored in *their* browser
(`localStorage`). When it expires it stays expired for that visitor — it
is deliberately not a resetting loop. The admin form says this outright,
and this README is the second place it's documented, because fake urgency
is the fastest way to lose a customer's trust.

### Promo strategy tips

- One evergreen code per ad campaign (e.g. `TIKTOK10`, `GOOGLE15`) so you
  can compare channels by `used_count` in the admin list.
- Keep the discount modest (10–15%) on the Premium tier — it's already the
  "Most Popular" badge holder; save deeper codes for slower tiers or
  bundles.
- Use fixed countdowns for real events (holiday sales, inventory
  clearances) and evergreen for always-on ad traffic.
- Set a `max_uses` cap on any code you post publicly — it bounds your
  exposure if the code gets shared.
- Retire codes by deactivating, not deleting: history in `used_count`
  stays accurate, and old landing pages just stop auto-applying.

### Files

- `landing.py` — the whole feature: Blueprint (`/go/<slug>`,
  `/cart/promo`, `/admin/landing*`, `/admin/promos*`), `promo_codes` +
  `landing_pages` schema, validation, discount math, seed content.
- `templates/go_landing.html` — the public page (standalone, slim header).
- `templates/go_admin_*.html`, `templates/go_promo_*.html` — admin CRUD.
- `test_landing.py` — 100 tests (pages, countdown validation, promo math
  edge cases, URL override, cart/checkout totals, Stripe coupon mapping
  with a faked Stripe module, usage counting, admin CRUD behind auth,
  copy guards). Run: `.venv/bin/python test_landing.py`.
