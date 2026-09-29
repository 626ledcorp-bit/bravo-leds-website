# Bravo LEDs — Ecommerce Feature Gap Audit
Date: 2026-09-28. Audit of ~/workspace/626leds/website (Flask app) against
standard ecommerce features (Shopify-class stores, LASFIT, Diode Dynamics).
"Covered" means verified in code; build sizes are rough (small = hours,
medium = 1-3 days, large = 1+ week).

## ALREADY COVERED

### Frontend (customer-facing)
- Site search (/search): token-AND over name, SKU, category, tags, blurb,
  bulb sizes; header search toggle; empty/no-result states.
- Mega-menu navigation: By Vehicle / By Bulb Size (16 sizes) / LED Bulbs
  image tiles / Track Order / Contact button; mobile drawer; hamburger.
- Admin-editable navigation (/admin/navigation): labels, links, tile images,
  show/hide, reorder; renders from nav_items table.
- Shop pages (/shop, /shop/<category>): category grids, sale badges,
  compare pricing (sale_price_cents).
- Product pages: image gallery w/ thumbnails + enlarge, variant option groups,
  customer reviews (submit + display + admin moderation), related products,
  bulb-size fitment info, off-road/DOT disclaimer, 1-year warranty line.
- Fitment finder: Year/Make/Model/Trim API + /fitment page + homepage widget;
  saved-vehicle pill in header (session).
- 21 vehicle-specific interior kit pages (/interior-kit/Y/M/M) + /interior-kits
  listing + fitment-result kit banner.
- Cart: add/update/remove, promo-code entry (/cart/promo), free-shipping
  threshold note.
- Checkout: Stripe hosted Checkout (guest, no account needed), coupon codes
  incl. spin-wheel prizes, webhook marks orders paid, success/cancel pages.
- Order tracking page (/track-order): order number + email lookup.
- Email capture: spin-to-win popup (server-side weighted odds, real coupon
  prizes, stats) + footer newsletter signup (/newsletter).
- Content pages: FAQ, guides, shipping, returns, privacy, terms, DOT
  compliance, contact form (emails owner).
- Trust elements: trust strip (warranty/shipping/off-road), reviews section,
  meta description, 404 page, mobile responsive.

### Backend (admin/operations)
- Dashboard: order stats (revenue by status), product search, inventory list,
  low-stock threshold alerts (owner email).
- Products: CRUD, draft/publish/archive, CSV import/export w/ template and
  confirm step, image uploads, variant groups, Square import bridge.
- Inventory: per-product stock mgmt + two-way Square POS sync (webhooks).
- Orders: list/detail, status changes, notes, Stripe refunds, Pirate Ship CSV
  export, packing slips, pick lists, bulk tracking-number import.
- Promotions: promo_codes CRUD (percent/amount, min order, max uses, expiry),
  landing pages (/go/<slug>) CRUD, spin-promo CRUD + stats
  (impressions/spins/emails/redeemed).
- Reviews moderation (approve/delete), newsletter CSV export.
- Settings page, TOTP 2FA + backup codes, login throttle.
- Notification preference toggles (order/shipment/low-stock emails).

## MISSING — HIGH PRIORITY

1. Shop filters & sorting — no way to filter by price/series/bulb size or sort
   by price on shop/search pages. Shoppers can't narrow the catalog; every
   major store has this. (medium)
2. Abandoned cart recovery — ~70% of carts are abandoned industry-wide; no
   email capture at cart or reminder flow. Blocked on email provider (see 12).
   (medium-large)
3. Stock indicators on product pages — no "In stock" / "Only X left" /
   low-stock urgency; inventory data exists but isn't shown. (small)
4. Breadcrumbs (Home / Shop / Category / Product) — helps navigation and SEO.
   (small)
5. sitemap.xml + robots.txt — no sitemap; Google discovery of 21 kit pages +
   products depends on it. (small)
6. Open Graph / Twitter card tags — no link previews when products are shared
   on social/messaging. (small)
7. Analytics (GA4 and/or Meta Pixel) — no traffic/conversion measurement; the
   planned A/B tests can't be measured without it. (small)
8. Email service provider (SMTP/Klaviyo) — outstanding: unlocks order
   confirmations, shipping notifications, abandoned cart, promo delivery.
   (medium)
9. CA sales tax at checkout — outstanding compliance item. (small-medium)
10. Shipping estimator on cart page — cart says "Calculated at checkout",
    which causes hesitation; ZIP-based estimate is standard. (small-medium)
11. Customer accounts (optional; guest checkout stays) — order history,
    faster repeat checkout. (medium-large)
12. Product Q&A — fitment questions are the #1 pre-sales friction for LED
    bulbs; even a simple "ask a question" form helps. (small-medium)

## MISSING — MEDIUM

- Wishlist / save-for-later. (small-medium)
- Recently viewed products. (small)
- Announcement bar (admin-editable promo banner above header). (small)
- CMS editing for FAQ/guides/homepage sections (currently hardcoded). (medium)
- Customer management in admin (list, per-customer order history). (small-medium)
- Returns/RMA portal (returns page is info-only; no return requests). (medium)
- Back-in-stock alerts for shoppers (only owner gets low-stock email). (small-medium)
- Cart cross-sell / "frequently bought together". (medium)
- Bulk quick-edit grid for products (price/stock). (medium)
- Per-product SEO fields (meta title/description) + image alt text. (small)
- Redirect manager for renamed/moved URLs. (small)
- Photo reviews / verified-purchase badge (reviews exist, basic). (small-medium)

## MISSING — LOW / LATER

- Live chat (small to embed; real cost is staffing). 
- Loyalty / rewards program. (large)
- Multi-currency / international shipping. (large; US-focused for now)
- Product comparison tool. (medium)
- Homepage hero/banner CMS (currently static). (small-medium)
- Advanced A/B testing framework. (medium-large)
- Marketplace integrations (Amazon/eBay sync). (large)
- 360° views / product videos. (small-medium)
- Gift cards. (medium; low demand signal)

## KNOWN TECH DEBT (not features, but flagged)
- No CSRF tokens on forms; no custom security headers; no rate limiting on
  public endpoints (spin API, search). Fix before real traffic.
