# Bravo LEDs — Competitor Inspiration (Track 1)

Research date: 2026-09-24. Sources: https://ledlightstreet.com/ (LED Light Street, Shopify) and https://www.silverholder.com/product/silverholder-led-headlight/ (SilverHolder™ LED product page — the exact same LED model as Bravo LEDs' Premium LED Bulb, differently branded). Inspiration only; no copy lifted verbatim. Every item is checked against Bravo LEDs' standing rules: zero "headlight" in user-facing copy, "off-road" never in product/category/nav names, 1-year warranty on everything, no fake reviews, no invented sale prices.

## Ranked list

### 1. Spec badges on product cards & PDP hero — "12,000 Lumens · 6000K · 15-Min Install" (S/M)
- **What:** LED Light Street puts 3–4 short spec badges (lumens, color temp, install time, warranty) right under the price on the product page, and SilverHolder leads its page with benefit headlines ("300% brighter", "10-minute install", "50,000-hour lifespan").
- **Why:** Shoppers compare bulbs on lumens/kelvin first; badges let them compare at a glance without scrolling to a table. Matches Bravo LEDs' tiered catalog (Basic/Plus/Premium/Platinum) where the differences are exactly these specs.
- **Effort:** S if spec fields exist in catalog data (render on cards), M to add a proper spec table to the product page.
- **Status:** Not implemented in this track (product.html is out of scope for Track 1). Recommended for the product-page track.

### 2. Trust trio above the fold — warranty / shipping / returns (S) ✅ IMPLEMENTED
- **What:** Both competitors repeat a short trust line everywhere: SilverHolder shows "⚡ Free Shipping · ⚡ Lifetime Warranty · ⚡ 30 Days Money-Back Guarantee" above the price; LED Light Street shows "Free Ship $49+ · Same day · Secure Checkout SSL · Easy Returns".
- **Why:** Removes the three biggest purchase anxieties (what if it breaks / when does it arrive / can I return it) before the shopper ever scrolls.
- **Effort:** S.
- **Status:** Implemented on the homepage as a slim trust strip under the hero (1-year warranty on every product · ships in 1–2 business days · off-road & fog use only notice linking to /dot-compliance).

### 3. Reviews section with aggregate rating + honest empty state (M) ✅ IMPLEMENTED (empty state)
- **What:** LED Light Street's homepage leads with "5220 5-Star reviews" and a scrolling feed of recent reviews with product photos, names, and dates; each product page shows "★★★★★ 4.67 — 5,194 reviews".
- **Why:** Social proof is the highest-leverage trust element on both competitor sites. Bravo LEDs already has a real review system (submit on product page → admin approval); surfacing it on the homepage closes the loop.
- **Effort:** M (template + a small route change to feed recent approved reviews).
- **Status:** Homepage section implemented with an honest empty state ("No reviews yet — be the first") that renders real reviews automatically once the route passes them in (`homepage_reviews`). Needs a one-line app.py addition (out of scope for this track — see "Not implemented").

### 4. Structured spec table on the product page (M)
- **What:** SilverHolder uses tabs (Specification / Installation / Return) with a clean spec list: Voltage DC9–32V, Power 60W/set (30W/bulb), Lumen up to 10,000 lm/set, Color Temp 6500K, Lifespan 50,000 hrs, Waterproof IP68, Operating temp −45°C…+150°C.
- **Why:** Standardized specs build authority and answer pre-sales questions that otherwise become support tickets. Bravo LEDs can standardize most values per tier (the model is identical across sizes).
- **Effort:** M. Recommended for the product-page track. Use real values from the actual product — never invent.

### 5. "What's in the box" kit-contents block (S)
- **What:** SilverHolder lists kit contents (2× LED bulbs, 2× waterproof drivers, aluminum design w/ micro-fan, free shipping, warranty). LED Light Street describes the conversion kit contents (ballast + 2 bulbs + optional harnesses).
- **Why:** Makes a $70–$100 kit feel like more for the money and sets install expectations.
- **Effort:** S — static per-tier copy in product descriptions. Keep "off-road" out of product names; descriptions are fine.

### 6. Benefit-led description blocks (S)
- **What:** SilverHolder structures its page as benefit sections: "300% BRIGHTER THAN HALOGEN / Super focused beam pattern", "10 MINUTES EASY INSTALLATION / Plug and play", "OVER 50,000 HOURS LIFESPAN / aviation aluminum, hollow heat sink, 12,000 RPM fan".
- **Why:** Feature → benefit translation sells upgrades (Plus → Premium → Platinum). Bravo LEDs' tier story (Basic $25 … Platinum $100) needs exactly this treatment per tier.
- **Effort:** S — copywriting in descriptions. Same compliance note as #5.

### 7. Fitment finder embedded on the product page + "Verified Fitment" badge (M)
- **What:** LED Light Street puts "Select Your Vehicle: Year → Make → Model" at the top of every product page and badges results "✓ Verified Fitment".
- **Why:** Bravo LEDs' killer feature is the year/make/model finder, but today it lives only on the homepage. Putting it on product pages catches deep-link traffic (SEO, ads) and kills the #1 fitment doubt.
- **Effort:** M (reuse the existing finder JS/component).

### 8. Free-shipping threshold messaging (S)
- **What:** LED Light Street: "Free Shipping over $75+"; SilverHolder: "Free shipping to the U.S. and Canada".
- **Why:** A threshold nudges average order value up; the banner itself is free.
- **Effort:** S — but only once a real shipping policy/threshold is set. Do not invent one. Bravo LEDs currently ships USPS/UPS in 1–2 business days.

### 9. Comparison / explainer content: LED vs HID, tier guide (M)
- **What:** LED Light Street's homepage carries long-form SEO content ("LED vs HID: energy, heat, lifespan 50,000h vs 5,000h, install 30 vs 60 min") plus how-to-fit guides.
- **Why:** Bravo LEDs already has install guides; a "which tier is right for you" guide and an LED-vs-HID explainer would capture the same educational search traffic and pre-sell the Premium/Platinum upsell.
- **Effort:** M — one or two guide pages in the existing guides system.

### 10. Mid-page repeated CTA ("Get Yours Now") (S)
- **What:** SilverHolder drops a "⚡ Get Yours Now" CTA between its feature sections, not just at the top.
- **Why:** On long product pages many shoppers never scroll back up; a sticky or mid-page CTA recovers them.
- **Effort:** S — for the product-page track (e.g., sticky add-to-cart bar).

## Observed but NOT recommended

- **Countdown timers + "Last 23 in stock!"** (LED Light Street): converts, but fake urgency would violate the no-invented-claims rule. Only if wired to real inventory counts (Phase 2 has inventory tracking — a future honest version is possible).
- **Selling warranty upgrades** ("Can't beat that $5 for 1 year warranty"): LED Light Street monetizes warranty; Bravo LEDs includes 1-year free on everything — that's the competitive advantage, call it out instead.
- **"Lifetime Warranty" claims** (both sites): Bravo LEDs' policy is 1 year; never inflate it.
- **Review volume claims** ("5220 5-Star reviews", "3,500 Reviews"): only ever show real counts from the real review system.

## Compliance notes for all of the above
- Never write "headlight" in new user-facing copy (use "Low Beam" / "High Beam" / "fog" position names). The only existing exception is the FMVSS 108 explainer on /dot-compliance.
- "off-road" may appear in descriptions, disclaimers, the footer, and /dot-compliance — never in product names, category names, or nav labels.
- The off-road-use / not-DOT-approved notice belongs on the homepage trust strip (done), the footer (exists), and /dot-compliance (exists).
