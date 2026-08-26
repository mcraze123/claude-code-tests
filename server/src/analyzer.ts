import { config } from './config.js';
import type {
  AppSettings,
  CompSource,
  RawListing,
  ScoredDeal,
  SearchProfile,
} from './types.js';
import { distanceMi, isPoint } from './util/geo.js';
import { buildCompQuery, containsAny } from './util/text.js';
import { summarizeComps, type Comp } from './valuation/comps.js';
import { fetchComps } from './valuation/ebay.js';
import { estimateRepair, stripSymptoms } from './valuation/repair.js';
import { buildScoredDeal, meetsThresholds } from './valuation/score.js';
import { sampleComps } from './sources/sample.js';

export interface AnalyzeContext {
  profile: SearchProfile;
  settings: AppSettings;
  firstSeenAt: number;
  lastSeenAt: number;
  notifiedAt: number | null;
  previousAskPrice: number | null;
  now?: number;
}

export interface AnalyzeResult {
  deal: ScoredDeal;
  pass: boolean;
  reason: string | null;
}

/** Where distances are measured from: the phone's last fix, else HOME_* in .env. */
export function originOf(settings: AppSettings): { lat: number; lon: number } | null {
  if (isPoint(settings.lat, settings.lon)) {
    return { lat: settings.lat!, lon: settings.lon! };
  }
  if (isPoint(config.home.lat, config.home.lon)) {
    return { lat: config.home.lat!, lon: config.home.lon! };
  }
  return null;
}

/**
 * Cheap text filters, applied before we spend an API call on comps.
 * Returns null when the listing passes, or the reason it was dropped.
 */
export function rejectByProfile(
  listing: RawListing,
  profile: SearchProfile,
): string | null {
  const hay = `${listing.title} ${listing.description ?? ''}`;

  if (profile.exclude.length > 0) {
    const hits = containsAny(hay, profile.exclude);
    if (hits.length > 0) return `excluded term: ${hits[0]}`;
  }
  if (profile.mustInclude.length > 0) {
    const missing = profile.mustInclude.filter((t) => containsAny(hay, [t]).length === 0);
    if (missing.length > 0) return `missing required term: ${missing[0]}`;
  }
  if (listing.askPrice !== null) {
    if (profile.minPrice != null && listing.askPrice < profile.minPrice) {
      return `ask $${listing.askPrice} below profile minimum`;
    }
    if (profile.maxPrice != null && listing.askPrice > profile.maxPrice) {
      return `ask $${listing.askPrice} above profile maximum`;
    }
  }
  return null;
}

/** Fixtures bring their own comps; everything else goes to eBay. */
async function compsFor(
  listing: RawListing,
  query: string,
): Promise<{ comps: Comp[]; source: CompSource; soldPerWeek: number | null; error?: string }> {
  if (listing.sourceId === 'sample') {
    const comps = sampleComps(listing.externalId);
    return { comps, source: comps.length ? 'sample' : 'none', soldPerWeek: null };
  }
  const res = await fetchComps(query);
  return {
    comps: res.comps,
    source: res.source,
    soldPerWeek: res.soldPerWeek,
    ...(res.error ? { error: res.error } : {}),
  };
}

/**
 * Full analysis for one listing: what it is worth, what the repair costs,
 * what is left after fees and fuel, and whether that clears the profile's
 * floors.
 */
export async function analyze(
  listing: RawListing,
  ctx: AnalyzeContext,
): Promise<AnalyzeResult> {
  const { profile, settings } = ctx;

  const origin = originOf(settings);
  const distance =
    origin && isPoint(listing.lat, listing.lon)
      ? distanceMi(origin, { lat: listing.lat!, lon: listing.lon! })
      : null;

  const repair = estimateRepair(listing.title, listing.description);
  // Value the product, not the fault: "no display" belongs to the repair
  // estimate, and would only wreck the comp search.
  const query = buildCompQuery(stripSymptoms(listing.title)) || buildCompQuery(listing.title);
  const { comps, source, soldPerWeek, error } = await compsFor(listing, query);

  const valuation = summarizeComps(listing.title, comps, {
    compSource: source,
    matchQuery: query,
    soldPerWeek,
  });

  const deal = buildScoredDeal({
    listing,
    section: profile.section,
    profile,
    valuation,
    repair,
    distanceMi: distance,
    mileageCostPerMi: settings.mileageCostPerMi,
    adRatePct: settings.ebayAdRatePct,
    firstSeenAt: ctx.firstSeenAt,
    lastSeenAt: ctx.lastSeenAt,
    notifiedAt: ctx.notifiedAt,
    previousAskPrice: ctx.previousAskPrice,
    ...(ctx.now !== undefined ? { now: ctx.now } : {}),
  });

  // Distance is only a filter when we actually know where the item is.
  if (distance !== null && distance > profile.radiusMi) {
    return { deal, pass: false, reason: `${Math.round(distance)} mi outside ${profile.radiusMi} mi radius` };
  }

  const verdict = meetsThresholds(
    {
      listing,
      section: profile.section,
      profile,
      valuation,
      repair,
      distanceMi: distance,
      mileageCostPerMi: settings.mileageCostPerMi,
      adRatePct: settings.ebayAdRatePct,
      firstSeenAt: ctx.firstSeenAt,
      lastSeenAt: ctx.lastSeenAt,
      notifiedAt: ctx.notifiedAt,
      previousAskPrice: ctx.previousAskPrice,
    },
    deal.math,
  );

  const reason = verdict.pass ? null : (error ? `${verdict.reason} (${error})` : verdict.reason ?? null);
  return { deal, pass: verdict.pass, reason };
}
