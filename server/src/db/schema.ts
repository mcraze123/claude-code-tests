/** Schema is inlined so the build needs no asset-copy step. */
export const SCHEMA_SQL = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profiles (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  section        TEXT    NOT NULL,
  name           TEXT    NOT NULL,
  enabled        INTEGER NOT NULL DEFAULT 1,
  keywords       TEXT    NOT NULL DEFAULT '[]',
  must_include   TEXT    NOT NULL DEFAULT '[]',
  exclude        TEXT    NOT NULL DEFAULT '[]',
  min_price      REAL,
  max_price      REAL,
  radius_mi      REAL    NOT NULL DEFAULT 30,
  min_profit     REAL    NOT NULL DEFAULT 60,
  min_roi        REAL    NOT NULL DEFAULT 0.5,
  include_broken INTEGER NOT NULL DEFAULT 1,
  min_confidence REAL    NOT NULL DEFAULT 0.35,
  notify         INTEGER NOT NULL DEFAULT 1,
  sources        TEXT,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
  key            TEXT    PRIMARY KEY,
  source_id      TEXT    NOT NULL,
  external_id    TEXT    NOT NULL,
  section        TEXT    NOT NULL,
  profile_id     INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
  url            TEXT    NOT NULL,
  title          TEXT    NOT NULL,
  description    TEXT,
  ask_price      REAL,
  prev_ask_price REAL,
  image_url      TEXT,
  posted_at      INTEGER NOT NULL,
  lat            REAL,
  lon            REAL,
  location_name  TEXT,
  attributes     TEXT    NOT NULL DEFAULT '{}',
  first_seen_at  INTEGER NOT NULL,
  last_seen_at   INTEGER NOT NULL,
  analysis       TEXT,
  score          REAL    NOT NULL DEFAULT 0,
  net_profit     REAL    NOT NULL DEFAULT 0,
  passed         INTEGER NOT NULL DEFAULT 0,
  reject_reason  TEXT,
  notified_at    INTEGER,
  hidden         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_listings_feed    ON listings(section, passed, hidden, score DESC);
CREATE INDEX IF NOT EXISTS idx_listings_posted  ON listings(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_profile ON listings(profile_id);

CREATE TABLE IF NOT EXISTS settings (
  id                    INTEGER PRIMARY KEY CHECK (id = 1),
  lat                   REAL,
  lon                   REAL,
  location_updated_at   INTEGER,
  mileage_cost_per_mi   REAL    NOT NULL DEFAULT 0.35,
  ebay_ad_rate_pct      REAL    NOT NULL DEFAULT 0,
  quiet_hours_start     INTEGER,
  quiet_hours_end       INTEGER
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint    TEXT PRIMARY KEY,
  keys_json   TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  last_ok_at  INTEGER,
  failures    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS poll_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  listings_seen INTEGER NOT NULL DEFAULT 0,
  new_listings  INTEGER NOT NULL DEFAULT 0,
  deals_found   INTEGER NOT NULL DEFAULT 0,
  notified      INTEGER NOT NULL DEFAULT 0,
  errors        TEXT NOT NULL DEFAULT '[]'
);
`;
