import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { ScoredDeal } from '../types.js';
import { formatNotification, truncate } from './format.js';
import { inQuietHours } from './index.js';

const deal = (over: Partial<ScoredDeal> = {}): ScoredDeal =>
  ({
    key: 'craigslist:1',
    section: 'electronics',
    profileId: 1,
    listing: {
      sourceId: 'craigslist',
      externalId: '1',
      url: 'https://example.invalid/1',
      title: 'EVGA RTX 3080 10GB - no display',
      description: null,
      askPrice: 120,
      currency: 'USD',
      imageUrl: 'https://example.invalid/1.jpg',
      postedAt: Date.now() - 60_000,
      lat: 37.7,
      lon: -122.1,
      locationName: 'Fremont',
    },
    valuation: {
      resaleValue: 342,
      resaleLow: 328,
      resaleHigh: 355,
      compCount: 12,
      compSource: 'ebay_sold',
      confidence: 0.8,
      matchQuery: 'evga rtx 3080',
      soldPerWeek: 9,
    },
    repair: {
      isBroken: true,
      partsCost: 18,
      partsLow: 3,
      partsHigh: 65,
      confidence: 0.55,
      symptoms: ['no display'],
      rulesApplied: ['gpu-no-display'],
      partsOnly: false,
      sellerSaysPartsOnly: true,
      notes: [],
    },
    math: {
      askPrice: 120,
      resaleValue: 342,
      expectedSalePrice: 342,
      marketplaceFees: 45.72,
      shippingCost: 12,
      suppliesCost: 3,
      partsCost: 18,
      travelCost: 3.59,
      netProfit: 139.69,
      roi: 0.99,
      marginPct: 0.41,
      breakEvenAsk: 199.69,
    },
    score: 50,
    badges: [
      { id: 'just_listed', label: 'Just listed', icon: '⚡', tone: 'good', detail: '1 min ago' },
      { id: 'hot', label: 'Hot item', icon: '🔥', tone: 'good', detail: '9/week' },
    ],
    adjustments: [],
    distanceMi: 4.1,
    firstSeenAt: Date.now(),
    lastSeenAt: Date.now(),
    notifiedAt: null,
    ...over,
  }) as ScoredDeal;

describe('formatNotification', () => {
  it('leads with the profit', () => {
    assert.ok(formatNotification(deal()).title.startsWith('⚡ $140 profit'));
  });

  it('includes ask, resale, parts, distance and confidence', () => {
    const body = formatNotification(deal()).body;
    for (const fragment of ['Ask $120', 'resale ~$342', 'parts $18', '4.1 mi', 'conf']) {
      assert.ok(body.includes(fragment), `missing "${fragment}" in: ${body}`);
    }
  });

  it('says so plainly when the seller gave no price', () => {
    const d = deal();
    d.listing.askPrice = null;
    assert.ok(formatNotification(d).body.includes('(no price)'));
  });

  it('carries the link and image through', () => {
    const p = formatNotification(deal());
    assert.equal(p.url, 'https://example.invalid/1');
    assert.equal(p.imageUrl, 'https://example.invalid/1.jpg');
  });

  it('raises priority for a high-scoring deal', () => {
    assert.ok(formatNotification(deal({ score: 85 })).priority > formatNotification(deal({ score: 20 })).priority);
  });

  it('abbreviates thousands', () => {
    const d = deal();
    d.math.netProfit = 2944.74;
    assert.ok(formatNotification(d).title.includes('$2.9k'));
  });
});

describe('truncate', () => {
  it('leaves short text alone', () => {
    assert.equal(truncate('short', 20), 'short');
  });

  it('ellipsises long text', () => {
    assert.equal(truncate('abcdefghij', 5), 'abcd…');
  });
});

describe('inQuietHours', () => {
  const settings = (start: number | null, end: number | null) => ({
    lat: null,
    lon: null,
    locationUpdatedAt: null,
    mileageCostPerMi: 0.35,
    ebayAdRatePct: 0,
    quietHoursStart: start,
    quietHoursEnd: end,
  });
  const at = (hour: number): Date => new Date(2026, 0, 15, hour, 0, 0);

  it('is off when unset', () => {
    assert.equal(inQuietHours(settings(null, null), at(3)), false);
  });

  it('handles a window that wraps midnight', () => {
    assert.equal(inQuietHours(settings(22, 7), at(23)), true);
    assert.equal(inQuietHours(settings(22, 7), at(3)), true);
    assert.equal(inQuietHours(settings(22, 7), at(12)), false);
  });

  it('handles a daytime window', () => {
    assert.equal(inQuietHours(settings(9, 17), at(12)), true);
    assert.equal(inQuietHours(settings(9, 17), at(20)), false);
  });

  it('treats an equal start and end as disabled', () => {
    assert.equal(inQuietHours(settings(8, 8), at(8)), false);
  });
});
