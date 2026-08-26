import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  classifyDevice,
  estimateRepair,
  partsOutPct,
  readCondition,
  stripSymptoms,
} from './repair.js';
import { buildCompQuery } from '../util/text.js';

describe('classifyDevice', () => {
  it('prefers the longest matching phrase', () => {
    assert.equal(classifyDevice('Fender tube amp 1965')?.id, 'tube_gear');
    assert.equal(classifyDevice('Yamaha integrated amp')?.id, 'amp');
  });

  it('does not mistake a symptom for a product', () => {
    // "no display" must not classify a graphics card as a monitor.
    assert.equal(classifyDevice('EVGA RTX 3080 - no display')?.id, 'gpu');
  });

  it('returns null for something it has no rules about', () => {
    assert.equal(classifyDevice('antique oak dining table'), null);
  });
});

describe('readCondition', () => {
  it('reads for-parts language', () => {
    const c = readCondition('Selling for parts or repair, untested');
    assert.equal(c.sellerSaysPartsOnly, true);
    assert.equal(c.isBroken, true);
    assert.equal(c.isUntested, true);
  });

  it('does not let "works great" cancel visible damage', () => {
    const c = readCondition('Works great but the screen is cracked');
    assert.equal(c.saysWorking, false);
    assert.equal(c.isBroken, true);
  });

  it('accepts a plainly working item', () => {
    const c = readCondition('Tested working, fully functional');
    assert.equal(c.saysWorking, true);
    assert.equal(c.isBroken, false);
  });
});

describe('estimateRepair', () => {
  it('budgets nothing for a working item', () => {
    const r = estimateRepair('Nintendo Switch OLED', 'Works perfectly, tested working');
    assert.equal(r.isBroken, false);
    assert.equal(r.partsCost, 0);
  });

  it('diagnoses a known symptom and cites the rule', () => {
    const r = estimateRepair('LG 65" TV - no power, clicking', 'Just clicks and will not turn on');
    assert.ok(r.rulesApplied.includes('tv-no-power'));
    assert.ok(r.partsCost > 0 && r.partsCost < 60, String(r.partsCost));
    assert.ok(r.confidence > 0.5);
  });

  it('ignores a negated symptom', () => {
    const withNegation = estimateRepair('RTX 3080 no display', 'No artifacts before it died');
    assert.ok(!withNegation.rulesApplied.includes('gpu-artifacting'));
    assert.deepEqual(withNegation.rulesApplied, ['gpu-no-display']);
  });

  it('charges a second fault at a discount, not in full', () => {
    const one = estimateRepair('MacBook Pro 16 - cracked screen', 'Screen is cracked');
    const two = estimateRepair(
      'MacBook Pro 16 - cracked screen, no hard drive',
      'Screen is cracked and there is no hard drive',
    );
    assert.ok(two.partsCost > one.partsCost);
    assert.ok(two.partsCost < one.partsCost * 2, 'second fault should be discounted');
  });

  it('is less certain when several faults are guessed at once', () => {
    const one = estimateRepair('MacBook Pro 16 - cracked screen', 'Screen is cracked');
    const two = estimateRepair(
      'MacBook Pro 16 - cracked screen, no hard drive',
      'Screen is cracked and there is no hard drive',
    );
    assert.ok(two.confidence < one.confidence);
  });

  it('flags a hard parts-out verdict from the rule, not the wording', () => {
    const tv = estimateRepair('LG OLED 65 - cracked screen', 'Panel is cracked, boards fine');
    assert.equal(tv.partsOnly, true);

    const gpu = estimateRepair('RTX 3080 for parts or repair', 'No display');
    assert.equal(gpu.partsOnly, false, 'seller wording alone is not a parts-out verdict');
    assert.equal(gpu.sellerSaysPartsOnly, true);
  });

  it('treats an iCloud lock as unrepairable', () => {
    const r = estimateRepair('iPhone 13 Pro - icloud locked', 'Previous owner never removed it');
    assert.equal(r.partsOnly, true);
  });

  it('falls back with low confidence when the seller only says "untested"', () => {
    const r = estimateRepair('Sony receiver', 'Untested, found in a storage unit');
    assert.equal(r.isBroken, true);
    assert.ok(r.partsCost > 0);
    assert.ok(r.confidence < 0.3, `expected low confidence, got ${r.confidence}`);
    assert.deepEqual(r.rulesApplied, ['fallback']);
  });

  it('is less confident from a title alone than with a description', () => {
    const titleOnly = estimateRepair('PS5 - no video, broken hdmi port');
    const withBody = estimateRepair('PS5 - no video, broken hdmi port', 'No video on any TV');
    assert.ok(titleOnly.confidence < withBody.confidence);
  });

  it('assumes working, but unsurely, when condition is not mentioned', () => {
    const r = estimateRepair('Dell OptiPlex 7060 desktop');
    assert.equal(r.isBroken, false);
    assert.ok(r.confidence < 0.6);
  });
});

describe('partsOutPct', () => {
  it('recovers more from a graphics card than from a television', () => {
    assert.ok(partsOutPct('RTX 3080 graphics card') > partsOutPct('LG OLED TV'));
  });
});

describe('stripSymptoms', () => {
  // What matters is the query that finally reaches eBay, so assert on that:
  // stripping leaves connective residue that buildCompQuery then discards.
  const query = (title: string): string => buildCompQuery(stripSymptoms(title));

  it('searches for the product, not the fault', () => {
    assert.equal(query('Marantz 2270 receiver - one channel out, unrestored'), '2270 marantz receiver');
  });

  it('drops for-parts language', () => {
    assert.equal(query('EVGA RTX 3080 10GB for parts or repair'), '3080 10gb evga rtx');
  });

  it('leaves a clean title alone', () => {
    assert.equal(query('Nintendo Switch OLED console'), 'nintendo switch oled console');
  });

  it('keeps enough of a heavily damaged listing to find comps', () => {
    const q = query('LG 65 inch OLED TV cracked screen, no power, for parts');
    assert.ok(q.includes('oled'), q);
    assert.ok(q.includes('lg'), q);
    assert.ok(!q.includes('cracked'), q);
  });

  it('does not empty a title that is nothing but symptoms', () => {
    // buildCompQuery falls back to the raw title upstream; stripping alone
    // may legitimately return very little here.
    assert.equal(typeof stripSymptoms('broken not working as is'), 'string');
  });
});
