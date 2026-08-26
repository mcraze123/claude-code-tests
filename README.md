# Scavenger

Watches local classifieds for things worth buying, fixing and reselling — and
pushes them to your phone within a minute or two of the seller hitting post.

Three sections, each with its own economics: **Electronics**, **Appliances**,
**Cars**. Broken is a feature, not a filter: the whole point is finding the
$120 graphics card with no display that resells for $340 once you replace a
MOSFET.

For every listing it answers the four questions that decide whether you get in
the car:

| | |
|---|---|
| **What's it worth?** | Median of comparable eBay listings, with a confidence score |
| **What are they asking?** | Straight from the ad |
| **What will the repair cost?** | Parts only — labour is yours — with its own confidence score |
| **How far away is it?** | Miles from your phone's current location, priced as fuel |

…and then subtracts marketplace fees, shipping, packaging, parts and fuel to
show you a single number: **what you actually clear.**

---

## Quick start (no accounts needed)

```bash
npm install
cp .env.example .env
npm run build
npm start
```

Open <http://localhost:8080>. It ships with a `sample` source — fourteen
realistic fixture listings across all three sections, with their own bundled
comps — so you can see scoring, badges, distances and the full profit
breakdown working before you have a single API key.

Set `HOME_LAT` / `HOME_LON` in `.env` (or tap **Use my current location** in
the app) and the fixtures arrange themselves at real distances around you.

### Dev mode

```bash
npm run dev     # API on :8080, Vite with hot reload on :5173
npm test        # 122 tests over the pricing, repair and parsing logic
```

---

## Making it real

### 1. Resale comps — eBay

Without this, nothing but the sample fixtures can be valued.

1. Create an app at <https://developer.ebay.com> and generate a **production**
   keyset.
2. Put the App ID and Cert ID in `.env` as `EBAY_CLIENT_ID` /
   `EBAY_CLIENT_SECRET`.

That gets you the **Browse API**, which returns *active* listings — asking
prices, not sale prices. Scavenger discounts them by `ACTIVE_TO_SOLD_FACTOR`
(0.82 by default, in `server/src/valuation/comps.ts`) and marks the confidence
down accordingly.

If you apply for and are granted **Marketplace Insights**, set
`EBAY_INSIGHTS_ENABLED=true`. You then get real sold prices, sold-per-week
velocity (which drives the 🔥 Hot badge), and noticeably higher confidence
numbers. It is worth applying for.

### 2. Notifications

Either channel works; both together is fine.

**ntfy** — the zero-setup option, and the one to start with:

