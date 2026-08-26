import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { RawListing, RepairEstimate, SearchProfile, Valuation } from '../types.js';
import {
  computeBadges,
  computeDealMath,
  computeScore,
  isPartsOut,
  meetsThresholds,
  overallConfidence,
  type ScoreInput,
} from './score.js';

const NOW = Date.UTC(2026, 0, 15, 12, 0, 0);

const listing = (over: Partial<RawListing> = {}): RawListing => ({
  sourceId: 'test',
  externalId: '1',
  url: 'https://example.invalid/1',
  title: 'EVGA RTX 3080 10GB graphics card',
  description: null,
  askPrice: 120,
  currency: 'USD',
  imageUrl: null,
  postedAt: NOW - 5 * 60_000,
  lat: 37.7,
  lon: -122.1,
  locationName: 'Test City',
  ...over,
});

const valuation = (over: Partial<Valuation> = {}): Valuation => ({
  resaleValue: 342,
  resaleLow: 328,
  resaleHigh: 355,
  compCount: 12,
  compSource: 'ebay_sold',
  confidence: 0.8,
  matchQuery: 'evga rtx 3080',
  soldPerWeek: null,
  ...over,
});

const repair = (over: Partial<RepairEstimate> = {}): RepairEstimate => ({
  isBroken: false,
  partsCost: 0,
  partsLow: 0,
  partsHigh: 0,
  confidence: 0.8,
  symptoms: [],
  rulesApplied: [],
  partsOnly: false,
  sellerSaysPartsOnly: false,
  notes: [],
  ...over,
});

const profile = (over: Partial<SearchProfile> = {}): SearchProfile => ({
  id: 1,
  section: 'electronics',
  name: 'test',
  enabled: true,
  keywords: [],
  mustInclude: [],
  exclude: [],
  minPrice: null,
  maxPrice: null,
  radiusMi: 30,
  minProfit: 60,
  minRoi: 0.5,
  includeBroken: true,
  minConfidence: 0.35,
  notify: true,
  sources: null,
  createdAt: 0,
  updatedAt: 0,
  ...over,
});

const input = (over: Partial<ScoreInput> = {}): ScoreInput => ({
  listing: listing(),
  section: 'electronics',
  profile: profile(),
  valuation: valuation(),
  repair: repair(),
  distanceMi: 4,
  mileageCostPerMi: 0.35,
  adRatePct: 0,
  firstSeenAt: NOW,
  lastSeenAt: NOW,
  notifiedAt: null,
  now: NOW,
  ...over,
});

describe('computeDealMath', () => {
  it('subtracts every cost from the sale price', () => {
    const math = computeDealMath(input());
    const reconstructed =
      math.expectedSalePrice -
      math.marketplaceFees -
      math.shippingCost -
      math.suppliesCost -
      math.partsCost -
      math.askPrice -
      math.travelCost;
    assert.equal(Math.round(reconstructed * 100) / 100, math.netProfit);
  });

  it('charges parts for a repairable item', () => {
    const broken = computeDealMath(
      input({ repair: repair({ isBroken: true, partsCost: 18, rulesApplied: ['gpu-no-display'] }) }),
    );
    const working = computeDealMath(input());
    assert.equal(broken.partsCost, 18);
    assert.equal(Math.round((working.netProfit - broken.netProfit) * 100) / 100, 18);
  });

  it('reports the ask price at which the deal stops clearing the floor', () => {
    const math = computeDealMath(input());
    const atBreakEven = computeDealMath(
      input({ listing: listing({ askPrice: math.breakEvenAsk }) }),
    );
    // Fuel is unchanged, so profit at the break-even ask equals the floor.
    assert.ok(Math.abs(atBreakEven.netProfit - 60) < 0.5, String(atBreakEven.netProfit));
  });

  it('treats a no-price listing as free rather than skipping it', () => {
    const math = computeDealMath(input({ listing: listing({ askPrice: null }) }));
    assert.equal(math.askPrice, 0);
    assert.ok(math.netProfit > 0);
  });

  it('gives an unlimited ROI to a free item with no other cash cost', () => {
    const math = computeDealMath(
      input({ listing: listing({ askPrice: 0, lat: null, lon: null }), distanceMi: null }),
    );
    assert.ok(math.roi > 10);
  });
});

