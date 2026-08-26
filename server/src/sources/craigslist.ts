import { XMLParser } from 'fast-xml-parser';
import { config } from '../config.js';
import type { RawListing, Section } from '../types.js';
import { PoliteQueue, fetchText } from '../util/http.js';
import type { SearchRequest, SourceAdapter } from './types.js';

/**
 * Craigslist search pages expose an RSS feed via `format=rss`. That feed is
 * the only interface Craigslist offers, it is unauthenticated, and it is what
 * this adapter uses. Craigslist rate-limits aggressively, so every request
 * goes through a queue with a hard gap between calls.
 */
const queue = new PoliteQueue(2_500);

/** Craigslist category codes per section. */
const CATEGORIES: Record<Section, string[]> = {
  // electronics, computers, video gaming, general for-sale
  electronics: ['ela', 'sya', 'vga'],
  appliances: ['app'],
  // cars & trucks, by owner: dealers rarely leave margin on the table
  vehicles: ['cto'],
};

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: '@_',
  removeNSPrefix: true,
});

interface RssItem {
  title?: string | { '#text'?: string };
  link?: string;
  description?: string;
  date?: string;
  about?: string;
  '@_about'?: string;
  enclosure?: { '@_resource'?: string } | { '@_resource'?: string }[];
  point?: string;
  lat?: string | number;
  long?: string | number;
}

export const craigslist: SourceAdapter = {
  id: 'craigslist',
  label: 'Craigslist',
  docsAnchor: 'craigslist',

  isAvailable(): boolean {
    return config.sources.craigslistSites.length > 0;
  },

  unavailableReason(): string | null {
    return this.isAvailable() ? null : 'CRAIGSLIST_SITES is empty in .env';
  },

  async search(req: SearchRequest): Promise<RawListing[]> {
    const out: RawListing[] = [];
    const seen = new Set<string>();

    for (const site of config.sources.craigslistSites) {
      for (const category of CATEGORIES[req.section]) {
        const url = buildUrl(site, category, req);
        const xml = await queue.run(() => fetchText(url, { timeoutMs: 20_000 }));
        assertLooksLikeRss(xml, url);
        for (const listing of parseFeed(xml, site)) {
          if (seen.has(listing.externalId)) continue;
          seen.add(listing.externalId);
          out.push(listing);
          if (out.length >= req.limit) return out;
        }
      }
    }
    return out;
  },
};

/**
 * Craigslist sometimes answers a `format=rss` URL with the regular HTML search
 * page - for a category that has no feed, or when it decides to challenge the
 * client. Silently parsing that to zero listings looks identical to "nothing
 * is for sale today", so say what actually happened instead.
 */
function assertLooksLikeRss(body: string, url: string): void {
  const head = body.slice(0, 400).toLowerCase();
  if (head.includes('<rdf:rdf') || head.includes('<rss') || head.includes('<feed')) return;

  const host = new URL(url).host;
  if (head.includes('<!doctype html') || head.includes('<html')) {
    throw new Error(
      `${host} returned HTML instead of RSS. That category may no longer publish a feed, ` +
        `or the request was challenged. Try a different CRAIGSLIST_SITES value or open the URL in a browser: ${url}`,
    );
  }
  throw new Error(`${host} returned an unrecognised response for ${url}`);
}

function buildUrl(site: string, category: string, req: SearchRequest): string {
  const params = new URLSearchParams({ format: 'rss', sort: 'date' });
  if (req.query) params.set('query', req.query);
  if (req.minPrice != null) params.set('min_price', String(Math.floor(req.minPrice)));
  if (req.maxPrice != null) params.set('max_price', String(Math.ceil(req.maxPrice)));
  if (req.lat != null && req.lon != null) {
    params.set('lat', req.lat.toFixed(5));
    params.set('lon', req.lon.toFixed(5));
    params.set('search_distance', String(Math.round(req.radiusMi)));
  }
  return `https://${site}.craigslist.org/search/${category}?${params}`;
}

