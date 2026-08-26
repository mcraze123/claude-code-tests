import { config } from '../config.js';
import type { NotificationPayload } from './format.js';

/**
 * ntfy delivery. This is the zero-setup path: install the ntfy app, subscribe
 * to a private random topic, put the topic in .env. It works on both phone
 * platforms without certificates, build steps or a public URL.
 *
 * Publishing uses ntfy's JSON body API rather than its header API on purpose:
 * HTTP headers are limited to ISO-8859-1, and our titles carry emoji.
 */
export function ntfyConfigured(): boolean {
  return config.push.ntfyTopic.trim().length > 0;
}

/** Badge ids -> ntfy tag names, which the app renders as emoji. */
const EMOJI_TAGS: Record<string, string> = {
  just_listed: 'zap',
  hot: 'fire',
  high_margin: 'moneybag',
  steal: 'dart',
  broken: 'wrench',
  parts_only: 'jigsaw',
  very_close: 'round_pushpin',
  far: 'motorway',
  price_drop: 'chart_with_downwards_trend',
  low_confidence: 'warning',
  no_price: 'question',
  thin_comps: 'feather',
};

interface NtfyMessage {
  topic: string;
  title: string;
  message: string;
  priority: number;
  tags: string[];
  click: string;
  attach?: string;
  actions?: { action: 'view'; label: string; url: string }[];
}

export async function sendNtfy(payload: NotificationPayload): Promise<void> {
  if (!ntfyConfigured()) return;

  const message: NtfyMessage = {
    topic: config.push.ntfyTopic,
    title: payload.title,
    message: payload.body,
    priority: payload.priority,
    tags: payload.tags.map((t) => EMOJI_TAGS[t] ?? t),
    click: payload.url,
    actions: [{ action: 'view', label: 'Open listing', url: payload.url }],
  };
  if (payload.imageUrl) message.attach = payload.imageUrl;

  const res = await fetch(config.push.ntfyServer, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(message),
  });
  if (!res.ok) {
    throw new Error(`ntfy ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
}