describe('isPartsOut', () => {
  it('honours a hard verdict from a repair rule', () => {
    assert.equal(isPartsOut(valuation(), repair({ partsOnly: true })), true);
  });

  it('ignores seller wording when a real repair was diagnosed', () => {
    const r = repair({
      isBroken: true,
      partsCost: 18,
      sellerSaysPartsOnly: true,
      rulesApplied: ['gpu-no-display'],
    });
    assert.equal(isPartsOut(valuation(), r), false);
  });

  it('believes seller wording when nothing could be diagnosed', () => {
    const r = repair({
      isBroken: true,
      partsCost: 30,
      sellerSaysPartsOnly: true,
      rulesApplied: ['fallback'],
    });
    assert.equal(isPartsOut(valuation(), r), true);
  });

  it('parts out a repair that eats most of the value', () => {
    const r = repair({ isBroken: true, partsCost: 300, rulesApplied: ['x'] });
    assert.equal(isPartsOut(valuation({ resaleValue: 342 }), r), true);
  });
});

describe('overallConfidence', () => {
  it('is the value confidence when nothing needs repair', () => {
    const math = computeDealMath(input());
    assert.equal(overallConfidence(valuation(), repair(), math), 0.8);
  });

  it('is dragged down by an uncertain repair estimate', () => {
    const r = repair({ isBroken: true, partsCost: 120, confidence: 0.2, rulesApplied: ['x'] });
    const math = computeDealMath(input({ repair: r }));
    assert.ok(overallConfidence(valuation(), r, math) < 0.7);
  });

  it('weights the repair more when it is a bigger share of the value', () => {
    const small = repair({ isBroken: true, partsCost: 15, confidence: 0.2, rulesApplied: ['x'] });
    const large = repair({ isBroken: true, partsCost: 180, confidence: 0.2, rulesApplied: ['x'] });
    const cSmall = overallConfidence(valuation(), small, computeDealMath(input({ repair: small })));
    const cLarge = overallConfidence(valuation(), large, computeDealMath(input({ repair: large })));
    assert.ok(cLarge < cSmall);
  });
});

describe('computeBadges', () => {
  const idsFor = (over: Partial<ScoreInput> = {}): string[] => {
    const i = input(over);
    return computeBadges(i, computeDealMath(i)).map((b) => b.id);
  };

  it('flags a listing posted minutes ago', () => {
    assert.ok(idsFor().includes('just_listed'));
  });

  it('does not flag a day-old listing as just listed', () => {
    assert.ok(!idsFor({ listing: listing({ postedAt: NOW - 26 * 3_600_000 }) }).includes('just_listed'));
  });

  it('flags an item that sells briskly on eBay', () => {
    assert.ok(idsFor({ valuation: valuation({ soldPerWeek: 12 }) }).includes('hot'));
    assert.ok(!idsFor({ valuation: valuation({ soldPerWeek: 0.2 }) }).includes('hot'));
  });

  it('flags a very cheap ask relative to resale', () => {
    assert.ok(idsFor({ listing: listing({ askPrice: 40 }) }).includes('steal'));
  });

  it('flags distance in both directions', () => {
    assert.ok(idsFor({ distanceMi: 2 }).includes('very_close'));
    assert.ok(idsFor({ distanceMi: 28 }).includes('far'));
  });

  it('flags a price cut', () => {
    assert.ok(idsFor({ previousAskPrice: 200 }).includes('price_drop'));
    assert.ok(!idsFor({ previousAskPrice: 100 }).includes('price_drop'));
  });

  it('warns about a thin comp set', () => {
    assert.ok(idsFor({ valuation: valuation({ compCount: 2 }) }).includes('thin_comps'));
  });

  it('warns when the whole estimate is shaky', () => {
    const r = repair({ isBroken: true, partsCost: 200, confidence: 0.1, rulesApplied: ['x'] });
    assert.ok(idsFor({ valuation: valuation({ confidence: 0.3 }), repair: r }).includes('low_confidence'));
  });

  it('says an item needs repair rather than parts when it is fixable', () => {
    const ids = idsFor({
      repair: repair({ isBroken: true, partsCost: 18, rulesApplied: ['gpu-no-display'] }),
    });
    assert.ok(ids.includes('broken'));
    assert.ok(!ids.includes('parts_only'));
  });
});

