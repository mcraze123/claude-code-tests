import { createRequire } from 'node:module';
import type { RawListing, Section } from '../types.js';
import type { Comp } from '../valuation/comps.js';
import { contentTokens } from '../util/text.js';
import type { SearchRequest, SourceAdapter } from './types.js';

const require = createRequire(import.meta.url);
const file = require('../../data/sample-listings.json') as SampleFile;

interface SampleListing {
  externalId: string;
  section: Section;
  title: string;
  description: string;
  askPrice: number | null;
  minutesAgo: number;
  offsetMi: number;
  bearingDeg: number;
  locationName: string;
  comps: Comp[];
}
interface SampleFile {
  listings: SampleListing[];
}

const byId = new Map(file.listings.map((l) => [l.externalId, l]));

/**
 * Offline fixture source. It makes the whole pipeline demonstrable - scoring,
 * badges, distance, push - before any credentials exist, and gives the tests
 * a stable corpus. Fixtures carry their own comps, so no network call is made
 * for them (see analyzer.ts).
 */
export const sample: SourceAdapter = {
  id: 'sample',
  label: 'Sample data (offline demo)',
  docsAnchor: 'sample',

  isAvailable(): boolean {
    return true;
  },

  unavailableReason(): null {
    return null;
  },

  async search(req: SearchRequest): Promise<RawListing[]> {
    const terms = contentTokens(req.query);
    const now = Date.now();

    return file.listings
      .filter((l) => l.section === req.section)
      .filter((l) => {
        if (terms.length === 0) return true;
        const hay = `${l.title} ${l.description}`.toLowerCase();
        return terms.some((t) => hay.includes(t));
      })
      .filter((l) => {
        if (req.minPrice != null && (l.askPrice ?? 0) < req.minPrice) return false;
        if (req.maxPrice != null && (l.askPrice ?? 0) > req.maxPrice) return false;
        return l.offsetMi <= req.radiusMi;
      })
      .slice(0, req.limit)
      .map((l) => {
        const origin =
          req.lat != null && req.lon != null ? { lat: req.lat, lon: req.lon } : null;
        const point = origin ? offset(origin, l.offsetMi, l.bearingDeg) : null;
        return {
          sourceId: 'sample',
          externalId: l.externalId,
          url: `https://example.invalid/sample/${l.externalId}`,
          title: l.title,
          description: l.description,
          askPrice: l.askPrice,
          currency: 'USD',
          imageUrl: null,
          // Fixtures are always "just posted" relative to now, so freshness
          // and the just-listed badge behave the way they will in production.
          postedAt: now - l.minutesAgo * 60_000,
          lat: point?.lat ?? null,
          lon: point?.lon ?? null,
          locationName: l.locationName,
          attributes: { fixture: true },
        } satisfies RawListing;
      });
  },
};

/** Comps bundled with a fixture, so demo mode needs no eBay keys. */
export function sampleComps(externalId: string): Comp[] {
  return byId.get(externalId)?.comps ?? [];
}

/** Project a point `miles` away from origin along a compass bearing. */
function offset(
  origin: { lat: number; lon: number },
  miles: number,
  bearingDeg: number,
): { lat: number; lon: number } {
  const R = 3958.8;
  const brg = (bearingDeg * Math.PI) / 180;
  const lat1 = (origin.lat * Math.PI) / 180;
  const lon1 = (origin.lon * Math.PI) / 180;
  const d = miles / R;

  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(brg),
  );
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(brg) * Math.sin(d) * Math.cos(lat1),
      Math.cos(d) - Math.sin(lat1) * Math.sin(lat2),
    );

  return { lat: (lat2 * 180) / Math.PI, lon: (lon2 * 180) / Math.PI };
}
