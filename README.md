# eBay Deal Finder

Self-hosted app that watches eBay for "for parts / repair" listings on stuff worth
fixing and reselling, compares each one to a reference "working" price, and flags
anything priced well below that. Runs on a schedule, keeps a web dashboard, and can
email you a daily digest.

## What it does

- Runs a set of named searches against eBay's Browse API (both Buy It Now and
  Auction listings) on a schedule (default every 90 minutes, configurable).
- For each search you maintain "comp entries" - reference prices for specific
  models (e.g. "Alienware x17 R2") - built from price samples you enter yourself.
- Items are matched to a comp entry by keyword, then flagged as a deal if the
  current price is at or below a threshold (default 50%) of the comp average
  *or* median.
- The dashboard shows two views - **Buy It Now Deals** and **Ending Soon Auctions**
  (auctions ending within a configurable lookahead window, default 24h) - each
  broken into one table per search, with price, ratio to comp avg/median, bid
  count, watcher count, condition, and how long it's been listed.
- A daily email digest (time configurable) summarizes both lists.
- Ended/sold/removed listings are pruned from the DB automatically after a
  configurable retention window.
- Everything - searches, query text, comp prices, schedule, email settings - is
  editable from the web UI. Nothing about the ~10 starter searches is
  hardcoded; they're just ordinary rows you can edit or delete.

## Important: how "average/median sold price" actually works here

eBay's official API for sold/completed listings (the Marketplace Insights API) is a
**Limited Release** that requires business approval from eBay - it is not available
on a normal developer account. The free Browse API only returns *active* listings.

So comp prices are **manually maintained**: you enter reference prices yourself
(ideally real sold prices you look up on eBay), and the app computes a trimmed
average (drops the single highest and lowest sample once you have 5+) and median
from them. There's a "Pre-fill" button on each comp entry that fetches current
*asking* prices from live listings as a rough starting point - these are clearly
labeled as asking prices, not sold prices, and are only used if you haven't entered
any real sold samples yet.

This is implemented behind an abstraction (`app/comps.py`: `CompPriceProvider`) so
that if you're ever approved for Marketplace Insights API access, you can implement
`MarketplaceInsightsCompProvider` and flip `COMP_PROVIDER=marketplace_insights` in
`.env` without touching anything else - the matching/scoring/dashboard/email code
doesn't care where the numbers come from.

## Also worth knowing: eBay search syntax support

The Browse API's `q` parameter supports quoted exact phrases and `-exclude`, the
same as eBay's search box, but **not** parenthetical `(a,b)` OR-grouping. To get
the same effect, a search can have multiple "query variants" - each one is run as
its own API call and the results are merged into the same table. Use this instead
of trying to cram OR logic into a single query string.

## Setup

1. **Get an eBay Developer key** (you said you already have one): a Client ID /
   Client Secret application keyset from https://developer.ebay.com. The Browse
   API is part of the free tier.
2. Copy `.env.example` to `.env` and fill in:
   - `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`
   - SMTP settings for the daily email (e.g. Gmail SMTP + an App Password)
3. On your Docker host:
   ```
   mkdir -p data
   docker compose up -d --build
   ```
   The SQLite database lives at `./data/ebay_deals.db` on the host (bind-mounted),
   so it survives container rebuilds/restarts.
4. Open `http://<host>:8000/`. The dashboard will be empty until the first search
   run completes (runs immediately on startup, then every 90 minutes by default).
5. Go to **Searches** and add comp entries (with real sold-price samples) for the
   specific models you want deal-scoring on - items without a matching comp entry
   still show up in the raw item list but won't be scored as a deal.
6. Go to **Notifications & Schedule** to set your email address, digest time, and
   search cadence.

## Project layout

```
app/
  ebay_client.py       eBay OAuth + Browse API wrapper
  comps.py             comp price abstraction (manual vs. future Marketplace Insights) + stats math
  scoring.py           item-to-comp matching, deal threshold logic
  ingest.py            runs searches, upserts items, tracks disappearance for pruning
  scheduler.py         APScheduler jobs (search run, ending-soon refresh, daily email, pruning)
  email_service.py     SMTP send + HTML digest template
  pruning.py           hard-deletes long-ended items
  models.py            SQLAlchemy models
  routers/             FastAPI routes (dashboard, searches+comps settings, notifications settings)
  templates/            server-rendered Jinja2 pages
  seed/default_searches.py   starter searches (fully editable afterward)
tests/                 unit tests for the comp math and scoring logic (no eBay creds needed)
```

## Running tests

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest tests/ -q
```

## Notes / limitations

- `itemCreationDate` (used for "time listed") isn't populated by eBay on every
  listing - when missing, the UI just shows "unknown" rather than guessing.
- The starter searches use fairly broad keyword variants; tune them (and the
  search interval) to manage your daily eBay API call volume if you hit rate
  limits - each variant × 2 buying-option types is one API call per search run.
- Comp entries start empty by design - seeding fabricated price data would be
  worse than no data. Add real numbers for the specific models you're tracking.
