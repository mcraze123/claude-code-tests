import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { distanceMi, isPoint, travelCost } from './geo.js';

describe('geo', () => {
  it('measures a known distance', () => {
    // Oakland -> San Jose is about 40 miles.
    const d = distanceMi({ lat: 37.8044, lon: -122.2712 }, { lat: 37.3382, lon: -121.8863 });
    assert.ok(d > 36 && d < 44, `expected ~40mi, got ${d}`);
  });

  it('is zero for the same point', () => {
    assert.equal(distanceMi({ lat: 37.8, lon: -122.2 }, { lat: 37.8, lon: -122.2 }), 0);
  });

  it('charges a round trip with a road-distance fudge', () => {
    // 10 crow-flies miles -> 25 road miles round trip at $0.40 = $10.
    assert.equal(travelCost(10, 0.4), 10);
  });

  it('charges nothing when the location is unknown', () => {
    assert.equal(travelCost(null, 0.4), 0);
  });

  it('rejects null island and out-of-range coordinates', () => {
    assert.equal(isPoint(0, 0), false);
    assert.equal(isPoint(91, 10), false);
    assert.equal(isPoint(null, 10), false);
    assert.equal(isPoint(37.8, -122.2), true);
  });
});
