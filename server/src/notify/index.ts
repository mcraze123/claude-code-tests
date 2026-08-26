import { getSettings } from '../db/repo.js';
import type { AppSettings, ScoredDeal } from '../types.js';
import { formatNotification, type NotificationPayload } from './format.js';
import { ntfyConfigured, sendNtfy } from './ntfy.js';
import { sendWebPush, webPushConfigured } from './webpush.js';

export { formatNotification } from './format.js';
export type { NotificationPayload } from './format.js';

/**
 * Quiet hours wrap midnight when start > end (e.g. 22 -> 7).
 * A null on either side disables the whole feature.
 */
export function inQuietHours(settings: AppSettings, now = new Date()): boolean {
  const { quietHoursStart: start, quietHoursEnd: end } = settings;
  if (start === null || end === null || start === end) return false;
  const hour = now.getHours();
  return start < end ? hour >= start && hour < end : hour >= start || hour < end;
}

export function notificationChannels(): string[] {
  const channels: string[] = [];
  if (webPushConfigured()) channels.push('web-push');
  if (ntfyConfigured()) channels.push('ntfy');
  return channels;
}

export interface DeliveryResult {
  delivered: boolean;
  channels: string[];
  errors: string[];
  skippedReason?: string;
}

export async function notifyDeal(
  deal: ScoredDeal,
  opts: { ignoreQuietHours?: boolean } = {},
): Promise<DeliveryResult> {
  const settings = getSettings();
  if (!opts.ignoreQuietHours && inQuietHours(settings)) {
    return { delivered: false, channels: [], errors: [], skippedReason: 'quiet hours' };
  }
  return send(formatNotification(deal));
}

export async function send(payload: NotificationPayload): Promise<DeliveryResult> {
  const channels: string[] = [];
  const errors: string[] = [];

  const results = await Promise.allSettled([
    (async () => {
      const n = await sendWebPush(payload);
      if (n > 0) channels.push(`web-push(${n})`);
    })(),
    (async () => {
      if (!ntfyConfigured()) return;
      await sendNtfy(payload);
      channels.push('ntfy');
    })(),
  ]);

  for (const r of results) {
    if (r.status === 'rejected') {
      errors.push(r.reason instanceof Error ? r.reason.message : String(r.reason));
    }
  }

  return { delivered: channels.length > 0, channels, errors };
}
