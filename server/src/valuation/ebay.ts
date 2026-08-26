import { config } from '../config.js';
import type { Comp } from './comps.js';

const OAUTH_URL = 'https://api.ebay.com/identity/v1/oauth2/token';
const BROWSE_URL = 'https://api.ebay.com/buy/browse/v1/item_summary/search';
const INSIGHTS_URL =
  'https://api.ebay.com/buy/marketplace_insights/v1_beta/item_sales/search';

const SCOPE = 'https://api.ebay.com/oauth/api_scope';

/**
 * Comps for the same model do not move hour to hour, and the Browse API has a
 * daily call ceiling, so results are cached aggressively. A sweep of 40 fresh
 * listings that are all "RTX 3080" costs one call, not forty.
 */
const CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const MAX_CACHE_ENTRIES = 2000;

interface CacheEntry {
  at: number;
  comps: Comp[];
  source: 'ebay_sold' | 'ebay_active';
  soldPerWeek: number | null;
}

const cache = new Map<string, CacheEntry>();

let token: { value: string; expiresAt: number } | null = null;

async function getToken(): Promise<string> {
  if (token && Date.now() < token.expiresAt - 60_000) return token.value;

  const basic = Buffer.from(
    `${config.ebay.clientId}:${config.ebay.clientSecret}`,
  ).toString('base64');

  const res = await fetch(OAUTH_URL, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${basic}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: new URLSearchParams({ grant_type: 'client_credentials', scope: SCOPE }),
  });

  if (!res.ok) {
    throw new Error(`eBay OAuth failed: ${res.status} ${await res.text()}`);
  }
  const json = (await res.json()) as { access_token: string; expires_in: number };
  token = {
    value: json.access_token,
    expiresAt: Date.now() + json.expires_in * 1000,
  };
  return token.value;
}

export interface CompResult {
  comps: Comp[];
  source: 'ebay_sold' | 'ebay_active' | 'none';
  soldPerWeek: number | null;
  /** Set when we could not reach eBay, so the UI can say why. */
  error?: string;
}

export function isConfigured(): boolean {
  return config.ebay.configured;
}

/**
 * Fetch comps for a query. Prefers *sold* comps via Marketplace Insights when
 * the keyset has been granted it, and falls back to active listings, which are
 * asking prices and get discounted downstream (see ACTIVE_TO_SOLD_FACTOR).
 */
export async function fetchComps(query: string, limit = 50): Promise<CompResult> {
  if (!query.trim()) return { comps: [], source: 'none', soldPerWeek: null };
  if (!isConfigured()) {
    return {
      comps: [],
      source: 'none',
      soldPerWeek: null,
      error: 'eBay API credentials are not configured',
    };
  }

  const key = `${config.ebay.insightsEnabled ? 'sold' : 'active'}:${query}:${limit}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
    return { comps: hit.comps, source: hit.source, soldPerWeek: hit.soldPerWeek };
  }

  try {
    const result = config.ebay.insightsEnabled
      ? await fetchSold(query, limit)
      : await fetchActive(query, limit);

    if (cache.size >= MAX_CACHE_ENTRIES) {
      // Cheap eviction: drop the oldest tenth rather than tracking LRU.
      const victims = [...cache.entries()]
        .sort((a, b) => a[1].at - b[1].at)
        .slice(0, Math.ceil(MAX_CACHE_ENTRIES / 10));
      for (const [k] of victims) cache.delete(k);
    }
    cache.set(key, {
      at: Date.now(),
      comps: result.comps,
      source: result.source as 'ebay_sold' | 'ebay_active',
      soldPerWeek: result.soldPerWeek,
    });
    return result;
  } catch (err) {
    return {
      comps: [],
      source: 'none',
      soldPerWeek: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

async function ebayGet(url: string, params: URLSearchParams): Promise<unknown> {
  const accessToken = await getToken();
  const res = await fetch(`${url}?${params}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'X-EBAY-C-MARKETPLACE-ID': config.ebay.marketplaceId,
      Accept: 'application/json',
    },
  });
  if (!res.ok) {
    throw new Error(`eBay ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  return res.json();
}

interface ItemSummary {
  title?: string;
  price?: { value?: string };
  condition?: string;
  itemWebUrl?: string;
  itemEndDate?: string;
  lastSoldDate?: string;
  lastSoldPrice?: { value?: string };
}

async function fetchActive(query: string, limit: number): Promise<CompResult> {
  const params = new URLSearchParams({
    q: query,
    limit: String(Math.min(200, limit)),
    // Used + open-box + for-parts. New units are a different market and would
    // drag the median up on a used item.
    filter: 'conditionIds:{3000|4000|5000|6000|7000},buyingOptions:{FIXED_PRICE}',
  });
  const json = (await ebayGet(BROWSE_URL, params)) as { itemSummaries?: ItemSummary[] };
  const comps = (json.itemSummaries ?? [])
    .map(toComp)
    .filter((c): c is Comp => c !== null);
  return { comps, source: comps.length ? 'ebay_active' : 'none', soldPerWeek: null };
}

async function fetchSold(query: string, limit: number): Promise<CompResult> {
  const since = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString();
  const params = new URLSearchParams({
    q: query,
    limit: String(Math.min(200, limit)),
    filter: `lastSoldDate:[${since}..],conditionIds:{3000|4000|5000|6000|7000}`,
  });
  const json = (await ebayGet(INSIGHTS_URL, params)) as {
    itemSales?: ItemSummary[];
    total?: number;
  };
  const sales = json.itemSales ?? [];
  const comps = sales.map(toComp).filter((c): c is Comp => c !== null);

  // total is the 90-day sold count for the query; convert to a weekly rate.
  const soldPerWeek =
    typeof json.total === 'number' ? Math.round((json.total / 90) * 7 * 10) / 10 : null;

  return { comps, source: comps.length ? 'ebay_sold' : 'none', soldPerWeek };
}

function toComp(item: ItemSummary): Comp | null {
  const raw = item.lastSoldPrice?.value ?? item.price?.value;
  const price = Number(raw);
  if (!item.title || !Number.isFinite(price) || price <= 0) return null;
  const soldAt = item.lastSoldDate ? Date.parse(item.lastSoldDate) : null;
  return {
    title: item.title,
    price,
    soldAt: Number.isFinite(soldAt as number) ? soldAt : null,
    condition: item.condition ?? null,
    url: item.itemWebUrl ?? null,
  };
}

/** Test hook. */
export function __clearCache(): void {
  cache.clear();
}
