# Sources

`ENABLED_SOURCES` in `.env` decides which marketplaces get swept:

```
ENABLED_SOURCES=craigslist,sample
```

Every adapter implements the same tiny interface
(`server/src/sources/types.ts`) and reports its own health, so the Settings →
Sources panel tells you exactly which ones are on, which need setup, and why.

---

## <a id="craigslist"></a>Craigslist — works out of the box

Craigslist search pages publish an RSS feed at `?format=rss`. That is a
documented, unauthenticated interface, and it is the only thing this adapter
touches. No scraping, no login, no headless browser.

```
CRAIGSLIST_SITES=sfbay,sacramento
```

Use the subdomain from the URL of your local site — `sfbay` from
`https://sfbay.craigslist.org`. Multiple regions are swept in sequence.

Categories per section:

| Section | Craigslist categories |
|---|---|
| Electronics | `ela` electronics, `sya` computers, `vga` video gaming |
| Appliances | `app` appliances |
| Cars | `cto` cars & trucks **by owner** (dealers rarely leave margin) |

Edit `CATEGORIES` in `server/src/sources/craigslist.ts` to change them.

**Being a good citizen.** Requests are serialised through a queue with a 2.5s
minimum gap, one region and category at a time, with a jittered sweep
interval. Do not lower `POLL_INTERVAL_SECONDS` below about 90 seconds — you
will get rate-limited, and a banned IP finds no deals at all.

**If it stops working.** Craigslist has removed RSS from parts of the site
before. The adapter detects an HTML response and reports it as a real error
rather than parsing it to zero results, so check Settings → Poller: you will
see the category and the URL to try in a browser.

---

## <a id="facebook-marketplace"></a>Facebook Marketplace — read this

**Facebook Marketplace has no public API, and its terms prohibit automated
collection.** There is no version of this app that legitimately scrapes it, so
it does not try.

What is here instead is a thin, dormant HTTP client. Point it at a source you
have decided you are entitled to use, and it will normalise whatever that
source returns into the same pipeline as everything else:

```
FB_PROVIDER_URL=https://your-provider.example/search
FB_PROVIDER_KEY=your-key
```

Your options, roughly in order of how defensible they are:

1. **Leave it off.** Craigslist plus OfferUp still covers a lot of ground, and
   this is the only option with no legal question attached.
2. **A commercial data provider** that licenses marketplace data and takes on
   the compliance question itself. You are buying their terms as much as their
   data — read them.
3. **Something you run yourself.** Your call, your risk, your account. Be
   aware that Meta bans accounts for this, and that a banned personal account
   is a real cost.

The adapter is deliberately agnostic so this decision stays yours and stays
outside the codebase.

### The contract

`GET {FB_PROVIDER_URL}?q=&section=&limit=&radius_mi=&lat=&lon=&min_price=&max_price=`
with `Authorization: Bearer {FB_PROVIDER_KEY}` if a key is set.

Respond with a JSON array, or `{"listings": [...]}`:

```json
[
  {
    "id": "1234567890",
    "url": "https://www.facebook.com/marketplace/item/1234567890",
    "title": "RTX 3080 - no display, for parts",
    "description": "Powers on, fans spin, nothing on screen.",
    "price": 120,
    "image": "https://.../photo.jpg",
    "posted_at": "2026-08-26T14:03:00Z",
    "lat": 37.5485,
    "lon": -121.9886,
    "location": "Fremont, CA"
  }
]
```

Only `url` and `title` are strictly required; an `id` is derived from the URL
if you omit it. `posted_at` accepts RFC 3339, epoch seconds or epoch millis,
and defaults to now. `price` may be a number or a string like `"$120"`.
Everything else degrades gracefully — a listing with no coordinates simply has
no distance and no fuel cost, and a listing with no price is surfaced with a
❓ badge.

Responses are normalised defensively: a third-party feed is not trusted to be
well-formed, and malformed entries are dropped rather than crashing a sweep.

---

## <a id="offerup"></a>OfferUp

Same situation and the same adapter:

```
OFFERUP_PROVIDER_URL=
OFFERUP_PROVIDER_KEY=
```

---

## <a id="sample"></a>Sample — offline fixtures

Fourteen realistic listings across all three sections, each carrying its own
comp set so no network call is made for them. They are positioned at fixed
distances and bearings from your current location, and are always "just
posted" relative to now, so freshness and distance behave the way they will in
production.

Keep it enabled while you are tuning thresholds; drop it from
`ENABLED_SOURCES` once you are running for real. Sample listings link to
`example.invalid` and are obvious in the feed.

Edit `server/data/sample-listings.json` to add your own cases — it is also the
quickest way to test a new repair rule end to end.

---

## Adding a source

Implement `SourceAdapter` and register it in `server/src/sources/index.ts`:

```ts
export const mysite: SourceAdapter = {
  id: 'mysite',
  label: 'My Site',
  docsAnchor: 'mysite',
  isAvailable: () => Boolean(process.env.MYSITE_KEY),
  unavailableReason() {
    return this.isAvailable() ? null : 'MYSITE_KEY is not set';
  },
  async search(req) {
    /* return RawListing[] */
  },
};
```

Everything downstream — valuation, repair estimation, scoring, dedupe,
price-drop detection, notifications — is source-agnostic and comes for free.

Two rules for a well-behaved adapter:

- Route requests through a `PoliteQueue` (`server/src/util/http.ts`) so you
  never fan out in parallel against one host.
- Throw a descriptive error rather than returning `[]` when something is
  wrong. An empty array means "nothing for sale"; an error means "something is
  broken", and only one of those should show up in Settings → Poller.
