import { useState } from 'react';
import type { Profile, Section, SourceStatus } from '../types';
import { TokenInput } from './TokenInput';

const numberOrNull = (v: string): number | null => {
  if (v.trim() === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

interface Props {
  profile: Profile;
  sources: SourceStatus[];
  onSave: (patch: Partial<Profile>) => Promise<void>;
  onDelete: () => Promise<void>;
  onCancel: () => void;
}

export function ProfileEditor({ profile, sources, onSave, onDelete, onCancel }: Props): JSX.Element {
  const [draft, setDraft] = useState<Profile>(profile);
  const [saving, setSaving] = useState(false);

  const set = <K extends keyof Profile>(key: K, value: Profile[K]): void =>
    setDraft((d) => ({ ...d, [key]: value }));

  const save = async (): Promise<void> => {
    setSaving(true);
    try {
      await onSave({
        name: draft.name,
        enabled: draft.enabled,
        keywords: draft.keywords,
        mustInclude: draft.mustInclude,
        exclude: draft.exclude,
        minPrice: draft.minPrice,
        maxPrice: draft.maxPrice,
        radiusMi: draft.radiusMi,
        minProfit: draft.minProfit,
        minRoi: draft.minRoi,
        includeBroken: draft.includeBroken,
        minConfidence: draft.minConfidence,
        notify: draft.notify,
        sources: draft.sources,
      });
    } finally {
      setSaving(false);
    }
  };

  const toggleSource = (id: string): void => {
    const current = draft.sources ?? sources.filter((s) => s.enabled).map((s) => s.id);
    const next = current.includes(id) ? current.filter((s) => s !== id) : [...current, id];
    set('sources', next.length === 0 ? null : next);
  };

  return (
    <div className="panel">
      <div className="field">
        <label>Search name</label>
        <input type="text" value={draft.name} onChange={(e) => set('name', e.target.value)} />
      </div>

      <TokenInput
        label="Search terms"
        hint="Each term is searched separately on every source. Broad beats clever — the scoring does the filtering."
        values={draft.keywords}
        placeholder="e.g. rtx 3080, tube amp, dryer no heat"
        onChange={(v) => set('keywords', v)}
      />

      <TokenInput
        label="Must contain"
        hint="Drop anything that does not mention all of these."
        values={draft.mustInclude}
        onChange={(v) => set('mustInclude', v)}
      />

      <TokenInput
        label="Never show"
        hint="Drop anything mentioning any of these. Good for 'wanted', 'iso', 'repair service'."
        values={draft.exclude}
        onChange={(v) => set('exclude', v)}
      />

      <div className="grid-2">
        <div className="field">
          <label>Min ask price ($)</label>
          <input
            type="number"
            inputMode="decimal"
            value={draft.minPrice ?? ''}
            placeholder="any"
            onChange={(e) => set('minPrice', numberOrNull(e.target.value))}
          />
        </div>
        <div className="field">
          <label>Max ask price ($)</label>
          <input
            type="number"
            inputMode="decimal"
            value={draft.maxPrice ?? ''}
            placeholder="any"
            onChange={(e) => set('maxPrice', numberOrNull(e.target.value))}
          />
        </div>
        <div className="field">
          <label>Search radius (mi)</label>
          <input
            type="number"
            inputMode="decimal"
            value={draft.radiusMi}
            onChange={(e) => set('radiusMi', Number(e.target.value) || 1)}
          />
        </div>
        <div className="field">
          <label>Min profit ($)</label>
          <input
            type="number"
            inputMode="decimal"
            value={draft.minProfit}
            onChange={(e) => set('minProfit', Number(e.target.value) || 0)}
          />
        </div>
        <div className="field">
          <label>Min ROI</label>
          <input
            type="number"
            step="0.05"
            inputMode="decimal"
            value={draft.minRoi}
            onChange={(e) => set('minRoi', Number(e.target.value) || 0)}
          />
          <span className="hint">0.5 = make back 50% of cash spent</span>
        </div>
        <div className="field">
          <label>Min confidence</label>
          <input
            type="number"
            step="0.05"
            min="0"
            max="1"
            inputMode="decimal"
            value={draft.minConfidence}
            onChange={(e) => set('minConfidence', Number(e.target.value) || 0)}
          />
          <span className="hint">Hide guesses below this</span>
        </div>
      </div>

      <div>
        <label style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 550 }}>Sources</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 5 }}>
          {sources
            .filter((s) => s.enabled)
            .map((s) => {
              const active = draft.sources === null || draft.sources.includes(s.id);
              return (
                <button
                  key={s.id}
                  type="button"
                  className="chip"
                  data-active={active}
                  onClick={() => toggleSource(s.id)}
                >
                  {active ? '✓ ' : ''}
                  {s.label}
                </button>
              );
            })}
        </div>
      </div>

      <div>
        <div className="switch">
          <span>Include broken / for-parts listings</span>
          <input
            type="checkbox"
            checked={draft.includeBroken}
            onChange={(e) => set('includeBroken', e.target.checked)}
          />
        </div>
        <div className="switch">
          <span>Push a notification for matches</span>
          <input
            type="checkbox"
            checked={draft.notify}
            onChange={(e) => set('notify', e.target.checked)}
          />
        </div>
        <div className="switch">
          <span>Search enabled</span>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
          />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={save} disabled={saving} style={{ flex: 1 }}>
          {saving ? 'Saving…' : 'Save search'}
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-danger" onClick={onDelete}>
          Delete
        </button>
      </div>
    </div>
  );
}

export function blankProfile(section: Section): Profile {
  return {
    id: 0,
    section,
    name: 'New search',
    enabled: true,
    keywords: [],
    mustInclude: [],
    exclude: ['wanted', 'looking for', 'iso'],
    minPrice: null,
    maxPrice: null,
    radiusMi: 30,
    minProfit: section === 'vehicles' ? 800 : 60,
    minRoi: 0.5,
    includeBroken: true,
    minConfidence: 0.35,
    notify: true,
    sources: null,
    createdAt: 0,
    updatedAt: 0,
  };
}
