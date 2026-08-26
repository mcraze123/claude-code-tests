import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config } from './config.js';
import { db } from './db/index.js';
import { api } from './routes/api.js';
import { seedProfiles } from './seed.js';
import { startPoller } from './poller.js';
import { activeSources, sourceStatus } from './sources/index.js';
import { notificationChannels } from './notify/index.js';
import { isConfigured as ebayConfigured } from './valuation/ebay.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const webDist = path.resolve(here, '../../web/dist');

const app = express();
app.use(express.json({ limit: '256kb' }));

app.use('/api', api);

// The built PWA, when it exists. Running the server alone is fine in dev -
// Vite proxies /api to this process instead.
if (fs.existsSync(webDist)) {
  app.use(express.static(webDist));
  app.get('*', (req, res, next) => {
    if (req.path.startsWith('/api/')) return next();
    res.sendFile(path.join(webDist, 'index.html'));
  });
}

db();
const seeded = seedProfiles();

app.listen(config.port, () => {
  console.log(`\n  Scavenger listening on http://localhost:${config.port}`);
  if (seeded > 0) console.log(`  seeded ${seeded} starter search profiles`);

  const active = activeSources().map((s) => s.id);
  console.log(`  sources:       ${active.length ? active.join(', ') : 'NONE ENABLED'}`);
  for (const s of sourceStatus()) {
    if (s.enabled && !s.available) console.log(`    ! ${s.label}: ${s.reason}`);
  }

  console.log(
    `  comps:         ${
      ebayConfigured()
        ? config.ebay.insightsEnabled
          ? 'eBay (sold data)'
          : 'eBay (active listings)'
        : 'NOT CONFIGURED — only sample fixtures will be valued'
    }`,
  );

  const channels = notificationChannels();
  console.log(
    `  notifications: ${channels.length ? channels.join(', ') : 'NONE — set VAPID keys or NTFY_TOPIC'}`,
  );
  console.log(`  sweeping every ${config.pollIntervalSeconds}s\n`);

  startPoller();
});
