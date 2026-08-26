/**
 * Mirrors the server's domain types. Kept as a hand-written copy rather than a
 * shared package so the two halves can be deployed independently; the API
 * shapes are small and change together.
 */

export type Section = 'electronics' | 'appliances' | 'vehicles';

export interface Badge {
  id: string;
  label: string;
  icon: string;
  tone: 'good' | 'warn' | 'info';
  detail: string;
}

export interface Listing {
  sourceId: string;
  externalId: string;
  url: string;
  title: string;
  description: string | null;
  askPrice: number | null;
  currency: string;
  imageUrl: string | null;
  postedAt: number;
  lat: number | null;
  lon: number | null;
  locationName: string | null;
  attributes?: Record<string, string | number | boolean>;
}

export interface Valuation {
  resaleValue: number;
  resaleLow: number;
  resaleHigh: number;
  compCount: number;
  compSource: 'ebay_sold' | 'ebay_active' | 'local' | 'sample' | 'none';
  confidence: number;
  matchQuery: string;
  soldPerWeek: number | null;
}

export interface RepairEstimate {
  isBroken: boolean;
  partsCost: number;
  partsLow: number;
  partsHigh: number;
  confidence: number;
  symptoms: string[];
  rulesApplied: string[];
  partsOnly: boolean;
  sellerSaysPartsOnly: boolean;
  notes: string[];
}

export interface DealMath {
  askPrice: number;
  resaleValue: number;
  expectedSalePrice: number;
  marketplaceFees: number;
  shippingCost: number;
  suppliesCost: number;
  partsCost: number;
  travelCost: number;
  netProfit: number;
  roi: number;
  marginPct: number;
  breakEvenAsk: number;
}

export interface ResaleAdjustment {
  id: string;
  pct: number;
  note: string;
  matched: string;
}

export interface Deal {
  key: string;
  section: Section;
  profileId: number;
  listing: Listing;
  valuation: Valuation;
  repair: RepairEstimate;
  math: DealMath;
  score: number;
  badges: Badge[];
  adjustments: ResaleAdjustment[];
  distanceMi: number | null;
  firstSeenAt: number;
  lastSeenAt: number;
  notifiedAt: number | null;
}

export interface Profile {
  id: number;
  section: Section;
  name: string;
  enabled: boolean;
  keywords: string[];
  mustInclude: string[];
  exclude: string[];
  minPrice: number | null;
  maxPrice: number | null;
  radiusMi: number;
  minProfit: number;
  minRoi: number;
  includeBroken: boolean;
  minConfidence: number;
  notify: boolean;
  sources: string[] | null;
  createdAt: number;
  updatedAt: number;
}

export interface Settings {
  lat: number | null;
  lon: number | null;
  locationUpdatedAt: number | null;
  mileageCostPerMi: number;
  ebayAdRatePct: number;
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
}

export interface SourceStatus {
  id: string;
  label: string;
  enabled: boolean;
  available: boolean;
  reason: string | null;
  docsAnchor: string;
}

export interface Status {
  poller: {
    running: boolean;
    intervalSeconds: number;
    lastRunAt: number | null;
    nextRunAt: number | null;
    lastStats: {
      listingsSeen: number;
      newListings: number;
      dealsFound: number;
      notified: number;
      errors: string[];
    } | null;
  };
  sources: SourceStatus[];
  notifications: {
    channels: string[];
    subscriptions: number;
    quietHours: { start: number; end: number } | null;
  };
  comps: { ebayConfigured: boolean; soldDataEnabled: boolean };
  feeModels: Record<Section, string>;
  location: {
    lat: number | null;
    lon: number | null;
    updatedAt: number | null;
    fallbackConfigured: boolean;
  };
}

export type SortKey = 'score' | 'new' | 'profit' | 'distance';
