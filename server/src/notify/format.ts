import type { ScoredDeal } from '../types.js';
import { overallConfidence } from '../valuation/score.js';

export interface NotificationPayload {
  title: string;
  body: string;
  url: string;
  imageUrl: string | null;
  key: string;
  section: string;
  /** 1 (lowest) to 5 (highest), mapped onto ntfy priorities. */
  priority: number;
  tags: string[];
}

const money = (n: number): string =>
  n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${Math.round(n)}`;

export function truncate(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, max - 1).trimEnd()}…`;
}

/**
 * One glanceable line. The whole point of this app is deciding whether to get
 * in the car, so the notification leads with profit and distance.
 */
export function formatNotification(deal: ScoredDeal): NotificationPayload {
  const { math, listing, valuation, repair } = deal;
  const confidence = overallConfidence(valuation, repair, math);

  const lead = deal.badges.some((b) => b.id === 'steal') ? '🎯' : '⚡';
  const title = `${lead} ${money(math.netProfit)} profit · ${truncate(listing.title, 52)}`;

  const parts: string[] = [
    `Ask ${listing.askPrice === null ? '(no price)' : money(listing.askPrice)}`,
    `resale ~${money(math.expectedSalePrice)}`,
  ];
  if (repair.isBroken && math.partsCost > 0) parts.push(`parts ${money(math.partsCost)}`);
  if (deal.distanceMi !== null) parts.push(`${deal.distanceMi.toFixed(1)} mi`);
  parts.push(`${Math.round(confidence * 100)}% conf`);

  const badgeLine = deal.badges
    .filter((b) => b.id !== 'just_listed')
    .slice(0, 3)
    .map((b) => `${b.icon} ${b.label}`)
    .join('  ');

  return {
    title,
    body: badgeLine ? `${parts.join(' · ')}\n${badgeLine}` : parts.join(' · '),
    url: listing.url,
    imageUrl: listing.imageUrl ?? null,
    key: deal.key,
    section: deal.section,
    priority: deal.score >= 70 ? 5 : deal.score >= 45 ? 4 : 3,
    tags: deal.badges.map((b) => b.id),
  };
}
