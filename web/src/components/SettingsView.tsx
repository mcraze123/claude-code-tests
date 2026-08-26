import { useState } from 'react';
import { api } from '../api';
import { currentPosition, enablePush, isIosNotInstalled, pushSupported } from '../lib/push';
import type { Settings, Status } from '../types';

interface Props {
  status: Status | null;
  settings: Settings | null;
  onSettingsChange: (s: Settings) => void;
  onRefresh: () => void;
}

const ago = (ts: number | null): string => {
  if (!ts) return 'never';
  const min = Math.round((Date.now() - ts) / 60_000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
};

export function SettingsView({ status, settings, onSettingsChange, onRefresh }: Props): JSX.Element {
  const [pushMsg, setPushMsg] = useState<{ tone: 'good' | 'warn' | 'bad'; text: string } | null>(null);
  const [locMsg, setLocMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const patch = async (body: Partial<Settings>): Promise<void> => {
    onSettingsChange(await api.updateSettings(body));
  };

  const doEnablePush = async (): Promise<void> => {
    setBusy('push');
    try {
      const result = await enablePush();
      setPushMsg({ tone: result.ok ? 'good' : 'warn', text: result.message });
      onRefresh();
    } catch (err) {
      setPushMsg({ tone: 'bad', text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };

  const doTestPush = async (): Promise<void> => {
    setBusy('test');
    try {
      const r = await api.testPush();
      setPushMsg(
        r.delivered
          ? { tone: 'good', text: `Sent via ${r.channels.join(', ')}. Check your phone.` }
          : {
              tone: 'warn',
              text: r.errors.length
                ? r.errors.join('; ')
                : 'No delivery channel is configured. Set VAPID keys or NTFY_TOPIC in .env.',
            },
      );
    } finally {
      setBusy(null);
    }
  };

  const doUpdateLocation = async (): Promise<void> => {
    setBusy('loc');
    setLocMsg('Asking your browser for a GPS fix…');
    try {
      const pos = await currentPosition();
      onSettingsChange(await api.setLocation(pos.coords.latitude, pos.coords.longitude));
      setLocMsg(`Location updated (±${Math.round(pos.coords.accuracy)} m).`);
      onRefresh();
    } catch (err) {
      setLocMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const hasLocation = settings?.lat != null && settings?.lon != null;

  return (
    <>
      <section className="panel">
        <h2>📍 Location</h2>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)' }}>
          Every distance and fuel cost is measured from here. Update it when you move — searches
          use it on the next sweep.
        </p>
        <div className="kv">
          <span className="k">Current fix</span>
          <span className="v">
            {hasLocation
              ? `${settings!.lat!.toFixed(4)}, ${settings!.lon!.toFixed(4)}`
              : status?.location.fallbackConfigured
                ? 'using HOME_LAT / HOME_LON'
                : 'not set'}
          </span>
        </div>
        <div className="kv">
          <span className="k">Updated</span>
          <span className="v">{ago(settings?.locationUpdatedAt ?? null)}</span>
        </div>
        <button className="btn btn-primary" onClick={doUpdateLocation} disabled={busy === 'loc'}>
          {busy === 'loc' ? 'Locating…' : 'Use my current location'}
        </button>
        {locMsg && <div className="notice">{locMsg}</div>}
      </section>

      <section className="panel">
        <h2>🔔 Notifications</h2>
        <div className="kv">
          <span className="k">Active channels</span>
          <span className="v">{status?.notifications.channels.join(', ') || 'none'}</span>
        </div>
        <div className="kv">
          <span className="k">Devices subscribed</span>
          <span className="v">{status?.notifications.subscriptions ?? 0}</span>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn btn-primary"
            onClick={doEnablePush}
            disabled={busy === 'push' || !pushSupported()}
            style={{ flex: 1 }}
          >
            {busy === 'push' ? 'Enabling…' : 'Enable alerts on this device'}
          </button>
          <button className="btn" onClick={doTestPush} disabled={busy === 'test'}>
            Send test
          </button>
        </div>

        {isIosNotInstalled() && (
          <div className="notice" data-tone="warn">
            On iPhone, web push only works once the app is installed: tap Share → Add to Home
            Screen, then open Scavenger from the icon.
          </div>
        )}
        {pushMsg && (
          <div className="notice" data-tone={pushMsg.tone}>
            {pushMsg.text}
          </div>
        )}

        <div className="grid-2">
          <div className="field">
            <label>Quiet hours start</label>
            <input
              type="number"
              min="0"
              max="23"
              value={settings?.quietHoursStart ?? ''}
              placeholder="off"
              onChange={(e) =>
                void patch({ quietHoursStart: e.target.value === '' ? null : Number(e.target.value) })
              }
            />
          </div>
          <div className="field">
            <label>Quiet hours end</label>
            <input
              type="number"
              min="0"
              max="23"
              value={settings?.quietHoursEnd ?? ''}
              placeholder="off"
              onChange={(e) =>
                void patch({ quietHoursEnd: e.target.value === '' ? null : Number(e.target.value) })
              }
            />
          </div>
        </div>
        <span className="hint">
          Deals found during quiet hours are held, not dropped — they go out when the window ends.
        </span>
      </section>

      <section className="panel">
        <h2>💵 Deal maths</h2>
        <div className="field">
          <label>Fuel cost per mile ($)</label>
          <input
            type="number"
            step="0.05"
            value={settings?.mileageCostPerMi ?? 0.35}
            onChange={(e) => void patch({ mileageCostPerMi: Number(e.target.value) || 0 })}
          />
          <span className="hint">Charged as a round trip with a 1.25× road-distance allowance.</span>
        </div>
        <div className="field">
          <label>eBay promoted-listing ad rate</label>
          <input
            type="number"
            step="0.01"
            min="0"
            max="0.3"
            value={settings?.ebayAdRatePct ?? 0}
            onChange={(e) => void patch({ ebayAdRatePct: Number(e.target.value) || 0 })}
          />
          <span className="hint">0 if you do not promote. 0.02 = 2% on top of the final value fee.</span>
        </div>
        <div className="kv">
          <span className="k">Fee model in use</span>
          <span className="v">{status ? Object.values(status.feeModels).join(' · ') : '—'}</span>
        </div>
      </section>

      <section className="panel">
        <h2>🔌 Sources</h2>
        {status?.sources.map((s) => (
          <div key={s.id}>
            <div className="kv">
              <span className="k">{s.label}</span>
              <span className="v">
                {!s.enabled ? '○ off' : s.available ? '● on' : '⚠ needs setup'}
              </span>
            </div>
            {s.enabled && !s.available && s.reason && (
              <div className="notice" data-tone="warn" style={{ marginBottom: 8 }}>
                {s.reason}
              </div>
            )}
          </div>
        ))}
        <div className="kv">
          <span className="k">Resale comps</span>
          <span className="v">
            {status?.comps.ebayConfigured
              ? status.comps.soldDataEnabled
                ? 'eBay sold data'
                : 'eBay active listings'
              : 'not configured'}
          </span>
        </div>
        {status && !status.comps.ebayConfigured && (
          <div className="notice" data-tone="warn">
            Without eBay API keys only the bundled sample listings can be valued. Add
            EBAY_CLIENT_ID and EBAY_CLIENT_SECRET to .env.
          </div>
        )}
      </section>

      <section className="panel">
        <h2>⏱ Poller</h2>
        <div className="kv">
          <span className="k">Sweeps every</span>
          <span className="v">{status ? `${status.poller.intervalSeconds}s` : '—'}</span>
        </div>
        <div className="kv">
          <span className="k">Last sweep</span>
          <span className="v">{ago(status?.poller.lastRunAt ?? null)}</span>
        </div>
        {status?.poller.lastStats && (
          <>
            <div className="kv">
              <span className="k">Last result</span>
              <span className="v">
                {status.poller.lastStats.listingsSeen} seen · {status.poller.lastStats.newListings} new ·{' '}
                {status.poller.lastStats.dealsFound} deals · {status.poller.lastStats.notified} pushed
              </span>
            </div>
            {status.poller.lastStats.errors.length > 0 && (
              <div className="notice" data-tone="warn">
                {status.poller.lastStats.errors.slice(0, 4).map((e) => (
                  <div key={e}>{e}</div>
                ))}
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}
