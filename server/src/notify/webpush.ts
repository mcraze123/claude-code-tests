import webpush from 'web-push';
import { config } from '../config.js';
import {
  deleteSubscription,
  listSubscriptions,
  recordSubscriptionFailure,
  recordSubscriptionSuccess,
} from '../db/repo.js';
import type { NotificationPayload } from './format.js';

let configured = false;

export function webPushConfigured(): boolean {
  return Boolean(config.push.vapidPublicKey && config.push.vapidPrivateKey);
}

function ensureConfigured(): void {
  if (configured || !webPushConfigured()) return;
  webpush.setVapidDetails(
    config.push.vapidSubject,
    config.push.vapidPublicKey,
    config.push.vapidPrivateKey,
  );
  configured = true;
}

/** Fan out to every registered browser, pruning the ones that have gone away. */
export async function sendWebPush(payload: NotificationPayload): Promise<number> {
  if (!webPushConfigured()) return 0;
  ensureConfigured();

  const subs = listSubscriptions();
  const body = JSON.stringify(payload);
  let delivered = 0;

  await Promise.all(
    subs.map(async (sub) => {
      try {
        await webpush.sendNotification(
          { endpoint: sub.endpoint, keys: sub.keys },
          body,
          { TTL: 3600, urgency: payload.priority >= 5 ? 'high' : 'normal' },
        );
        recordSubscriptionSuccess(sub.endpoint);
        delivered += 1;
      } catch (err) {
        const status = (err as { statusCode?: number }).statusCode;
        // 404/410 mean the browser dropped the subscription for good.
        if (status === 404 || status === 410) deleteSubscription(sub.endpoint);
        else recordSubscriptionFailure(sub.endpoint);
      }
    }),
  );

  return delivered;
}
