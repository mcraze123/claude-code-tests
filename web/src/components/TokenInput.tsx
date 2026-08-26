import { useState } from 'react';

interface Props {
  label: string;
  hint?: string;
  values: string[];
  placeholder?: string;
  onChange: (next: string[]) => void;
}

/**
 * Chip-style list editor for keyword / include / exclude terms. Accepts
 * comma-separated paste so a long list can be dropped in at once.
 */
export function TokenInput({ label, hint, values, placeholder, onChange }: Props): JSX.Element {
  const [draft, setDraft] = useState('');

  const commit = (raw: string): void => {
    const additions = raw
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter((s) => s.length > 0 && !values.includes(s));
    if (additions.length > 0) onChange([...values, ...additions]);
    setDraft('');
  };

  return (
    <div className="field">
      <label>
        {label} <span style={{ color: 'var(--text-faint)' }}>({values.length})</span>
      </label>

      {values.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
          {values.map((v) => (
            <span key={v} className="chip" data-active="true">
              {v}
              <button
                type="button"
                onClick={() => onChange(values.filter((x) => x !== v))}
                aria-label={`Remove ${v}`}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      <input
        type="text"
        value={draft}
        placeholder={placeholder ?? 'Type a term, press Enter'}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit(draft);
          } else if (e.key === 'Backspace' && draft === '' && values.length > 0) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={() => commit(draft)}
      />
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}
