import { analyze, originOf, rejectByProfile } from './analyzer.js';
import { config } from './config.js';
import {
  finishPollRun,
  getSettings,
  listProfiles,
  markNotified,
  pruneOldListings,
  saveAnalysis,
  startPollRun,
  upsertListing,
  type PollStats,
} from './db/repo.js';
import { notifyDeal } from './notify/index.js';
import { activeSources } from './sources/index.js';
import type { RawListing, ScoredDeal, SearchProfile } from './types.js';

/** eBay's Browse API has a daily ceiling; do not analyse 500 items at once. */
const ANALYSIS_CONCURRENCY = 4;
const PER_SEARCH_LIMIT = 40;

let timer: NodeJS.Timeout | null = null;
let running = false;
let lastRunAt: number | null = null;
let lastStats: PollStats | null = null;

export interface PollerStatus {
  running: boolean;
  intervalSeconds: number;
  lastRunAt: number | null;
  lastStats: PollStats | null;
  nextRunAt: number | null;
}

export function pollerStatus(): PollerStatus {
  return {
    running,
    intervalSeconds: config.pollIntervalSeconds,
    lastRunAt,
    lastStats,
    nextRunAt: lastRunAt ? lastRunAt + config.pollIntervalSeconds * 1000 : null,
  };
}

/**
 * One full sweep: every enabled profile, against every enabled source, for
 * every keyword. New listings that clear their profile's floors are pushed to
 * the phone once each.
 */
export async function runSweep(): Promise<PollStats> {
  if (running) {
    return lastStats ?? emptyStats(['a sweep was already running']);
  }
  running = true;
  const runId = startPollRun();
  const stats = emptyStats([]);
  const settings = getSettings();
  const origin = originOf(settings);
  const sources = activeSources();
  const profiles = listProfiles().filter((p) => p.enabled);

  try {
    if (sources.length === 0) stats.errors.push('no sources are enabled and configured');
    if (profiles.length === 0) stats.errors.push('no enabled search profiles');

    // One listing can match several profiles, but there is only one row per
    // listing. Without this, a later, stricter profile would overwrite an
    // earlier profile's passing analysis with its own rejection.
    const claimed = new Set<string>();

    for (const profile of profiles) {
      const allowed = profile.sources
        ? sources.filter((s) => profile.sources!.includes(s.id))
        : sources;

      const found = new Map<string, RawListing>();

      for (const source of allowed) {
        for (const keyword of profile.keywords.length ? profile.keywords : ['']) {
          try {
            const listings = await source.search({
              query: keyword,
              section: profile.section,
              minPrice: profile.minPrice,
              maxPrice: profile.maxPrice,
              lat: origin?.lat ?? null,
              lon: origin?.lon ?? null,
              radiusMi: profile.radiusMi,
              limit: PER_SEARCH_LIMIT,
            });
            for (const l of listings) found.set(`${l.sourceId}:${l.externalId}`, l);
          } catch (err) {
            stats.errors.push(
              `${source.id}/"${keyword}": ${err instanceof Error ? err.message : String(err)}`,
            );
          }
        }
      }

      stats.listingsSeen += found.size;
      await processProfile(profile, [...found.values()], stats, claimed);
    }

    pruneOldListings(21);
  } catch (err) {
    stats.errors.push(err instanceof Error ? err.message : String(err));
  } finally {
    running = false;
    lastRunAt = Date.now();
    lastStats = stats;
    finishPollRun(runId, stats);
  }

  return stats;
}

