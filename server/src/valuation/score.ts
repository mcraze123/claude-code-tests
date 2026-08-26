import type {
  Badge,
  DealMath,
  RawListing,
  RepairEstimate,
  ScoredDeal,
  SearchProfile,
  Section,
  Valuation,
} from '../types.js';
import { round2, travelCost } from '../util/geo.js';
import {
  estimateFulfillment,
  localHaircut,
  marketplaceFees,
  resaleHaircuts,
} from './fees.js';
import { partsOutPct } from './repair.js';

export interface ScoreInput {
  listing: RawListing;
  section: Section;
  profile: SearchProfile;
  valuation: Valuation;
  repair: RepairEstimate;
  distanceMi: number | null;
  mileageCostPerMi: number;
  adRatePct: number;
  firstSeenAt: number;
  lastSeenAt: number;
  notifiedAt: number | null;
  /** Ask price the last time we saw this listing, for drop detection. */
  previousAskPrice?: number | null;
  now?: number;
}

/**
 * Should this be valued as a bag of parts rather than a working unit?
 *
 * A repair rule saying so is definitive. Otherwise it comes down to money: a
 * repair that eats most of the resale value is not a repair, it is a parts
 * donor. The seller's own "for parts" wording only decides it when we could
 * not diagnose anything - by itself it usually just means "no warranty", and
 * those listings are the whole reason this app exists.
 */
export function isPartsOut(valuation: Valuation, repair: RepairEstimate): boolean {
  if (repair.partsOnly) return true;
  if (valuation.resaleValue > 0 && repair.partsCost > 0.6 * valuation.resaleValue) return true;
  const undiagnosed = repair.rulesApplied.length === 0 || repair.rulesApplied.includes('fallback');
  return repair.sellerSaysPartsOnly && undiagnosed;
}

/**
 * Full cost stack for a flip:
 *
 *   profit = sale - platform fees - shipping - packaging
 *          - repair parts - purchase price - fuel
 *
 * Labour is intentionally absent: the operator does the repair themselves and
 * is deciding whether the *cash* works, not what their hour is worth.
 */
export function computeDealMath(input: ScoreInput): DealMath {
  const { listing, section, valuation, repair } = input;
  const askPrice = listing.askPrice ?? 0;
  const fulfillment = estimateFulfillment(listing.title, section);

  const partsOut = isPartsOut(valuation, repair);

  let expectedSalePrice = valuation.resaleValue;
  // A cracked TV panel is not a TV, it is a bag of boards.
  if (partsOut) expectedSalePrice *= partsOutPct(listing.title);
  // Branded title, missing charger, smoke smell: the comp median assumed none
  // of that. Parting out already prices the damage, so skip it there.
  if (!partsOut) {
    expectedSalePrice *= resaleHaircuts(`${listing.title} ${listing.description ?? ''}`).factor;
  }
  expectedSalePrice *= 1 - localHaircut(section, fulfillment.localOnly);
  expectedSalePrice = round2(expectedSalePrice);

  // We model free shipping baked into the price, which is what actually sells
  // on eBay: the buyer pays nothing extra and we absorb the label.
  const fees = marketplaceFees(expectedSalePrice, 0, section, input.adRatePct);
  const shippingCost = partsOut && fulfillment.localOnly ? 0 : fulfillment.shippingCost;
  const suppliesCost = shippingCost > 0 ? fulfillment.suppliesCost : 0;
  // Parting out means no repair happens, so no parts are bought.
  const partsCost = partsOut ? 0 : repair.partsCost;
  const travel = travelCost(input.distanceMi, input.mileageCostPerMi);

  const netProfit = round2(
    expectedSalePrice - fees - shippingCost - suppliesCost - partsCost - askPrice - travel,
  );
  const cashOut = askPrice + partsCost + travel;
  const roi = cashOut > 0 ? round2(netProfit / cashOut) : netProfit > 0 ? 99 : 0;
  const marginPct = expectedSalePrice > 0 ? round2(netProfit / expectedSalePrice) : 0;
  const breakEvenAsk = round2(
    expectedSalePrice - fees - shippingCost - suppliesCost - partsCost - travel -
      input.profile.minProfit,
  );

  return {
    askPrice,
    resaleValue: valuation.resaleValue,
    expectedSalePrice,
    marketplaceFees: fees,
    shippingCost,
    suppliesCost,
    partsCost,
    travelCost: travel,
    netProfit,
    roi,
    marginPct,
    breakEvenAsk,
  };
}