export function parseFeed(xml: string, site: string): RawListing[] {
  const doc = parser.parse(xml) as Record<string, unknown>;
  const root = (doc['RDF'] ?? doc['rss'] ?? doc) as Record<string, unknown>;
  const channel = (root['channel'] ?? root) as Record<string, unknown>;
  const rawItems = (root['item'] ?? channel['item'] ?? []) as RssItem | RssItem[];
  const items = Array.isArray(rawItems) ? rawItems : rawItems ? [rawItems] : [];

  const listings: RawListing[] = [];
  for (const item of items) {
    const title = textOf(item.title);
    const link = item.link ?? item['@_about'] ?? item.about;
    if (!title || typeof link !== 'string') continue;

    const externalId = postIdFrom(link);
    if (!externalId) continue;

    const { lat, lon } = coordsOf(item);
    listings.push({
      sourceId: 'craigslist',
      externalId,
      url: link,
      title: stripPriceSuffix(title),
      description: typeof item.description === 'string' ? decode(item.description) : null,
      askPrice: priceFrom(title) ?? priceFrom(String(item.description ?? '')),
      currency: 'USD',
      imageUrl: imageOf(item),
      postedAt: item.date ? Date.parse(item.date) || Date.now() : Date.now(),
      lat,
      lon,
      locationName: locationFrom(title) ?? site,
      attributes: { site },
    });
  }
  return listings;
}

function textOf(v: unknown): string {
  if (typeof v === 'string') return decode(v);
  if (v && typeof v === 'object' && '#text' in v) return decode(String((v as never)['#text']));
  return '';
}

/** Craigslist post ids are the trailing numeric segment of the URL. */
function postIdFrom(url: string): string | null {
  const m = url.match(/\/(\d{8,})\.html/);
  return m ? m[1]! : null;
}

/** "Sony receiver - $120 (Oakland)" -> 120 */
export function priceFrom(text: string): number | null {
  const m = text.match(/\$\s?([\d,]+(?:\.\d{2})?)/);
  if (!m) return null;
  const n = Number(m[1]!.replace(/,/g, ''));
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** "Sony receiver - $120 (Oakland)" -> "Oakland" */
export function locationFrom(text: string): string | null {
  const m = text.match(/\(([^()]+)\)\s*$/);
  return m ? m[1]!.trim() : null;
}

/** Drop the " - $120 (Oakland)" tail so comp queries are not polluted. */
export function stripPriceSuffix(title: string): string {
  return title
    .replace(/\s*-\s*\$[\d,]+(?:\.\d{2})?\s*(\([^()]*\))?\s*$/, '')
    .replace(/\s*\([^()]*\)\s*$/, '')
    .trim();
}

function imageOf(item: RssItem): string | null {
  const enc = item.enclosure;
  const first = Array.isArray(enc) ? enc[0] : enc;
  const url = first?.['@_resource'];
  if (!url) return null;
  // Craigslist serves 300x300 thumbs in RSS; 600x450 exists at the same path.
  return url.replace('_300x300', '_600x450');
}

function coordsOf(item: RssItem): { lat: number | null; lon: number | null } {
  if (typeof item.point === 'string') {
    const [a, b] = item.point.trim().split(/\s+/).map(Number);
    if (Number.isFinite(a) && Number.isFinite(b)) return { lat: a!, lon: b! };
  }
  const lat = Number(item.lat);
  const lon = Number(item.long);
  if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat, lon };
  return { lat: null, lon: null };
}

function decode(s: string): string {
  return s
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#(\d+);/g, (_, d: string) => String.fromCharCode(Number(d)))
    .replace(/&#x([0-9a-f]+);/gi, (_, h: string) => String.fromCharCode(parseInt(h, 16)))
    .replace(/\s+/g, ' ')
    .trim();
}