async function processProfile(
  profile: SearchProfile,
  listings: RawListing[],
  stats: PollStats,
  claimed: Set<string>,
): Promise<void> {
  const settings = getSettings();
  const queue = [...listings];
  const toNotify: ScoredDeal[] = [];

  const worker = async (): Promise<void> => {
    for (;;) {
      const listing = queue.shift();
      if (!listing) return;

      // Already surfaced by an earlier profile this sweep - leave its analysis
      // alone and skip the comp lookup entirely.
      if (claimed.has(`${listing.sourceId}:${listing.externalId}`)) continue;

      const upsert = upsertListing(listing, profile.section, profile.id);
      if (upsert.isNew) stats.newListings += 1;

      const textReject = rejectByProfile(listing, profile);
      if (textReject) {
        // Still record it so the "everything we saw" view can explain itself.
        saveAnalysis(
          upsert.key,
          skeletonDeal(listing, profile, upsert.firstSeenAt, upsert.lastSeenAt),
          false,
          textReject,
        );
        continue;
      }

      try {
        const { deal, pass, reason } = await analyze(listing, {
          profile,
          settings,
          firstSeenAt: upsert.firstSeenAt,
          lastSeenAt: upsert.lastSeenAt,
          notifiedAt: upsert.notifiedAt,
          previousAskPrice: upsert.previousAskPrice,
        });
        saveAnalysis(upsert.key, deal, pass, reason);
        if (!pass) continue;

        stats.dealsFound += 1;
        claimed.add(upsert.key);
        const priceDropped = deal.badges.some((b) => b.id === 'price_drop');
        // Notify once per listing, and again only if the seller cut the price.
        if (profile.notify && (upsert.notifiedAt === null || priceDropped)) {
          toNotify.push(deal);
        }
      } catch (err) {
        stats.errors.push(
          `analyze ${upsert.key}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(ANALYSIS_CONCURRENCY, Math.max(1, queue.length)) }, worker),
  );

  // Best deals first, so if a burst arrives the good one lands on top of the
  // notification stack.
  toNotify.sort((a, b) => b.score - a.score);
  for (const deal of toNotify) {
    const result = await notifyDeal(deal);
    if (result.delivered) {
      markNotified(deal.key);
      stats.notified += 1;
    } else if (result.skippedReason) {
      // Leave notified_at null: it goes out when quiet hours end.
    } else if (result.errors.length) {
      stats.errors.push(`notify ${deal.key}: ${result.errors.join('; ')}`);
    }
  }
}

/** A placeholder analysis for listings rejected before any valuation ran. */
function skeletonDeal(
  listing: RawListing,
  profile: SearchProfile,
  firstSeenAt: number,
  lastSeenAt: number,
): ScoredDeal {
  return {
    key: `${listing.sourceId}:${listing.externalId}`,
    section: profile.section,
    profileId: profile.id,
    listing,
    valuation: {
      resaleValue: 0,
      resaleLow: 0,
      resaleHigh: 0,
      compCount: 0,
      compSource: 'none',
      confidence: 0,
      matchQuery: '',
      soldPerWeek: null,
    },
    repair: {
      isBroken: false,
      partsCost: 0,
      partsLow: 0,
      partsHigh: 0,
      confidence: 0,
      symptoms: [],
      rulesApplied: [],
      partsOnly: false,
      sellerSaysPartsOnly: false,
      notes: [],
    },
    math: {
      askPrice: listing.askPrice ?? 0,
      resaleValue: 0,
      expectedSalePrice: 0,
      marketplaceFees: 0,
      shippingCost: 0,
      suppliesCost: 0,
      partsCost: 0,
      travelCost: 0,
      netProfit: 0,
      roi: 0,
      marginPct: 0,
      breakEvenAsk: 0,
    },
    score: 0,
    badges: [],
    adjustments: [],
    distanceMi: null,
    firstSeenAt,
    lastSeenAt,
    notifiedAt: null,
  };
}

function emptyStats(errors: string[]): PollStats {
  return { listingsSeen: 0, newListings: 0, dealsFound: 0, notified: 0, errors };
}

export function startPoller(): void {
  if (timer) return;
  const intervalMs = config.pollIntervalSeconds * 1000;

  const tick = (): void => {
    void runSweep().catch((err) => {
      console.error('[poller] sweep failed:', err);
    });
    // Jitter so repeated runs do not hit a marketplace on a fixed cadence.
    const jitter = Math.round((Math.random() - 0.5) * intervalMs * 0.2);
    timer = setTimeout(tick, intervalMs + jitter);
  };

  timer = setTimeout(tick, 2_000);
}

export function stopPoller(): void {
  if (timer) clearTimeout(timer);
  timer = null;
}
