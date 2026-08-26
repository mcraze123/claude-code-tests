import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { median, percentile, summarizeComps, trimOutliers, type Comp } from './comps.js';

const comp = (title: string, price: number): Comp => ({ title, price });

describe('statistics', () => {
  it('takes the median of an even-length set', () => {
    assert.equal(median([10, 20, 30, 40]), 25);
  });

  it('interpolates percentiles', () => {
    assert.equal(percentile([0, 10, 20, 30, 40], 0.5), 20);
  });

  it('drops a lot listing far outside the fence', () => {
    const kept = trimOutliers([300, 310, 320, 330, 340, 5000]);
    assert.ok(!kept.includes(5000));
    assert.equal(kept.length, 5);
  });

  it('leaves tiny samples alone rather than emptying them', () => {
    assert.deepEqual(trimOutliers([100, 900]), [100, 900]);
  });
});

describe('summarizeComps', () => {
  const title = 'EVGA RTX 3080 10GB graphics card';
  const good: Comp[] = [
    comp('EVGA RTX 3080 10GB FTW3', 340),
    comp('NVIDIA RTX 3080 10GB Founders', 355),
    comp('MSI RTX 3080 10GB Ventus', 330),
    comp('ASUS RTX 3080 10GB TUF', 349),
    comp('RTX 3080 10GB graphics card', 344),
    comp('EVGA RTX 3080 10GB XC3', 325),
  ];

  it('reports no valuation without comps', () => {
    const v = summarizeComps(title, [], { compSource: 'ebay_active', matchQuery: 'x' });
    assert.equal(v.compSource, 'none');
    assert.equal(v.confidence, 0);
  });

  it('discounts active asking prices toward realised prices', () => {
    const active = summarizeComps(title, good, { compSource: 'ebay_active', matchQuery: 'q' });
    const sold = summarizeComps(title, good, { compSource: 'ebay_sold', matchQuery: 'q' });
    assert.ok(active.resaleValue < sold.resaleValue);
    assert.ok(active.confidence < sold.confidence, 'sold data should be more trusted');
  });

  it('is more confident with more agreeing comps', () => {
    const few = summarizeComps(title, good.slice(0, 2), {
      compSource: 'ebay_sold',
      matchQuery: 'q',
    });
    const many = summarizeComps(title, good, { compSource: 'ebay_sold', matchQuery: 'q' });
    assert.ok(many.confidence > few.confidence);
  });

  it('is less confident when the comps disagree wildly', () => {
    const scattered: Comp[] = [
      comp('EVGA RTX 3080 10GB', 120),
      comp('NVIDIA RTX 3080 10GB', 340),
      comp('MSI RTX 3080 10GB', 500),
      comp('ASUS RTX 3080 10GB', 700),
      comp('RTX 3080 10GB card', 260),
      comp('EVGA RTX 3080 10GB used', 610),
    ];
    const tight = summarizeComps(title, good, { compSource: 'ebay_sold', matchQuery: 'q' });
    const loose = summarizeComps(title, scattered, { compSource: 'ebay_sold', matchQuery: 'q' });
    assert.ok(loose.confidence < tight.confidence);
  });

  it('is less confident when the comps are a different product', () => {
    const wrong: Comp[] = [
      comp('RTX 4090 24GB', 1600),
      comp('GTX 1060 6GB', 90),
      comp('Radeon RX 580', 70),
      comp('Generic gaming graphics card', 200),
    ];
    const right = summarizeComps(title, good, { compSource: 'ebay_sold', matchQuery: 'q' });
    const off = summarizeComps(title, wrong, { compSource: 'ebay_sold', matchQuery: 'q' });
    assert.ok(off.confidence < right.confidence);
  });

  it('reports a quartile range around the value', () => {
    const v = summarizeComps(title, good, { compSource: 'ebay_sold', matchQuery: 'q' });
    assert.ok(v.resaleLow <= v.resaleValue && v.resaleValue <= v.resaleHigh);
  });

  it('ignores free and malformed prices', () => {
    const v = summarizeComps(title, [...good, comp('RTX 3080 free', 0)], {
      compSource: 'ebay_sold',
      matchQuery: 'q',
    });
    assert.equal(v.compCount, good.length);
  });
});
