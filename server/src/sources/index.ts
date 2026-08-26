import { config } from '../config.js';
import { craigslist } from './craigslist.js';
import { providerAdapter } from './provider.js';
import { sample } from './sample.js';
import type { SourceAdapter } from './types.js';

const facebook = providerAdapter({
  id: 'facebook',
  label: 'Facebook Marketplace',
  docsAnchor: 'facebook-marketplace',
  url: config.sources.facebook.providerUrl,
  key: config.sources.facebook.providerKey,
});

const offerup = providerAdapter({
  id: 'offerup',
  label: 'OfferUp',
  docsAnchor: 'offerup',
  url: config.sources.offerup.providerUrl,
  key: config.sources.offerup.providerKey,
});

const ALL: SourceAdapter[] = [craigslist, facebook, offerup, sample];

const byId = new Map(ALL.map((a) => [a.id, a]));

/** Every adapter that exists, whether configured or not. */
export function allSources(): SourceAdapter[] {
  return ALL;
}

export function getSource(id: string): SourceAdapter | undefined {
  return byId.get(id);
}

/** Adapters the operator turned on in .env *and* that have what they need. */
export function activeSources(): SourceAdapter[] {
  return config.sources.enabled
    .map((id) => byId.get(id))
    .filter((a): a is SourceAdapter => Boolean(a) && a!.isAvailable());
}

/** For the UI's source panel: what is on, off, and why. */
export function sourceStatus(): {
  id: string;
  label: string;
  enabled: boolean;
  available: boolean;
  reason: string | null;
  docsAnchor: string;
}[] {
  return ALL.map((a) => ({
    id: a.id,
    label: a.label,
    enabled: config.sources.enabled.includes(a.id),
    available: a.isAvailable(),
    reason: a.unavailableReason(),
    docsAnchor: a.docsAnchor,
  }));
}

export type { SourceAdapter, SearchRequest } from './types.js';
