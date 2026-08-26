/**
 * Core domain types.
 *
 * The pipeline is: SourceAdapter -> RawListing -> Valuation + RepairEstimate
 * -> DealMath -> ScoredDeal -> (notification + UI).
 */

/** The three top-level tabs in the app. Each has its own economics. */
export type Section = 'electronics' | 'appliances' | 'vehicles';

export const SECTIONS: Section[] = ['electronics', 'appliances', 'vehicles'];

/** A listing exactly as a marketplace gave it to us, before any analysis. */
export interface RawListing {
  /** Adapter that produced this, e.g. 'craigslist'. */
  sourceId: string;
  /** Stable id within that source. `sourceId:externalId` is the global key. */
  externalId: string;
  url: string;
  title: string;
  description?: string | null;
  /** null when the seller wrote "make offer" / left it blank. */
  askPrice: number | null;
  currency: string;
  imageUrl?: string | null;
  /** Epoch ms the seller posted it (not when we saw it). */
  postedAt: number;
  lat?: number | null;
  lon?: number | null;
  locationName?: string | null;
  /** Source-specific extras: mileage, year, model, condition string, ... */
  attributes?: Record<string, string | number | boolean>;
}

export type CompSource = 'ebay_sold' | 'ebay_active' | 'local' | 'sample' | 'none';

/** What we think the item is worth once resold, and how sure we are. */
export interface Valuation {
  /** Expected realized sale price, working, before fees. */
  resaleValue: number;
  /** 25th / 75th percentile of the comp set, after outlier trimming. */
  resaleLow: number;
  resaleHigh: number;
  compCount: number;
  compSource: CompSource;
  /** 0..1. Blend of sample size, price dispersion and title-match quality. */
  confidence: number;
  /** The query we actually sent to the comp source; shown in the UI. */
  matchQuery: string;
  /** Comps sold per week, when sold data is available. Drives the HOT badge. */
  soldPerWeek?: number | null;
}

/** What it will cost in *parts* to make the thing sellable. Labour is free. */
export interface RepairEstimate {
  isBroken: boolean;
  /** Point estimate used by the deal math. */
  partsCost: number;
  partsLow: number;
  partsHigh: number;
  /** 0..1. Low when the seller only said "as-is / untested". */
  confidence: number;
  /** Phrases we matched in the listing, e.g. 'no power', 'cracked screen'. */
  symptoms: string[];
  /** Ids of the repair rules that fired, for auditing an estimate. */
  rulesApplied: string[];
  /**
   * A hard verdict from a repair rule: this cannot be economically fixed at
   * all (cracked TV panel, iCloud lock, dead output transformer). Distinct
   * from the seller merely writing "for parts" - see sellerSaysPartsOnly.
   */
  partsOnly: boolean;
  /**
   * The seller used for-parts language. On its own this is weak evidence:
   * "for parts or repair" is what people write when they simply will not
   * warrant the item, and those are exactly the listings worth buying.
   */
  sellerSaysPartsOnly: boolean;
  notes: string[];
}

/** Every number that goes into the profit figure, kept for display. */
export interface DealMath {
  askPrice: number;
  resaleValue: number;
  /** Sale price we model, after any parts-only or condition haircut. */
  expectedSalePrice: number;
  marketplaceFees: number;
  shippingCost: number;
  suppliesCost: number;
  partsCost: number;
  travelCost: number;
  netProfit: number;
  /** netProfit / total cash out. */
  roi: number;
  /** netProfit / expectedSalePrice. */
  marginPct: number;
  /** Highest ask price at which this still clears the profile's floor. */
  breakEvenAsk: number;
}

export type BadgeId =
  | 'just_listed'
  | 'hot'
  | 'high_margin'
  | 'steal'
  | 'broken'
  | 'parts_only'
  | 'very_close'
  | 'far'
  | 'price_drop'
  | 'low_confidence'
  | 'no_price'
  | 'thin_comps';

export interface Badge {
  id: BadgeId;
  label: string;
  /** Emoji so the PWA needs no icon pipeline. */
  icon: string;
  tone: 'good' | 'warn' | 'info';
  /** Why it fired, e.g. "listed 4 min ago". */
  detail: string;
}

/** A condition-driven reduction applied to the comp median. */
export interface ResaleAdjustment {
  id: string;
  pct: number;
  note: string;
  matched: string;
}

export interface ScoredDeal {
  key: string;
  section: Section;
  profileId: number;
  listing: RawListing;
  valuation: Valuation;
  repair: RepairEstimate;
  math: DealMath;
  /** 0..100 composite ranking, confidence-weighted. */
  score: number;
  badges: Badge[];
  /** Why expectedSalePrice sits below the comp median, if it does. */
  adjustments: ResaleAdjustment[];
  distanceMi: number | null;
  firstSeenAt: number;
  lastSeenAt: number;
  notifiedAt: number | null;
}

/** A saved search. This is the "refine the search terms" surface. */
export interface SearchProfile {
  id: number;
  section: Section;
  name: string;
  enabled: boolean;
  /** OR'd. Each is sent to every source as its own query. */
  keywords: string[];
  /** Listing is dropped unless the title/body contains all of these. */
  mustInclude: string[];
  /** Listing is dropped if the title/body contains any of these. */
  exclude: string[];
  minPrice: number | null;
  maxPrice: number | null;
  radiusMi: number;
  /** Deal floors. A listing must clear both to be surfaced. */
  minProfit: number;
  minRoi: number;
  /** Include listings the seller flagged as broken/for-parts. */
  includeBroken: boolean;
  /** Suppress items whose value confidence is below this. */
  minConfidence: number;
  notify: boolean;
  sources: string[] | null;
  createdAt: number;
  updatedAt: number;
}

export interface AppSettings {
  lat: number | null;
  lon: number | null;
  locationUpdatedAt: number | null;
  /** $/mile round trip, used as an acquisition cost. */
  mileageCostPerMi: number;
  /** Promoted-listing ad rate, if you run them. 0 = off. */
  ebayAdRatePct: number;
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
}
