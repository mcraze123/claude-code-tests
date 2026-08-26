import { Router } from 'express';
import { z } from 'zod';
import { config } from '../config.js';
import * as repo from '../db/repo.js';
import { notificationChannels, send, formatNotification } from '../notify/index.js';
import { pollerStatus, runSweep } from '../poller.js';
import { sourceStatus } from '../sources/index.js';
import { SECTIONS, type Section } from '../types.js';
import { isConfigured as ebayConfigured } from '../valuation/ebay.js';
import { feeModelLabel } from '../valuation/fees.js';

export const api = Router();

const sectionSchema = z.enum(['electronics', 'appliances', 'vehicles']);

const asSection = (v: unknown): Section | null => {
  const parsed = sectionSchema.safeParse(v);
  return parsed.success ? parsed.data : null;
};

/* -------------------------------------------------------------------------- */
/* Status                                                                      */
/* -------------------------------------------------------------------------- */

api.get('/health', (_req, res) => {
  res.json({ ok: true, at: Date.now() });
});

api.get('/status', (_req, res) => {
  const settings = repo.getSettings();
  res.json({
    poller: pollerStatus(),
    sources: sourceStatus(),
    notifications: {
      channels: notificationChannels(),
      subscriptions: repo.listSubscriptions().length,
      quietHours:
        settings.quietHoursStart === null
          ? null
          : { start: settings.quietHoursStart, end: settings.quietHoursEnd },
    },
    comps: {
      ebayConfigured: ebayConfigured(),
      soldDataEnabled: config.ebay.insightsEnabled,
    },
    feeModels: Object.fromEntries(SECTIONS.map((s) => [s, feeModelLabel(s)])),
    location: {
      lat: settings.lat,
      lon: settings.lon,
      updatedAt: settings.locationUpdatedAt,
      fallbackConfigured: config.home.lat !== null && config.home.lon !== null,
    },
    recentRuns: repo.recentPollRuns(5),
  });
});

api.post('/sweep', async (_req, res) => {
  const stats = await runSweep();
  res.json(stats);
});

/* -------------------------------------------------------------------------- */
/* Deals                                                                       */
/* -------------------------------------------------------------------------- */

api.get('/deals', (req, res) => {
  const section = asSection(req.query.section);
  if (!section) {
    res.status(400).json({ error: `section must be one of ${SECTIONS.join(', ')}` });
    return;
  }
  const sortRaw = String(req.query.sort ?? 'score');
  const sort = (['score', 'new', 'profit', 'distance'] as const).includes(sortRaw as never)
    ? (sortRaw as 'score' | 'new' | 'profit' | 'distance')
    : 'score';

  const limit = Math.min(200, Math.max(1, Number(req.query.limit) || 60));
  const profileId = Number(req.query.profileId) || undefined;
  const maxAgeHours = Number(req.query.maxAgeHours) || undefined;

  res.json({
    section,
    sort,
    deals: repo.feed({
      section,
      sort,
      limit,
      includeRejected: req.query.includeRejected === 'true',
      ...(profileId ? { profileId } : {}),
      ...(maxAgeHours ? { maxAgeHours } : {}),
    }),
  });
});

api.get('/deals/:key', (req, res) => {
  const deal = repo.getDeal(req.params.key);
  if (!deal) {
    res.status(404).json({ error: 'not found' });
    return;
  }
  res.json(deal);
});

api.post('/deals/:key/hide', (req, res) => {
  const hidden = req.body?.hidden !== false;
  res.json({ ok: repo.hideListing(req.params.key, hidden), hidden });
});

/* -------------------------------------------------------------------------- */
/* Profiles                                                                    */
/* -------------------------------------------------------------------------- */

