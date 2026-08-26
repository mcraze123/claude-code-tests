import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildCompQuery,
  containsAnyPositive,
  containsPhrasePositive,
  modelTokens,
  titleSimilarity,
} from './text.js';

describe('text', () => {
  it('keeps model tokens and drops classified-ad noise', () => {
    const q = buildCompQuery('**MUST GO TODAY** EVGA RTX 3080 10GB obo cash only');
    assert.ok(q.includes('3080'), q);
    assert.ok(q.includes('evga'), q);
    assert.ok(!q.includes('obo'), q);
    assert.ok(!q.includes('today'), q);
  });

  it('does not treat sizes or years as model numbers', () => {
    assert.deepEqual(modelTokens('2009 Honda Civic 4 door'), []);
    assert.deepEqual(modelTokens('RTX 3080'), ['3080']);
  });

  it('scores a same-model comp far above a generic one', () => {
    const listing = 'EVGA RTX 3080 10GB graphics card';
    const same = titleSimilarity(listing, 'EVGA GeForce RTX 3080 10GB XC3');
    const generic = titleSimilarity(listing, 'Gaming graphics card GPU');
    assert.ok(same > 0.6, `same-model similarity was ${same}`);
    assert.ok(same > generic * 2, `same ${same} vs generic ${generic}`);
  });

  it('treats a different model as a weak match', () => {
    const s = titleSimilarity('RTX 3080 10GB', 'RTX 4090 24GB graphics card');
    assert.ok(s < 0.35, `expected a weak match, got ${s}`);
  });

  describe('negation', () => {
    it('ignores a negated symptom', () => {
      assert.equal(containsPhrasePositive('no artifacts before it died', 'artifacts'), false);
    });

    it('keeps a symptom that is itself a negation', () => {
      assert.equal(containsPhrasePositive('card gives no display at all', 'no display'), true);
    });

    it('still matches a positive occurrence elsewhere in the text', () => {
      assert.equal(
        containsPhrasePositive('no artifacts at first, now artifacts everywhere', 'artifacts'),
        true,
      );
    });

    it('filters a list', () => {
      assert.deepEqual(
        containsAnyPositive('no water damage, cracked screen', ['water damage', 'cracked screen']),
        ['cracked screen'],
      );
    });
  });
});
