import { api } from '../api';

/** VAPID keys are base64url; PushManager wants raw bytes. */
function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  const raw = atob(padded);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export function pushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

/**
 * iOS only allows web push once the site is installed to the home screen, and
 * silently rejects the subscribe call otherwise. Detecting it lets us give a
 * real instruction instead of a generic failure.
 */
export function isIosNotInstalled(): boolean {
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone =
    window.matchMedia('(display-mode: standalone)').matches ||
    (navigator as { standalone?: boolean }).standalone === true;
  return ios && !standalone;
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null;
  return navigator.serviceWorker.register('/sw.js', { scope: '/' });
}

export interface EnableResult {
  ok: boolean;
  message: string;
}

export async function enablePush(): Promise<EnableResult> {
  if (!pushSupported()) {
    return { ok: false, message: 'This browser does not support web push. Use the ntfy channel instead.' };
  }
  if (isIosNotInstalled()) {
    return {
      ok: false,
      message: 'On iPhone, tap Share → Add to Home Screen first, then open Scavenger from the icon and try again.',
    };
  }

  const { publicKey } = await api.pushKey();
  if (!publicKey) {
    return { ok: false, message: 'The server has no VAPID keys. Run: npm run gen:vapid --workspace=server' };
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    return { ok: false, message: `Notification permission was ${permission}.` };
  }

  const registration = await registerServiceWorker();
  if (!registration) return { ok: false, message: 'Service worker registration failed.' };
  await navigator.serviceWorker.ready;

  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
    }));

  await api.subscribe(subscription.toJSON());
  return { ok: true, message: 'Alerts are on for this device.' };
}

/** Push the phone's current GPS fix to the server. */
export function currentPosition(): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('This browser has no geolocation.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: false,
      timeout: 12_000,
      maximumAge: 300_000,
    });
  });
}
