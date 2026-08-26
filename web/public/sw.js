/**
 * Service worker: receives web push and opens the listing on tap.
 *
 * Kept deliberately tiny - there is no offline caching here, because a stale
 * deal feed is worse than no feed.
 */

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { title: 'Scavenger', body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'Scavenger';
  const options = {
    body: payload.body || '',
    icon: '/icon.svg',
    badge: '/icon.svg',
    image: payload.imageUrl || undefined,
    tag: payload.key || 'scavenger',
    renotify: true,
    requireInteraction: (payload.priority || 0) >= 5,
    vibrate: [80, 40, 80],
    data: { url: payload.url || '/', key: payload.key, section: payload.section },
    actions: [
      { action: 'open', title: 'Open listing' },
      { action: 'app', title: 'Open Scavenger' },
    ],
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = event.action === 'app' ? '/' : data.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Reuse an open Scavenger tab when the target is the app itself.
      if (target === '/') {
        const existing = clients.find((c) => 'focus' in c);
        if (existing) return existing.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});
