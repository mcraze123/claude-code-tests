import type {
  AppSettings,
  RawListing,
  ScoredDeal,
  SearchProfile,
  Section,
} from '../types.js';
import { db } from './index.js';

const json = <T>(s: string | null, fallback: T): T => {
  if (!s) return fallback;
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
};

/* -------------------------------------------------------------------------- */
/* Profiles                                                                    */
/* -------------------------------------------------------------------------- */

interface ProfileRow {
  id: number;
  section: string;
  name: string;
  enabled: number;
  keywords: string;
  must_include: string;
  exclude: string;
  min_price: number | null;
  max_price: number | null;
  radius_mi: number;
  min_profit: number;
  min_roi: number;
  include_broken: number;
  min_confidence: number;
  notify: number;
  sources: string | null;
  created_at: number;
  updated_at: number;
}

function toProfile(r: ProfileRow): SearchProfile {
  return {
    id: r.id,
    section: r.section as Section,
    name: r.name,
    enabled: r.enabled === 1,
    keywords: json<string[]>(r.keywords, []),
    mustInclude: json<string[]>(r.must_include, []),
    exclude: json<string[]>(r.exclude, []),
    minPrice: r.min_price,
    maxPrice: r.max_price,
    radiusMi: r.radius_mi,
    minProfit: r.min_profit,
    minRoi: r.min_roi,
    includeBroken: r.include_broken === 1,
    minConfidence: r.min_confidence,
    notify: r.notify === 1,
    sources: r.sources ? json<string[]>(r.sources, []) : null,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

export function listProfiles(section?: Section): SearchProfile[] {
  const rows = section
    ? (db().prepare('SELECT * FROM profiles WHERE section = ? ORDER BY id').all(section) as ProfileRow[])
    : (db().prepare('SELECT * FROM profiles ORDER BY section, id').all() as ProfileRow[]);
  return rows.map(toProfile);
}

export function getProfile(id: number): SearchProfile | null {
  const row = db().prepare('SELECT * FROM profiles WHERE id = ?').get(id) as ProfileRow | undefined;
  return row ? toProfile(row) : null;
}

export type ProfileInput = Omit<SearchProfile, 'id' | 'createdAt' | 'updatedAt'>;

export function createProfile(input: ProfileInput): SearchProfile {
  const now = Date.now();
  const info = db()
    .prepare(
      `INSERT INTO profiles
        (section, name, enabled, keywords, must_include, exclude, min_price, max_price,
         radius_mi, min_profit, min_roi, include_broken, min_confidence, notify, sources,
         created_at, updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    )
    .run(
      input.section,
      input.name,
      input.enabled ? 1 : 0,
      JSON.stringify(input.keywords),
      JSON.stringify(input.mustInclude),
      JSON.stringify(input.exclude),
      input.minPrice,
      input.maxPrice,
      input.radiusMi,
      input.minProfit,
      input.minRoi,
      input.includeBroken ? 1 : 0,
      input.minConfidence,
      input.notify ? 1 : 0,
      input.sources ? JSON.stringify(input.sources) : null,
      now,
      now,
    );
  return getProfile(Number(info.lastInsertRowid))!;
}

export function updateProfile(id: number, patch: Partial<ProfileInput>): SearchProfile | null {
  const current = getProfile(id);
  if (!current) return null;
  const next = { ...current, ...patch };
  db()
    .prepare(
      `UPDATE profiles SET section=?, name=?, enabled=?, keywords=?, must_include=?, exclude=?,
         min_price=?, max_price=?, radius_mi=?, min_profit=?, min_roi=?, include_broken=?,
         min_confidence=?, notify=?, sources=?, updated_at=?
       WHERE id=?`,
    )
    .run(
      next.section,
      next.name,
      next.enabled ? 1 : 0,
      JSON.stringify(next.keywords),
      JSON.stringify(next.mustInclude),
      JSON.stringify(next.exclude),
      next.minPrice,
      next.maxPrice,
      next.radiusMi,
      next.minProfit,
      next.minRoi,
      next.includeBroken ? 1 : 0,
      next.minConfidence,
      next.notify ? 1 : 0,
      next.sources ? JSON.stringify(next.sources) : null,
      Date.now(),
      id,
    );
  return getProfile(id);
}

export function deleteProfile(id: number): boolean {
  return db().prepare('DELETE FROM profiles WHERE id = ?').run(id).changes > 0;
}

/* -------------------------------------------------------------------------- */
/* Settings                                                                    */
/* -------------------------------------------------------------------------- */

interface SettingsRow {
  lat: number | null;
  lon: number | null;
  location_updated_at: number | null;
  mileage_cost_per_mi: number;
  ebay_ad_rate_pct: number;
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
}

export function getSettings(): AppSettings {
  const r = db().prepare('SELECT * FROM settings WHERE id = 1').get() as SettingsRow;
  return {
    lat: r.lat,
    lon: r.lon,
    locationUpdatedAt: r.location_updated_at,
    mileageCostPerMi: r.mileage_cost_per_mi,
    ebayAdRatePct: r.ebay_ad_rate_pct,
    quietHoursStart: r.quiet_hours_start,
    quietHoursEnd: r.quiet_hours_end,
  };
}

export function updateSettings(patch: Partial<AppSettings>): AppSettings {
  const next = { ...getSettings(), ...patch };
  db()
    .prepare(
      `UPDATE settings SET lat=?, lon=?, location_updated_at=?, mileage_cost_per_mi=?,
         ebay_ad_rate_pct=?, quiet_hours_start=?, quiet_hours_end=? WHERE id=1`,
    )
    .run(
      next.lat,
      next.lon,
      next.locationUpdatedAt,
      next.mileageCostPerMi,
      next.ebayAdRatePct,
      next.quietHoursStart,
      next.quietHoursEnd,
    );
  return getSettings();
}

/* -------------------------------------------------------------------------- */
/* Listings                                                                    */
/* -------------------------------------------------------------------------- */

export interface UpsertResult {
  key: string;
  isNew: boolean;
  previousAskPrice: number | null;
  firstSeenAt: number;
  lastSeenAt: number;
  notifiedAt: number | null;
}

/**
 * Insert or refresh a listing. Reposts keep their original first-seen time so
 * a seller bumping an ad does not re-trigger "just listed", but a genuine
 * price change is recorded so the price-drop badge can fire.
 */
export function upsertListing(
  listing: RawListing,
  section: Section,
  profileId: number,
): UpsertResult {
  const key = `${listing.sourceId}:${listing.externalId}`;
  const now = Date.now();
  const existing = db()
    .prepare('SELECT ask_price, first_seen_at, notified_at FROM listings WHERE key = ?')
    .get(key) as
    | { ask_price: number | null; first_seen_at: number; notified_at: number | null }
    | undefined;

  if (!existing) {
    db()
      .prepare(
        `INSERT INTO listings
          (key, source_id, external_id, section, profile_id, url, title, description,
           ask_price, prev_ask_price, image_url, posted_at, lat, lon, location_name,
           attributes, first_seen_at, last_seen_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      )
      .run(
        key,
        listing.sourceId,
        listing.externalId,
        section,
        profileId,
        listing.url,
        listing.title,
        listing.description ?? null,
        listing.askPrice,
        null,
        listing.imageUrl ?? null,
        listing.postedAt,
        listing.lat ?? null,
        listing.lon ?? null,
        listing.locationName ?? null,
        JSON.stringify(listing.attributes ?? {}),
        now,
        now,
      );
    return {
      key,
      isNew: true,
      previousAskPrice: null,
      firstSeenAt: now,
      lastSeenAt: now,
      notifiedAt: null,
    };
  }

  const priceChanged =
    listing.askPrice !== null &&
    existing.ask_price !== null &&
    listing.askPrice !== existing.ask_price;

  db()
    .prepare(
      `UPDATE listings SET title=?, description=?, ask_price=?, prev_ask_price=?, image_url=?,
         lat=?, lon=?, location_name=?, attributes=?, last_seen_at=?, profile_id=?, section=?
       WHERE key=?`,
    )
    .run(
      listing.title,
      listing.description ?? null,
      listing.askPrice,
      priceChanged ? existing.ask_price : null,
      listing.imageUrl ?? null,
      listing.lat ?? null,
      listing.lon ?? null,
      listing.locationName ?? null,
      JSON.stringify(listing.attributes ?? {}),
      now,
      profileId,
      section,
      key,
    );

  return {
    key,
    isNew: false,
    previousAskPrice: priceChanged ? existing.ask_price : null,
    firstSeenAt: existing.first_seen_at,
    lastSeenAt: now,
    notifiedAt: existing.notified_at,
  };
}

export function saveAnalysis(
  key: string,
  deal: ScoredDeal,
  passed: boolean,
  rejectReason: string | null,
): void {
  const analysis = JSON.stringify({
    valuation: deal.valuation,
    repair: deal.repair,
    math: deal.math,
    badges: deal.badges,
    adjustments: deal.adjustments,
    distanceMi: deal.distanceMi,
  });
  db()
    .prepare(
      'UPDATE listings SET analysis=?, score=?, net_profit=?, passed=?, reject_reason=? WHERE key=?',
    )
    .run(analysis, deal.score, deal.math.netProfit, passed ? 1 : 0, rejectReason, key);
}

export function markNotified(key: string, at = Date.now()): void {
  db().prepare('UPDATE listings SET notified_at = ? WHERE key = ?').run(at, key);
}

export function hideListing(key: string, hidden = true): boolean {
  return (
    db().prepare('UPDATE listings SET hidden = ? WHERE key = ?').run(hidden ? 1 : 0, key)
      .changes > 0
  );
}

interface ListingRow {
  key: string;
  source_id: string;
  external_id: string;
  section: string;
  profile_id: number | null;
  url: string;
  title: string;
  description: string | null;
  ask_price: number | null;
  prev_ask_price: number | null;
  image_url: string | null;
  posted_at: number;
  lat: number | null;
  lon: number | null;
  location_name: string | null;
  attributes: string;
  first_seen_at: number;
  last_seen_at: number;
  analysis: string | null;
  score: number;
  net_profit: number;
  passed: number;
  reject_reason: string | null;
  notified_at: number | null;
  hidden: number;
}

function toDeal(r: ListingRow): ScoredDeal {
  const analysis = json<Partial<ScoredDeal>>(r.analysis, {});
  return {
    key: r.key,
    section: r.section as Section,
    profileId: r.profile_id ?? 0,
    listing: {
      sourceId: r.source_id,
      externalId: r.external_id,
      url: r.url,
      title: r.title,
      description: r.description,
      askPrice: r.ask_price,
      currency: 'USD',
      imageUrl: r.image_url,
      postedAt: r.posted_at,
      lat: r.lat,
      lon: r.lon,
      locationName: r.location_name,
      attributes: json<Record<string, string | number | boolean>>(r.attributes, {}),
    },
    valuation: analysis.valuation!,
    repair: analysis.repair!,
    math: analysis.math!,
    badges: analysis.badges ?? [],
    adjustments: analysis.adjustments ?? [],
    distanceMi: analysis.distanceMi ?? null,
    score: r.score,
    firstSeenAt: r.first_seen_at,
    lastSeenAt: r.last_seen_at,
    notifiedAt: r.notified_at,
  };
}

export interface FeedQuery {
  section: Section;
  limit?: number;
  /** Include listings that did not clear the profile floors. */
  includeRejected?: boolean;
  profileId?: number;
  /** 'score' ranks by opportunity, 'new' ranks by posting time. */
  sort?: 'score' | 'new' | 'profit' | 'distance';
  maxAgeHours?: number;
}

export function feed(q: FeedQuery): ScoredDeal[] {
  const clauses = ['section = ?', 'hidden = 0', 'analysis IS NOT NULL'];
  const params: (string | number)[] = [q.section];
  if (!q.includeRejected) clauses.push('passed = 1');
  if (q.profileId) {
    clauses.push('profile_id = ?');
    params.push(q.profileId);
  }
  if (q.maxAgeHours) {
    clauses.push('posted_at >= ?');
    params.push(Date.now() - q.maxAgeHours * 3_600_000);
  }

  const order =
    q.sort === 'new'
      ? 'posted_at DESC'
      : q.sort === 'profit'
        ? 'net_profit DESC'
        : q.sort === 'distance'
          ? 'score DESC' // distance lives in the analysis blob; re-sorted below
          : 'score DESC, posted_at DESC';

  const limit = q.limit ?? 100;
  // Distance is not a column, so sorting by it has to happen in JS. Widen the
  // fetch first, or "closest" would only ever sort the top page by score.
  const fetchLimit = q.sort === 'distance' ? Math.min(1000, limit * 5) : limit;

  const rows = db()
    .prepare(`SELECT * FROM listings WHERE ${clauses.join(' AND ')} ORDER BY ${order} LIMIT ?`)
    .all(...params, fetchLimit) as ListingRow[];

  const deals = rows.map(toDeal);
  if (q.sort === 'distance') {
    deals.sort((a, b) => (a.distanceMi ?? 1e9) - (b.distanceMi ?? 1e9));
    return deals.slice(0, limit);
  }
  return deals;
}

export function getDeal(key: string): ScoredDeal | null {
  const row = db().prepare('SELECT * FROM listings WHERE key = ?').get(key) as
    | ListingRow
    | undefined;
  return row?.analysis ? toDeal(row) : null;
}

/** Deals that cleared their profile and have never been pushed to the phone. */
export function unnotifiedDeals(limit = 20): ScoredDeal[] {
  const rows = db()
    .prepare(
      `SELECT * FROM listings
       WHERE passed = 1 AND hidden = 0 AND notified_at IS NULL AND analysis IS NOT NULL
       ORDER BY score DESC LIMIT ?`,
    )
    .all(limit) as ListingRow[];
  return rows.map(toDeal);
}

export function pruneOldListings(days = 21): number {
  return db()
    .prepare('DELETE FROM listings WHERE last_seen_at < ?')
    .run(Date.now() - days * 86_400_000).changes;
}

/* -------------------------------------------------------------------------- */
/* Push subscriptions                                                          */
/* -------------------------------------------------------------------------- */

export interface StoredSubscription {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

export function saveSubscription(sub: StoredSubscription): void {
  db()
    .prepare(
      `INSERT INTO push_subscriptions (endpoint, keys_json, created_at)
       VALUES (?,?,?)
       ON CONFLICT(endpoint) DO UPDATE SET keys_json = excluded.keys_json, failures = 0`,
    )
    .run(sub.endpoint, JSON.stringify(sub.keys), Date.now());
}

export function listSubscriptions(): StoredSubscription[] {
  const rows = db()
    .prepare('SELECT endpoint, keys_json FROM push_subscriptions WHERE failures < 5')
    .all() as { endpoint: string; keys_json: string }[];
  return rows.map((r) => ({
    endpoint: r.endpoint,
    keys: json(r.keys_json, { p256dh: '', auth: '' }),
  }));
}

export function deleteSubscription(endpoint: string): void {
  db().prepare('DELETE FROM push_subscriptions WHERE endpoint = ?').run(endpoint);
}

export function recordSubscriptionFailure(endpoint: string): void {
  db()
    .prepare('UPDATE push_subscriptions SET failures = failures + 1 WHERE endpoint = ?')
    .run(endpoint);
}

export function recordSubscriptionSuccess(endpoint: string): void {
  db()
    .prepare('UPDATE push_subscriptions SET last_ok_at = ?, failures = 0 WHERE endpoint = ?')
    .run(Date.now(), endpoint);
}

/* -------------------------------------------------------------------------- */
/* Poll runs                                                                   */
/* -------------------------------------------------------------------------- */

export function startPollRun(): number {
  const info = db().prepare('INSERT INTO poll_runs (started_at) VALUES (?)').run(Date.now());
  return Number(info.lastInsertRowid);
}

export interface PollStats {
  listingsSeen: number;
  newListings: number;
  dealsFound: number;
  notified: number;
  errors: string[];
}

export function finishPollRun(id: number, stats: PollStats): void {
  db()
    .prepare(
      `UPDATE poll_runs SET finished_at=?, listings_seen=?, new_listings=?, deals_found=?,
         notified=?, errors=? WHERE id=?`,
    )
    .run(
      Date.now(),
      stats.listingsSeen,
      stats.newListings,
      stats.dealsFound,
      stats.notified,
      JSON.stringify(stats.errors),
      id,
    );
}

export interface PollRunSummary {
  id: number;
  started_at: number;
  finished_at: number | null;
  listings_seen: number;
  new_listings: number;
  deals_found: number;
  notified: number;
  errors: string;
}

export function recentPollRuns(limit = 10): PollRunSummary[] {
  return db()
    .prepare('SELECT * FROM poll_runs ORDER BY id DESC LIMIT ?')
    .all(limit) as PollRunSummary[];
}
