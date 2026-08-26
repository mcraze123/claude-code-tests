import type { CompSource, Valuation } from '../types.js';
import { round2 } from '../util/geo.js';
import { titleSimilarity } from '../util/text.js';

export interface Comp {
  title: string;
  price: number;
  /** Epoch ms. Present only for sold comps. */
  soldAt?: number | null;
  condition?: string | null;
  url?: string | null;
}

/**
 * Active eBay listings are asking prices, and asking prices are optimistic.
 * Across most used-electronics categories, realised sale prices land around
 * 80-85% of the active median. This factor converts one to the other; drop it
 * to 1.0 the day you get Marketplace Insights (sold) access.
 */
export const ACTIVE_TO_SOLD_FACTOR = 0.82;

/** Comps below this title similarity are treated as different products. */
const MIN_SIMILARITY = 0.35;

/** At least this many comps before we stop scraping the barrel for matches. */
const MIN_KEPT = 5;

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  if (sorted.length === 1) return sorted[0]!;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  const w = idx - lo;
  return sorted[lo]! * (1 - w) + sorted[hi]! * w;
}

export function median(values: number[]): number {
  return percentile([...values].sort((a, b) => a - b), 0.5);
}

/**
 * Drop prices outside the 1.5 x IQR fence. On marketplace comps this mostly
 * removes accessory listings ("RTX 3080 *box only*") and lot listings.
 */
export function trimOutliers(values: number[]): number[] {
  if (values.length < 4) return values;
  const sorted = [...values].sort((a, b) => a - b);
  const q1 = percentile(sorted, 0.25);
  const q3 = percentile(sorted, 0.75);
  const iqr = q3 - q1;
  const lo = q1 - 1.5 * iqr;
  const hi = q3 + 1.5 * iqr;
  const kept = sorted.filter((v) => v >= lo && v <= hi);
  return kept.length >= 3 ? kept : sorted;
}

export interface SummarizeOptions {
  compSource: CompSource;
  matchQuery: string;
  /** Sold comps only: how many changed hands per week. */
  soldPerWeek?: number | null;
}

/**
 * Turn a bag of comps into a defensible resale number plus a confidence we
 * are willing to show the user. Confidence blends three independent worries:
 * too few comps, comps that disagree with each other, and comps that are not
 * really the same product.
 */
export function summarizeComps(
  sourceTitle: string,
  comps: Comp[],
  opts: SummarizeOptions,
): Valuation {
  const empty: Valuation = {
    resaleValue: 0,
    resaleLow: 0,
    resaleHigh: 0,
    compCount: 0,
    compSource: 'none',
    confidence: 0,
    matchQuery: opts.matchQuery,
    soldPerWeek: opts.soldPerWeek ?? null,
  };
  if (comps.length === 0) return empty;

  const scored = comps
    .filter((c) => Number.isFinite(c.price) && c.price > 0)
    .map((c) => ({ comp: c, sim: titleSimilarity(sourceTitle, c.title) }))
    .sort((a, b) => b.sim - a.sim);

  if (scored.length === 0) return empty;

  // Prefer confident matches, but if the strict cut leaves almost nothing,
  // fall back to the best few and let confidence reflect the compromise.
  let kept = scored.filter((s) => s.sim >= MIN_SIMILARITY);
  if (kept.length < MIN_KEPT) kept = scored.slice(0, Math.min(MIN_KEPT, scored.length));

  const prices = trimOutliers(kept.map((s) => s.comp.price));
  if (prices.length === 0) return empty;

  const sorted = [...prices].sort((a, b) => a - b);
  const rawMedian = percentile(sorted, 0.5);
  const factor = opts.compSource === 'ebay_active' ? ACTIVE_TO_SOLD_FACTOR : 1;

  const meanSim = kept.reduce((acc, s) => acc + s.sim, 0) / kept.length;
  const q1 = percentile(sorted, 0.25);
  const q3 = percentile(sorted, 0.75);
  const dispersion = rawMedian > 0 ? (q3 - q1) / rawMedian : 1;

  // Each sub-score is 0..1 and independently interpretable.
  const sizeScore = Math.min(1, Math.log(prices.length + 1) / Math.log(16));
  const agreementScore = Math.max(0, 1 - dispersion);
  const matchScore = Math.min(1, meanSim / 0.8);
  const sourceScore =
    opts.compSource === 'ebay_sold' ? 1 : opts.compSource === 'sample' ? 0.9 : 0.78;

  const confidence =
    (0.35 * sizeScore + 0.3 * agreementScore + 0.35 * matchScore) * sourceScore;

  return {
    resaleValue: round2(rawMedian * factor),
    resaleLow: round2(q1 * factor),
    resaleHigh: round2(q3 * factor),
    compCount: prices.length,
    compSource: opts.compSource,
    confidence: Math.max(0, Math.min(1, Math.round(confidence * 100) / 100)),
    matchQuery: opts.matchQuery,
    soldPerWeek: opts.soldPerWeek ?? null,
  };
}