/**
 * Combined confidence in the whole estimate. The repair number only matters
 * when there is a repair, and it matters in proportion to how much of the
 * spread it consumes.
 */
export function overallConfidence(
  valuation: Valuation,
  repair: RepairEstimate,
  math: DealMath,
): number {
  if (!repair.isBroken || math.partsCost === 0) return valuation.confidence;
  const repairShare = Math.min(
    1,
    math.partsCost / Math.max(1, math.expectedSalePrice),
  );
  const weight = 0.3 + 0.5 * repairShare;
  return round2(valuation.confidence * (1 - weight) + repair.confidence * weight);
}

const MINUTE = 60_000;

export function computeBadges(input: ScoreInput, math: DealMath): Badge[] {
  const now = input.now ?? Date.now();
  const { listing, valuation, repair, profile } = input;
  const badges: Badge[] = [];
  const ageMin = Math.max(0, Math.round((now - listing.postedAt) / MINUTE));
  const confidence = overallConfidence(valuation, repair, math);

  if (ageMin <= 20) {
    badges.push({
      id: 'just_listed',
      label: 'Just listed',
      icon: '⚡',
      tone: 'good',
      detail: ageMin <= 1 ? 'listed under a minute ago' : `listed ${ageMin} min ago`,
    });
  }

  const soldPerWeek = valuation.soldPerWeek ?? null;
  if (soldPerWeek !== null && soldPerWeek >= 5) {
    badges.push({
      id: 'hot',
      label: 'Hot item',
      icon: '🔥',
      tone: 'good',
      detail: `${soldPerWeek}/week sell on eBay`,
    });
  } else if (soldPerWeek === null && valuation.compCount >= 25 && valuation.confidence >= 0.6) {
    badges.push({
      id: 'hot',
      label: 'Hot item',
      icon: '🔥',
      tone: 'good',
      detail: `${valuation.compCount} tightly-priced comps listed`,
    });
  }

  if (math.marginPct >= 0.45 && math.netProfit > 0) {
    badges.push({
      id: 'high_margin',
      label: 'High margin',
      icon: '💰',
      tone: 'good',
      detail: `${Math.round(math.marginPct * 100)}% of sale price is profit`,
    });
  }

  if (
    math.expectedSalePrice > 0 &&
    math.askPrice > 0 &&
    math.askPrice <= 0.3 * math.expectedSalePrice &&
    math.netProfit >= profile.minProfit * 2
  ) {
    badges.push({
      id: 'steal',
      label: 'Steal',
      icon: '🎯',
      tone: 'good',
      detail: `asking ${Math.round((math.askPrice / math.expectedSalePrice) * 100)}% of resale`,
    });
  }

  if (isPartsOut(valuation, repair)) {
    badges.push({
      id: 'parts_only',
      label: 'Parts only',
      icon: '🧩',
      tone: 'warn',
      detail: repair.partsOnly
        ? 'not economically repairable — valued as parts'
        : 'repair costs too much of its value — valued as parts',
    });
  } else if (repair.isBroken) {
    badges.push({
      id: 'broken',
      label: 'Needs repair',
      icon: '🔧',
      tone: 'info',
      detail: repair.symptoms.length
        ? `${repair.symptoms.slice(0, 2).join(', ')} · ~$${Math.round(repair.partsCost)} parts`
        : `~$${Math.round(repair.partsCost)} in parts`,
    });
  }

  if (input.distanceMi !== null) {
    if (input.distanceMi <= 5) {
      badges.push({
        id: 'very_close',
        label: 'Close by',
        icon: '📍',
        tone: 'good',
        detail: `${input.distanceMi.toFixed(1)} mi away`,
      });
    } else if (input.distanceMi >= profile.radiusMi * 0.8) {
      badges.push({
        id: 'far',
        label: 'Long drive',
        icon: '🛣️',
        tone: 'warn',
        detail: `${Math.round(input.distanceMi)} mi · $${math.travelCost.toFixed(2)} fuel`,
      });
    }
  }

  if (
    input.previousAskPrice != null &&
    listing.askPrice != null &&
    listing.askPrice < input.previousAskPrice
  ) {
    const drop = input.previousAskPrice - listing.askPrice;
    badges.push({
      id: 'price_drop',
      label: 'Price drop',
      icon: '📉',
      tone: 'good',
      detail: `down $${Math.round(drop)} from $${Math.round(input.previousAskPrice)}`,
    });
  }

  if (listing.askPrice === null) {
    badges.push({
      id: 'no_price',
      label: 'No price',
      icon: '❓',
      tone: 'warn',
      detail: 'seller did not list a price — profit shown assumes free',
    });
  }

  if (valuation.compCount > 0 && valuation.compCount < 5) {
    badges.push({
      id: 'thin_comps',
      label: 'Thin comps',
      icon: '🪶',
      tone: 'warn',
      detail: `only ${valuation.compCount} comparable sale${valuation.compCount === 1 ? '' : 's'}`,
    });
  }

  if (confidence < 0.4) {
    badges.push({
      id: 'low_confidence',
      label: 'Low confidence',
      icon: '⚠️',
      tone: 'warn',
      detail: `${Math.round(confidence * 100)}% confidence in these numbers`,
    });
  }

  return badges;
}

