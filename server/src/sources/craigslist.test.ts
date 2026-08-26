import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { locationFrom, parseFeed, priceFrom, stripPriceSuffix } from './craigslist.js';

const FEED = `<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:enc="http://purl.oclc.org/net/rss_2.0/enc#"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
  <item rdf:about="https://sfbay.craigslist.org/eby/ele/d/thing/7712345678.html">
    <title>EVGA RTX 3080 &amp;quot;no display&amp;quot; - $120 (Fremont)</title>
    <link>https://sfbay.craigslist.org/eby/ele/d/thing/7712345678.html</link>
    <description>&lt;p&gt;Card powers on but &lt;b&gt;no display&lt;/b&gt;. $120 firm.&lt;/p&gt;</description>
    <dc:date>2026-01-15T09:30:00-08:00</dc:date>
    <enc:enclosure resource="https://images.craigslist.org/abc_300x300.jpg" type="image/jpeg"/>
    <geo:lat>37.5485</geo:lat>
    <geo:long>-121.9886</geo:long>
  </item>
  <item rdf:about="https://sfbay.craigslist.org/eby/ele/d/other/7799999999.html">
    <title>Free broken TV (Hayward)</title>
    <link>https://sfbay.craigslist.org/eby/ele/d/other/7799999999.html</link>
    <description>Cracked screen, take it away</description>
    <dc:date>2026-01-15T10:00:00-08:00</dc:date>
  </item>
  <item rdf:about="https://sfbay.craigslist.org/search/ele">
    <title>Not a post, no id in the url</title>
    <link>https://sfbay.craigslist.org/search/ele</link>
  </item>
</rdf:RDF>`;

describe('craigslist feed parsing', () => {
  const listings = parseFeed(FEED, 'sfbay');

  it('skips entries that are not posts', () => {
    assert.equal(listings.length, 2);
  });

  it('takes the post id from the url', () => {
    assert.equal(listings[0]?.externalId, '7712345678');
  });

  it('pulls the price out of the title', () => {
    assert.equal(listings[0]?.askPrice, 120);
  });

  it('leaves the price null when the seller gave none', () => {
    assert.equal(listings[1]?.askPrice, null);
  });

  it('strips the price and location tail from the title', () => {
    assert.equal(listings[0]?.title, 'EVGA RTX 3080 "no display"');
  });

  it('decodes entities and strips html from the body', () => {
    assert.equal(listings[0]?.description, 'Card powers on but no display . $120 firm.');
  });

  it('reads coordinates when the post has them', () => {
    assert.equal(listings[0]?.lat, 37.5485);
    assert.equal(listings[0]?.lon, -121.9886);
  });

  it('leaves coordinates null when the post has none', () => {
    assert.equal(listings[1]?.lat, null);
  });

  it('upgrades the thumbnail to the larger image', () => {
    assert.ok(listings[0]?.imageUrl?.includes('600x450'));
  });

  it('keeps the posting time, not the fetch time', () => {
    assert.equal(listings[0]?.postedAt, Date.parse('2026-01-15T09:30:00-08:00'));
  });

  it('survives a feed with no items', () => {
    assert.deepEqual(parseFeed('<rdf:RDF></rdf:RDF>', 'sfbay'), []);
  });
});

describe('title helpers', () => {
  it('reads prices with separators', () => {
    assert.equal(priceFrom('Truck - $12,500 (Tracy)'), 12500);
    assert.equal(priceFrom('no price here'), null);
  });

  it('reads the trailing location', () => {
    assert.equal(locationFrom('Amp - $50 (San Leandro)'), 'San Leandro');
    assert.equal(locationFrom('Amp - $50'), null);
  });

  it('leaves a clean title untouched', () => {
    assert.equal(stripPriceSuffix('Marantz 2270 receiver'), 'Marantz 2270 receiver');
  });
});

describe('feed sanity', () => {
  it('recognises a real feed', () => {
    assert.equal(parseFeed(FEED, 'sfbay').length, 2);
  });

  it('parses an empty but valid feed to nothing', () => {
    assert.deepEqual(parseFeed('<?xml version="1.0"?><rdf:RDF></rdf:RDF>', 'sfbay'), []);
  });
});
