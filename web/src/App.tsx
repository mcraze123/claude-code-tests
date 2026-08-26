import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { DealCard } from './components/DealCard';
import { ProfileEditor, blankProfile } from './components/ProfileEditor';
import { SettingsView } from './components/SettingsView';
import { registerServiceWorker } from './lib/push';
import type { Deal, Profile, Section, Settings, SortKey, Status } from './types';

const SECTIONS: { id: Section; label: string; icon: string }[] = [
  { id: 'electronics', label: 'Electronics', icon: '💻' },
  { id: 'appliances', label: 'Appliances', icon: '🧺' },
  { id: 'vehicles', label: 'Cars', icon: '🚗' },
];

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'score', label: 'Best' },
  { id: 'new', label: 'Newest' },
  { id: 'profit', label: 'Profit' },
  { id: 'distance', label: 'Closest' },
];

type View = 'deals' | 'searches' | 'settings';

export function App(): JSX.Element {
  const [section, setSection] = useState<Section>('electronics');
  const [view, setView] = useState<View>('deals');
  const [sort, setSort] = useState<SortKey>('score');
  const [showRejected, setShowRejected] = useState(false);

  const [deals, setDeals] = useState<Deal[]>([]);
  const [counts, setCounts] = useState<Record<Section, number>>({
    electronics: 0,
    appliances: 0,
    vehicles: 0,
  });
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [status, setStatus] = useState<Status | null>(null);

  const [editing, setEditing] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [sweeping, setSweeping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDeals = useCallback(async (): Promise<void> => {
    try {
      const res = await api.deals(section, { sort, includeRejected: showRejected });
      setDeals(res.deals);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [section, sort, showRejected]);

  const loadCounts = useCallback(async (): Promise<void> => {
    const entries = await Promise.all(
      SECTIONS.map(async (s) => {
        try {
          const res = await api.deals(s.id, { limit: 200 });
          return [s.id, res.deals.length] as const;
        } catch {
          return [s.id, 0] as const;
        }
      }),
    );
    setCounts(Object.fromEntries(entries) as Record<Section, number>);
  }, []);

  const loadEverything = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [statusRes, profilesRes, settingsRes] = await Promise.all([
        api.status(),
        api.profiles(),
        api.settings(),
      ]);
      setStatus(statusRes);
      setProfiles(profilesRes.profiles);
      setSettings(settingsRes);
      await Promise.all([loadDeals(), loadCounts()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [loadDeals, loadCounts]);

  useEffect(() => {
    void registerServiceWorker();
    void loadEverything();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadDeals();
  }, [loadDeals]);

  // Keep the feed live without a websocket: the poller's cadence is the floor
  // on how fresh anything can be anyway.
  useEffect(() => {
    const id = setInterval(() => {
      void loadDeals();
      void loadCounts();
      void api.status().then(setStatus).catch(() => undefined);
    }, 30_000);
    return () => clearInterval(id);
  }, [loadDeals, loadCounts]);

  const sweepNow = async (): Promise<void> => {
    setSweeping(true);
    try {
      await api.sweep();
      await Promise.all([loadDeals(), loadCounts()]);
      setStatus(await api.status());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSweeping(false);
    }
  };

  const hideDeal = async (key: string): Promise<void> => {
    setDeals((d) => d.filter((x) => x.key !== key));
    await api.hideDeal(key).catch(() => undefined);
  };

  const saveProfile = async (patch: Partial<Profile>): Promise<void> => {
    if (!editing) return;
    const saved =
      editing.id === 0
        ? await api.createProfile({ ...patch, section: editing.section })
        : await api.updateProfile(editing.id, patch);
    setProfiles((all) =>
      editing.id === 0 ? [...all, saved] : all.map((p) => (p.id === saved.id ? saved : p)),
    );
    setEditing(null);
  };

  const deleteProfile = async (): Promise<void> => {
    if (!editing) return;
    if (editing.id !== 0) {
      await api.deleteProfile(editing.id);
      setProfiles((all) => all.filter((p) => p.id !== editing.id));
    }
    setEditing(null);
  };

  const sectionProfiles = profiles.filter((p) => p.section === section);
  const noChannels = status !== null && status.notifications.channels.length === 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">🔎</span>
            Scavenger
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {sweeping && <span className="spinner" aria-label="Sweeping" />}
            <button className="btn btn-sm" onClick={sweepNow} disabled={sweeping}>
              {sweeping ? 'Scanning…' : 'Scan now'}
            </button>
          </div>
        </div>

        <nav className="tabs" role="tablist">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              className="tab"
              role="tab"
              aria-selected={section === s.id}
              onClick={() => {
                setSection(s.id);
                setEditing(null);
              }}
            >
              <span aria-hidden="true">{s.icon}</span> {s.label}
              <span className="count">{counts[s.id]}</span>
            </button>
          ))}
        </nav>
      </header>

      {view === 'deals' && (
        <div className="toolbar">
          {SORTS.map((s) => (
            <button
              key={s.id}
              className="chip"
              data-active={sort === s.id}
              onClick={() => setSort(s.id)}
            >
              {s.label}
            </button>
          ))}
          <button
            className="chip"
            data-active={showRejected}
            onClick={() => setShowRejected((v) => !v)}
            title="Include listings that did not clear your thresholds"
          >
            {showRejected ? '✓ ' : ''}All seen
          </button>
        </div>
      )}

      <main className="content">
        {error && (
          <div className="notice" data-tone="bad">
            {error}
          </div>
        )}

        {noChannels && view === 'deals' && (
          <div className="notice" data-tone="warn">
            No notification channel is set up yet, so nothing will reach your phone.{' '}
            <button className="btn btn-sm" onClick={() => setView('settings')}>
              Set it up
            </button>
          </div>
        )}

        {loading && deals.length === 0 && (
          <div className="empty">
            <div className="big">
              <span className="spinner" />
            </div>
            Loading…
          </div>
        )}

        {view === 'deals' &&
          !loading &&
          (deals.length === 0 ? (
            <div className="empty">
              <div className="big">🕳️</div>
              <p>
                Nothing in {SECTIONS.find((s) => s.id === section)?.label.toLowerCase()} yet.
              </p>
              <p style={{ fontSize: 13 }}>
                Deals appear as sellers post them. Widen your search terms or lower the profit
                floor if it stays quiet.
              </p>
              <button className="btn" onClick={() => setView('searches')}>
                Edit searches
              </button>
            </div>
          ) : (
            deals.map((deal) => <DealCard key={deal.key} deal={deal} onHide={hideDeal} />)
          ))}

        {view === 'searches' && (
          <>
            {editing ? (
              <ProfileEditor
                profile={editing}
                sources={status?.sources ?? []}
                onSave={saveProfile}
                onDelete={deleteProfile}
                onCancel={() => setEditing(null)}
              />
            ) : (
              <>
                <section className="panel">
                  <h2>Searches in {SECTIONS.find((s) => s.id === section)?.label}</h2>
                  {sectionProfiles.length === 0 && (
                    <p style={{ color: 'var(--text-dim)', fontSize: 13, margin: 0 }}>
                      No searches here yet.
                    </p>
                  )}
                  {sectionProfiles.map((p) => (
                    <div key={p.id} className="profile-row">
                      <div className="name">
                        <strong>{p.name}</strong>
                        <small>
                          {p.keywords.length} terms · {p.radiusMi} mi · min ${p.minProfit} ·{' '}
                          {p.includeBroken ? 'incl. broken' : 'working only'}
                          {!p.notify && ' · muted'}
                        </small>
                      </div>
                      <button
                        className="btn btn-sm"
                        onClick={() =>
                          void api
                            .updateProfile(p.id, { enabled: !p.enabled })
                            .then((u) =>
                              setProfiles((all) => all.map((x) => (x.id === u.id ? u : x))),
                            )
                        }
                      >
                        {p.enabled ? 'On' : 'Off'}
                      </button>
                      <button className="btn btn-sm" onClick={() => setEditing(p)}>
                        Edit
                      </button>
                    </div>
                  ))}
                  <button
                    className="btn btn-primary"
                    onClick={() => setEditing(blankProfile(section))}
                  >
                    + New search
                  </button>
                </section>

                <div className="notice">
                  A search runs every term against every enabled source, then keeps only what
                  clears your profit, ROI and confidence floors. Broad terms with tight floors beat
                  narrow terms with loose ones.
                </div>
              </>
            )}
          </>
        )}

        {view === 'settings' && (
          <SettingsView
            status={status}
            settings={settings}
            onSettingsChange={setSettings}
            onRefresh={() => void api.status().then(setStatus).catch(() => undefined)}
          />
        )}
      </main>

      <nav className="bottom-nav">
        {(
          [
            { id: 'deals', icon: '⚡', label: 'Deals' },
            { id: 'searches', icon: '🎯', label: 'Searches' },
            { id: 'settings', icon: '⚙️', label: 'Settings' },
          ] as const
        ).map((item) => (
          <button
            key={item.id}
            aria-current={view === item.id}
            onClick={() => {
              setView(item.id);
              setEditing(null);
            }}
          >
            <span className="nav-icon" aria-hidden="true">
              {item.icon}
            </span>
            {item.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