1. Install the [ntfy](https://ntfy.sh) app.
2. Subscribe to a long random topic name (it is a public namespace — treat the
   topic like a password).
3. Put it in `.env` as `NTFY_TOPIC`.

**Web push** — notifications from the app itself, no third party:

```bash
npm run gen:vapid --workspace=server   # paste the output into .env
```

Then open the app on your phone and tap **Enable alerts on this device**.

> On iPhone, web push only works after the site is installed to the home
> screen. Tap Share → Add to Home Screen, open it from the icon, *then* enable
> alerts. The app detects this and tells you.

### 3. Marketplaces

`ENABLED_SOURCES` controls which are swept. **Craigslist works out of the
box**; Facebook Marketplace and OfferUp need a decision from you about where
their data comes from. See **[docs/SOURCES.md](docs/SOURCES.md)** — that file
is worth reading before you enable anything.

---

## How the money is worked out

```
        comp median          what similar units actually sell for
      − condition haircuts   branded title, no charger, smoke smell…
      × parts-out ratio      only if it cannot be economically repaired
      × local-sale haircut   for things too big to ship
      ─────────────────────
      = expected sale price
      − marketplace fees     13.25% + $0.40, tiered; $0 for local cash sales
      − shipping             from an estimated weight
      − packaging
      − repair parts         from the symptom in the ad
      − purchase price
      − fuel                 round trip, 1.25× crow-flies, at your $/mile
      ─────────────────────
      = net profit
```

**Your labour is deliberately not in there.** You do your own board-level
work; this tells you whether the *cash* works, not what your hour is worth.

### The two confidence numbers

Both are shown on every card, because they fail independently.

**Sell price confidence** blends three separate worries: too few comps, comps
that disagree with each other, and comps that are not really the same product
(matched on model tokens, so an RTX 3080 comp counts and a "gaming graphics
card" comp barely does). Sold data scores higher than active listings.

**Repair cost confidence** comes from how specifically the seller described
the fault. `"no heat"` on a dryer is 0.80 — it is an element and a thermal
fuse, every time. `"untested, found in a storage unit"` is 0.15, and the parts
figure is a class average rather than a diagnosis.

A profile's `minConfidence` filters on the blend of the two, weighted by how
much of the value the repair consumes.

### Parts-out vs. repair

"For parts or repair" in a listing title is *weak* evidence — it is mostly
what people write when they will not warrant something, and those are exactly
the listings you want. Scavenger only values something as a bag of parts when:

- a repair rule says it is unfixable (cracked TV panel, iCloud lock, dead
  output transformer), **or**
- the parts bill exceeds 60% of the resale value, **or**
- the seller said "for parts" *and* nothing could be diagnosed from the text.

### Badges

| | | |
|---|---|---|
| ⚡ | Just listed | posted in the last 20 minutes |
| 🔥 | Hot item | sells ≥5/week on eBay (or has a deep, tight comp set) |
| 💰 | High margin | ≥45% of the sale price is profit |
| 🎯 | Steal | asking ≤30% of resale, at ≥2× your profit floor |
| 🔧 | Needs repair | diagnosed, with the parts estimate |
| 🧩 | Parts only | not economically repairable |
| 📍 | Close by | within 5 miles |
| 🛣️ | Long drive | near the edge of your radius, with the fuel cost |
| 📉 | Price drop | the seller cut the price since we first saw it |
| ⚠️ | Low confidence | under 40% confidence overall |
| ❓ | No price | seller listed none; profit assumes free |
| 🪶 | Thin comps | fewer than 5 comparable sales |

---

## Tuning it

The knowledge lives in JSON, not in code. Edit and restart.

### `server/data/repair-rules.json`

51 rules across 18 device classes, keyed on the phrases sellers actually use.
Each carries a low/typical/high parts range, a confidence, and a note
explaining the diagnosis:

```json
{ "id": "tv-no-power", "deviceClass": "tv",
  "symptoms": ["no power", "clicking", "red light blinking"],
  "parts": { "low": 3, "typical": 22, "high": 60 }, "confidence": 0.7,
  "note": "Classic bulging caps in the PSU, or a failed backlight driver." }
```

Costs assume you buy components, not modules. Adjust them to what *you* pay —
if you have a parts stash, your numbers are lower than the defaults.

Matching is negation-aware: `"No artifacts before it died"` does not fire the
artifacting rule, and `"no display"` still fires the no-display rule.

### `server/data/economics.json`

Fee tiers, per-order fees, shipping weight classes, and the condition
haircuts. **Check the fee percentages against your own eBay invoices** — rates
change, and a store subscription changes them again.

### Search profiles

Everything else is editable from the app's **Searches** tab: terms, required
terms, excluded terms, price range, radius, minimum profit, minimum ROI,
minimum confidence, whether to include broken items, whether to notify, and
which sources to use. Four starter profiles are seeded on first run.

Broad terms with tight floors beat narrow terms with loose ones — the scoring
is what does the filtering.

---

## What this does not do

Worth knowing before you rely on it:

- **Facebook Marketplace has no public API.** Nothing here scrapes it. The
  adapter is a pluggable client for a source *you* supply and are licensed to
  use — see [docs/SOURCES.md](docs/SOURCES.md). It stays dormant until you
  configure one.
- **The Craigslist RSS feed is unverified against the live site.** The parser
  is tested against real-shaped feed XML, and the adapter reports clearly when
  Craigslist answers with HTML instead of a feed — but the network policy of
  the machine this was built on blocked craigslist.org, so the live round trip
  has not been exercised. Check the Settings → Poller panel after your first
  sweep.
- **Comps are not condition-matched.** A vehicle's comp set does not exclude
  branded titles; the haircut rules approximate that instead.
- **Active-listing comps are a proxy.** Until you have Marketplace Insights,
  every valuation rests on the 0.82 asking-to-sold factor.
- **Nothing verifies the seller's claim.** "Only needs a screen" is an
  unverified assertion from a stranger; the confidence numbers describe how
  well we parsed the ad, not whether it is true.

---

## Layout

```
server/
  src/
    sources/        marketplace adapters (craigslist, provider-backed, sample)
    valuation/      ebay comps · statistics · fees · repair rules · scoring
    notify/         web push · ntfy · message formatting
    db/             SQLite schema and data access
    analyzer.ts     one listing -> a scored deal
    poller.ts       the sweep loop
  data/             repair-rules.json · economics.json · sample-listings.json
web/
  src/              React PWA: deal feed, search editor, settings
  public/sw.js      service worker (push + notification click-through)
```

## API

| | |
|---|---|
| `GET /api/status` | poller, sources, channels, comp config |
| `GET /api/deals?section=&sort=&includeRejected=` | the feed; `sort` is score, new, profit or distance |
| `POST /api/deals/:key/hide` | dismiss one |
| `GET POST PATCH DELETE /api/profiles` | search profiles |
| `GET PATCH /api/settings` | fuel cost, ad rate, quiet hours |
| `POST /api/location` | `{lat, lon}` from the phone |
| `POST /api/sweep` | force a sweep now |
| `GET /api/push/key`, `POST /api/push/subscribe`, `POST /api/push/test` | notifications |

`includeRejected=true` shows everything seen along with the reason it did not
make the cut — the fastest way to find out why a search is quiet.
