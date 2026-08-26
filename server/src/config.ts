import 'dotenv/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// server/src/ in dev, server/dist/ once built - both are one level under the
// server package, which is where data/ lives.
const serverRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const num = (v: string | undefined, fallback: number): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

const optNum = (v: string | undefined): number | null => {
  if (v === undefined || v.trim() === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const list = (v: string | undefined, fallback: string[]): string[] => {
  if (!v || v.trim() === '') return fallback;
  return v.split(',').map((s) => s.trim()).filter(Boolean);
};

export const config = {
  port: num(process.env.PORT, 8080),
  // A relative DATABASE_PATH is resolved against the caller's cwd, which is
  // what an operator expects; the default is anchored to the package so
  // `npm start` behaves the same from any directory.
  databasePath: process.env.DATABASE_PATH
    ? path.resolve(process.env.DATABASE_PATH)
    : path.join(serverRoot, 'data', 'scavenger.db'),
  pollIntervalSeconds: Math.max(30, num(process.env.POLL_INTERVAL_SECONDS, 120)),

  home: {
    lat: optNum(process.env.HOME_LAT),
    lon: optNum(process.env.HOME_LON),
    zip: process.env.HOME_ZIP ?? null,
  },

  ebay: {
    clientId: process.env.EBAY_CLIENT_ID ?? '',
    clientSecret: process.env.EBAY_CLIENT_SECRET ?? '',
    marketplaceId: process.env.EBAY_MARKETPLACE_ID ?? 'EBAY_US',
    insightsEnabled: process.env.EBAY_INSIGHTS_ENABLED === 'true',
    get configured(): boolean {
      return Boolean(process.env.EBAY_CLIENT_ID && process.env.EBAY_CLIENT_SECRET);
    },
  },

  push: {
    vapidPublicKey: process.env.VAPID_PUBLIC_KEY ?? '',
    vapidPrivateKey: process.env.VAPID_PRIVATE_KEY ?? '',
    vapidSubject: process.env.VAPID_SUBJECT ?? 'mailto:scavenger@example.com',
    ntfyTopic: process.env.NTFY_TOPIC ?? '',
    ntfyServer: process.env.NTFY_SERVER ?? 'https://ntfy.sh',
  },

  sources: {
    enabled: list(process.env.ENABLED_SOURCES, ['craigslist', 'sample']),
    craigslistSites: list(process.env.CRAIGSLIST_SITES, ['sfbay']),
    facebook: {
      providerUrl: process.env.FB_PROVIDER_URL ?? '',
      providerKey: process.env.FB_PROVIDER_KEY ?? '',
    },
    offerup: {
      providerUrl: process.env.OFFERUP_PROVIDER_URL ?? '',
      providerKey: process.env.OFFERUP_PROVIDER_KEY ?? '',
    },
  },
} as const;

export type Config = typeof config;