describe('computeScore', () => {
  it('is zero when there is no profit', () => {
    const i = input({ listing: listing({ askPrice: 5000 }) });
    assert.equal(computeScore(i, computeDealMath(i)), 0);
  });

  it('rewards more profit', () => {
    const cheap = input({ listing: listing({ askPrice: 250 }) });
    const cheaper = input({ listing: listing({ askPrice: 60 }) });
    assert.ok(
      computeScore(cheaper, computeDealMath(cheaper)) >
        computeScore(cheap, computeDealMath(cheap)),
    );
  });

  it('discounts an uncertain estimate against a confident one', () => {
    const sure = input();
    const unsure = input({ valuation: valuation({ confidence: 0.2 }) });
    assert.ok(computeScore(unsure, computeDealMath(unsure)) < computeScore(sure, computeDealMath(sure)));
  });

  it('decays with the age of the listing', () => {
    const fresh = input();
    const stale = input({ listing: listing({ postedAt: NOW - 48 * 3_600_000 }) });
    assert.ok(computeScore(stale, computeDealMath(stale)) < computeScore(fresh, computeDealMath(fresh)));
  });

  it('prefers a near item to an identical far one', () => {
    const near = input({ distanceMi: 3 });
    const far = input({ distanceMi: 90 });
    assert.ok(computeScore(far, computeDealMath(far)) < computeScore(near, computeDealMath(near)));
  });

  it('stays inside 0..100', () => {
    const i = input({ listing: listing({ askPrice: 1 }), valuation: valuation({ resaleValue: 90_000 }) });
    const s = computeScore(i, computeDealMath(i));
    assert.ok(s >= 0 && s <= 100, String(s));
  });
});

describe('meetsThresholds', () => {
  const verdict = (over: Partial<ScoreInput> = {}) => {
    const i = input(over);
    return meetsThresholds(i, computeDealMath(i));
  };

  it('passes a solid deal', () => {
    assert.equal(verdict().pass, true);
  });

  it('rejects a listing with no comps at all', () => {
    assert.equal(verdict({ valuation: valuation({ compSource: 'none', compCount: 0 }) }).pass, false);
  });

  it('rejects a profit below the floor', () => {
    assert.equal(verdict({ profile: profile({ minProfit: 1000 }) }).pass, false);
  });

  it('rejects an ROI below the floor', () => {
    assert.equal(verdict({ profile: profile({ minRoi: 10 }) }).pass, false);
  });

  it('rejects an estimate below the confidence floor', () => {
    assert.equal(
      verdict({ valuation: valuation({ confidence: 0.2 }), profile: profile({ minConfidence: 0.6 }) })
        .pass,
      false,
    );
  });

  it('honours a profile that excludes broken items', () => {
    const r = repair({ isBroken: true, partsCost: 10, rulesApplied: ['x'] });
    assert.equal(verdict({ repair: r, profile: profile({ includeBroken: false }) }).pass, false);
    assert.equal(verdict({ repair: r, profile: profile({ includeBroken: true }) }).pass, true);
  });

  it('explains itself when it rejects', () => {
    const v = verdict({ profile: profile({ minProfit: 1000 }) });
    assert.ok(typeof v.reason === 'string' && v.reason.length > 0);
  });
});