const profileSchema = z.object({
  section: sectionSchema,
  name: z.string().min(1).max(120),
  enabled: z.boolean().default(true),
  keywords: z.array(z.string().min(1).max(120)).max(200).default([]),
  mustInclude: z.array(z.string().min(1).max(80)).max(50).default([]),
  exclude: z.array(z.string().min(1).max(80)).max(200).default([]),
  minPrice: z.number().min(0).max(1_000_000).nullable().default(null),
  maxPrice: z.number().min(0).max(1_000_000).nullable().default(null),
  radiusMi: z.number().min(1).max(500).default(30),
  minProfit: z.number().min(0).max(1_000_000).default(60),
  minRoi: z.number().min(0).max(50).default(0.5),
  includeBroken: z.boolean().default(true),
  minConfidence: z.number().min(0).max(1).default(0.35),
  notify: z.boolean().default(true),
  sources: z.array(z.string().min(1).max(40)).nullable().default(null),
});

api.get('/profiles', (req, res) => {
  const section = asSection(req.query.section);
  res.json({ profiles: section ? repo.listProfiles(section) : repo.listProfiles() });
});

api.post('/profiles', (req, res) => {
  const parsed = profileSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'invalid profile', issues: parsed.error.issues });
    return;
  }
  res.status(201).json(repo.createProfile(parsed.data));
});

api.patch('/profiles/:id', (req, res) => {
  const parsed = profileSchema.partial().safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'invalid profile', issues: parsed.error.issues });
    return;
  }
  const updated = repo.updateProfile(Number(req.params.id), parsed.data);
  if (!updated) {
    res.status(404).json({ error: 'not found' });
    return;
  }
  res.json(updated);
});

api.delete('/profiles/:id', (req, res) => {
  res.json({ ok: repo.deleteProfile(Number(req.params.id)) });
});

/* -------------------------------------------------------------------------- */
/* Settings & location                                                         */
/* -------------------------------------------------------------------------- */

const settingsSchema = z.object({
  mileageCostPerMi: z.number().min(0).max(10).optional(),
  ebayAdRatePct: z.number().min(0).max(0.5).optional(),
  quietHoursStart: z.number().int().min(0).max(23).nullable().optional(),
  quietHoursEnd: z.number().int().min(0).max(23).nullable().optional(),
});

api.get('/settings', (_req, res) => {
  res.json(repo.getSettings());
});

api.patch('/settings', (req, res) => {
  const parsed = settingsSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'invalid settings', issues: parsed.error.issues });
    return;
  }
  res.json(repo.updateSettings(parsed.data));
});

const locationSchema = z.object({
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
});

/** The PWA posts the phone's GPS fix here; distances key off the latest one. */
api.post('/location', (req, res) => {
  const parsed = locationSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'lat and lon are required numbers' });
    return;
  }
  res.json(
    repo.updateSettings({
      lat: parsed.data.lat,
      lon: parsed.data.lon,
      locationUpdatedAt: Date.now(),
    }),
  );
});

/* -------------------------------------------------------------------------- */
/* Push                                                                        */
/* -------------------------------------------------------------------------- */

api.get('/push/key', (_req, res) => {
  res.json({
    publicKey: config.push.vapidPublicKey || null,
    channels: notificationChannels(),
  });
});

const subscriptionSchema = z.object({
  endpoint: z.string().url(),
  keys: z.object({ p256dh: z.string().min(1), auth: z.string().min(1) }),
});

api.post('/push/subscribe', (req, res) => {
  const parsed = subscriptionSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'invalid subscription' });
    return;
  }
  repo.saveSubscription(parsed.data);
  res.status(201).json({ ok: true });
});

api.delete('/push/subscribe', (req, res) => {
  const endpoint = String(req.body?.endpoint ?? '');
  if (endpoint) repo.deleteSubscription(endpoint);
  res.json({ ok: true });
});

api.post('/push/test', async (req, res) => {
  const key = typeof req.body?.key === 'string' ? req.body.key : null;
  const deal = key ? repo.getDeal(key) : null;

  const payload = deal
    ? formatNotification(deal)
    : {
        title: '⚡ Scavenger test notification',
        body: 'If you can read this on your phone, alerts are working.',
        url: '/',
        imageUrl: null,
        key: 'test',
        section: 'electronics',
        priority: 4,
        tags: ['just_listed'],
      };

  res.json(await send(payload));
});
