import type { RawListing, Section } from '../types.js';
import { fetchJson } from '../util/http.js';
import type { SearchRequest, SourceAdapter } from './types.js';

/**
 * Adapter for marketplaces that publish no usable public API.
 *
 * Rather than shipping a scraper for a site whose terms forbid it, this
 * adapter calls an HTTP endpoint *you* configure and that *you* are
 * responsible for - a licensed data provider, or your own service. It has to
 * answer with the small normalised shape documented in docs/SOURCES.md, and
 * it stays dormant until you set the URL.
 */
export function providerAdapter(opts: {
  id: string;
  label: string;
  docsAnchor: string;
  url: string;
  key: string;
}): SourceAdapter {
  return {
    id: opts.id,
    label: opts.label,
    docsAnchor: opts.docsAnchor,

    isAvailable(): boolean {
      return opts.url.trim().length > 0;
    },

    unavailableReason(): string | null {
      return this.isAvailable()
        ? null
        : `${opts.label} has no public API. Point ${opts.id.toUpperCase()}_PROVIDER_URL at a source you are licensed to use — see docs/SOURCES.md.`;
    },

    async search(req: SearchRequest): Promise<RawListing[]> {
      const url = new URL(opts.url);
      url.searchParams.set('q', req.query);
      url.searchParams.set('section', req.section);
      url.searchParams.set('limit', String(req.limit));
      url.searchParams.set('radius_mi', String(req.radiusMi));
      if (req.lat != null) url.searchParams.set('lat', String(req.lat));
      if (req.lon != null) url.searchParams.set('lon', String(req.lon));
      if (req.minPrice != null) url.searchParams.set('min_price', String(req.minPrice));
      if (req.maxPrice != null) url.searchParams.set('max_price', String(req.maxPrice));

      const headers: Record<string, string> = {};
      if (opts.key) headers.Authorization = `Bearer ${opts.key}`;

      const body = await fetchJson<unknown>(url.toString(), { headers, timeoutMs: 25_000 });
      const items = Array.isArray(body)
        ? body
        : Array.isArray((body as { listings?: unknown[] })?.listings)
          ? (body as { listings: unknown[] }).listings
          : [];

      return items
        .map((raw) => normalize(raw, opts.id, req.section))
        .filter((l): l is RawListing => l !== null);
    },
  };
}

/** Defensive normalisation: a third-party feed is not trusted to be well-formed. */
function normalize(raw: unknown, sourceId: string, _section: Section): RawListing | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;

  const url = str(r.url ?? r.link);
  const title = str(r.title ?? r.name);
  const externalId = str(r.id ?? r.external_id ?? r.listing_id) || hashOf(url);
  if (!url || !title || !externalId) return null;

  const postedRaw = r.posted_at ?? r.postedAt ?? r.created_at;
  const postedAt =
    typeof postedRaw === 'number'
      ? postedRaw < 1e12
        ? postedRaw * 1000 // seconds, not millis
        : postedRaw
      : typeof postedRaw === 'string'
        ? Date.parse(postedRaw) || Date.now()
        : Date.now();

  return {
    sourceId,
    externalId,
    url,
    title,
    description: str(r.description ?? r.body) || null,
    askPrice: numOrNull(r.price ?? r.ask_price ?? r.amount),
    currency: str(r.currency) || 'USD',
    imageUrl: str(r.image ?? r.image_url ?? r.photo) || null,
    postedAt,
    lat: numOrNull(r.lat ?? r.latitude),
    lon: numOrNull(r.lon ?? r.lng ?? r.longitude),
    locationName: str(r.location ?? r.location_name ?? r.city) || null,
    attributes: isRecord(r.attributes) ? (r.attributes as Record<string, string>) : {},
  };
}

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : '');

const numOrNull = (v: unknown): number | null => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string') {
    const n = Number(v.replace(/[^0-9.]/g, ''));
    return Number.isFinite(n) ? n : null;
  }
  return null;
};

const isRecord = (v: unknown): v is Record<string, unknown> =>
  Boolean(v) && typeof v === 'object' && !Array.isArray(v);

/** Stable fallback id when a provider omits one. */
function hashOf(s: string): string {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(36);
}