/**
 * 0..100 ranking. Profit dominates, ROI keeps small cheap flips competitive
 * with big slow ones, freshness rewards being first, and the whole thing is
 * scaled by confidence so a $900 guess never outranks a $300 certainty.
 */
export function computeScore(input: ScoreInput, math: DealMath): number {
  const now = input.now ?? Date.now();
  const confidence = overallConfidence(input.valuation, input.repair, math);
  if (math.netProfit <= 0) return 0;

  // Reference profit: 4x the user's own floor is "a great one" for them.
  const reference = Math.max(25, input.profile.minProfit * 4);
  const profitScore = Math.tanh(math.netProfit / reference);
  const roiScore = Math.tanh(math.roi / Math.max(0.25, input.profile.minRoi * 3));

  const ageHours = Math.max(0, (now - input.listing.postedAt) / 3_600_000);
  const freshness = Math.exp(-ageHours / 12); // half-life of about 8 hours

  const base = 0.5 * profitScore + 0.3 * roiScore + 0.2 * freshness;

  // A long drive is a real tax on a flip beyond the fuel already charged.
  const distancePenalty =
    input.distanceMi === null ? 0.05 : Math.min(0.25, input.distanceMi / 400);

  const score = 100 * base * (0.35 + 0.65 * confidence) * (1 - distancePenalty);
  return Math.max(0, Math.min(100, Math.round(score)));
}

/** Does this clear the profile's floors and deserve to be shown at all? */
export function meetsThresholds(
  input: ScoreInput,
  math: DealMath,
): { pass: boolean; reason?: string } {
  const { profile, valuation, repair } = input;
  const confidence = overallConfidence(valuation, repair, math);

  if (valuation.compSource === 'none') return { pass: false, reason: 'no comps found' };
  if (math.netProfit < profile.minProfit) {
    return { pass: false, reason: `profit $${math.netProfit} below floor $${profile.minProfit}` };
  }
  if (math.roi < profile.minRoi) {
    return { pass: false, reason: `ROI ${math.roi} below floor ${profile.minRoi}` };
  }
  if (confidence < profile.minConfidence) {
    return { pass: false, reason: `confidence ${confidence} below floor ${profile.minConfidence}` };
  }
  if (repair.isBroken && !profile.includeBroken) {
    return { pass: false, reason: 'broken items excluded by profile' };
  }
  return { pass: true };
}

export function buildScoredDeal(input: ScoreInput): ScoredDeal {
  const math = computeDealMath(input);
  const partsOut = isPartsOut(input.valuation, input.repair);
  const adjustments = partsOut
    ? []
    : resaleHaircuts(`${input.listing.title} ${input.listing.description ?? ''}`).applied;
  return {
    key: `${input.listing.sourceId}:${input.listing.externalId}`,
    section: input.section,
    profileId: input.profile.id,
    listing: input.listing,
    valuation: input.valuation,
    repair: input.repair,
    math,
    score: computeScore(input, math),
    badges: computeBadges(input, math),
    adjustments,
    distanceMi: input.distanceMi === null ? null : round2(input.distanceMi),
    firstSeenAt: input.firstSeenAt,
    lastSeenAt: input.lastSeenAt,
    notifiedAt: input.notifiedAt,
  };
}
