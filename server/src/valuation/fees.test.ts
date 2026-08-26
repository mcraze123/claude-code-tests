import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  estimateFulfillment,
  localHaircut,
  marketplaceFees,
  resaleHaircuts,
} from './fees.js';

describe('marketplaceFees', () => {
  it('charges the standard rate plus the per-order fee', () => {
    // 13.25% of $100 = $13.25, plus $0.40 per order.
    assert.equal(marketplaceFees(100, 0, 'electronics'), 13.65);
  });

  it('charges fees on shipping the buyer pays', () => {
    const noShip = marketplaceFees(100, 0, 'electronics');
    const withShip = marketplaceFees(100, 20, 'electronics');
    assert.ok(withShip > noShip);
    assert.equal(withShip, 16.3); // 13.25% of $120 + $0.40
  });

  it('uses the reduced fixed fee under ten dollars', () => {
    assert.equal(marketplaceFees(5, 0, 'electronics'), 0.96); // 0.6625 + 0.30
  });

  it('applies the reduced rate above the tier ceiling', () => {
    const fee = marketplaceFees(10_000, 0, 'electronics');
    // 13.25% on the first 7500, 2.35% on the remaining 2500, plus 0.40.
    assert.equal(fee, 993.75 + 58.75 + 0.4);
  });

  it('adds a promoted-listing ad rate on top', () => {
    const plain = marketplaceFees(200, 0, 'electronics');
    const promoted = marketplaceFees(200, 0, 'electronics', 0.05);
    assert.equal(Math.round((promoted - plain) * 100) / 100, 10);
  });

  it('charges nothing for a local cash appliance sale', () => {
    assert.equal(marketplaceFees(400, 0, 'appliances'), 0);
  });

  it('charges eBay Motors a flat successful-listing fee', () => {
    assert.equal(marketplaceFees(6000, 0, 'vehicles'), 125);
  });

  it('charges nothing on a zero sale', () => {
    assert.equal(marketplaceFees(0, 0, 'electronics'), 0);
  });
});

describe('estimateFulfillment', () => {
  it('prices a graphics card as a small parcel', () => {
    const f = estimateFulfillment('EVGA RTX 3080 10GB', 'electronics');
    assert.equal(f.localOnly, false);
    assert.ok(f.shippingCost > 0 && f.shippingCost < 20, String(f.shippingCost));
  });

  it('marks a television as local pickup only', () => {
    const f = estimateFulfillment('LG 65" OLED C1 TV', 'electronics');
    assert.equal(f.localOnly, true);
    assert.equal(f.shippingCost, 0);
  });

  it('prices a laptop above a phone', () => {
    const laptop = estimateFulfillment('MacBook Pro 16', 'electronics');
    const phone = estimateFulfillment('iPhone 13 Pro', 'electronics');
    assert.ok(laptop.shippingCost > phone.shippingCost);
  });

  it('never ships an appliance', () => {
    assert.equal(estimateFulfillment('Whirlpool dryer', 'appliances').localOnly, true);
  });
});

describe('localHaircut', () => {
  it('does not discount a shippable electronics item', () => {
    assert.equal(localHaircut('electronics', false), 0);
  });

  it('discounts electronics that can only sell locally', () => {
    assert.ok(localHaircut('electronics', true) > 0);
  });

  it('always discounts appliances, which sell locally for cash', () => {
    assert.ok(localHaircut('appliances', true) > 0);
  });
});

describe('resaleHaircuts', () => {
  it('leaves a clean listing alone', () => {
    const { factor, applied } = resaleHaircuts('2009 Honda Civic clean title runs great');
    assert.equal(factor, 1);
    assert.equal(applied.length, 0);
  });

  it('discounts a branded title', () => {
    const { factor, applied } = resaleHaircuts('2011 Ford F-150 rebuilt title');
    assert.equal(factor, 0.7);
    assert.equal(applied[0]?.id, 'branded-title');
  });

  it('compounds several conditions', () => {
    const { factor } = resaleHaircuts('Receiver, no remote, smoke smell from a smoker home');
    assert.ok(factor < 0.85, String(factor));
  });

  it('caps the total discount', () => {
    const text = 'salvage title no title high miles no remote scratched smoke smell';
    assert.ok(resaleHaircuts(text).factor >= 0.4);
  });
});
