import { createRequire } from 'node:module';
import type { RepairEstimate } from '../types.js';
import { containsAnyPositive, containsPhrase, normalize } from '../util/text.js';
import { round2 } from '../util/geo.js';

const require = createRequire(import.meta.url);
const rulesFile = require('../../data/repair-rules.json') as RulesFile;

interface PartsRange {
  low: number;
  typical: number;
  high: number;
}
interface DeviceClass {
  id: string;
  label: string;
  /** Fraction of working value recovered when sold for parts. */
  partsOutPct: number;
  match: string[];
}
interface RepairRule {
  id: string;
  deviceClass: string;
  symptoms: string[];
  parts: PartsRange;
  confidence: number;
  partsOnly?: boolean;
  note: string;
}
interface RulesFile {
  conditionSignals: Record<'partsOnly' | 'broken' | 'untested' | 'working', string[]>;
  deviceClasses: DeviceClass[];
  rules: RepairRule[];
  classFallbacks: { deviceClass: string; parts: PartsRange; confidence: number }[];
  genericFallback: { parts: PartsRange; confidence: number };
}

export interface ConditionRead {
  isBroken: boolean;
  isUntested: boolean;
  sellerSaysPartsOnly: boolean;
  saysWorking: boolean;
  signals: string[];
}

/** What the seller told us about condition, in their own words. */
export function readCondition(text: string): ConditionRead {
  const s = rulesFile.conditionSignals;
  const partsOnly = containsAnyPositive(text, s.partsOnly);
  const broken = containsAnyPositive(text, s.broken);
  const untested = containsAnyPositive(text, s.untested);
  const working = containsAnyPositive(text, s.working);

  return {
    sellerSaysPartsOnly: partsOnly.length > 0,
    // "works great" does not cancel "cracked screen" - damage wins.
    isBroken: partsOnly.length > 0 || broken.length > 0,
    isUntested: untested.length > 0,
    saysWorking: working.length > 0 && broken.length === 0 && partsOnly.length === 0,
    signals: [...new Set([...partsOnly, ...broken, ...untested, ...working])],
  };
}

/**
 * Words that turn the *next* word into a symptom rather than a product noun.
 * Without this, "RTX 3080 - no display" classifies as a monitor.
 */
const SYMPTOM_PREFIXES = ['no', 'broken', 'cracked', 'bad', 'dead', 'blown', 'needs', 'missing'];

/**
 * Pick the device class by the *longest* matching phrase, so "tube amp" beats
 * "amp" and "gaming pc" beats "pc" regardless of rule-file ordering. Matches
 * that sit in symptom context are ignored.
 */
export function classifyDevice(text: string): DeviceClass | null {
  const hay = normalize(text);
  let best: DeviceClass | null = null;
  let bestLen = 0;
  for (const cls of rulesFile.deviceClasses) {
    for (const phrase of cls.match) {
      const p = normalize(phrase);
      if (p.length > bestLen && containsPhraseAsProduct(hay, p)) {
        best = cls;
        bestLen = p.length;
      }
    }
  }
  return best;
}

/** containsPhrase, but rejecting occurrences preceded by a symptom word. */
function containsPhraseAsProduct(hay: string, phrase: string): boolean {
  if (!containsPhrase(hay, phrase)) return false;
  const padded = ` ${hay} `;
  const needle = ` ${phrase} `;
  let from = 0;
  for (;;) {
    const at = padded.indexOf(needle, from);
    if (at === -1) return false;
    const before = padded.slice(0, at + 1).trim().split(' ');
    const prev = before[before.length - 1] ?? '';
    if (!SYMPTOM_PREFIXES.includes(prev)) return true;
    from = at + 1;
  }
}

/**
 * Every symptom and condition phrase the rule set knows about, longest first
 * so "no display" is removed before "display" would be.
 */
const ALL_SYMPTOM_PHRASES: string[] = [
  ...rulesFile.rules.flatMap((r) => r.symptoms),
  ...Object.values(rulesFile.conditionSignals).flat(),
]
  .map((p) => normalize(p))
  .filter((p, i, arr) => p.length > 0 && arr.indexOf(p) === i)
  .sort((a, b) => b.length - a.length);

/**
 * Remove fault language before building a comp query.
 *
 * Searching eBay for "marantz 2270 receiver one channel out" finds nothing;
 * searching for "marantz 2270 receiver" finds the market. The symptom is what
 * the repair estimate is for - it has no business in the valuation query.
 */
export function stripSymptoms(text: string): string {
  let out = ` ${normalize(text)} `;
  for (const phrase of ALL_SYMPTOM_PHRASES) {
    out = out.split(` ${phrase} `).join(' ');
  }
  return out.replace(/\s+/g, ' ').trim();
}

export function partsOutPct(text: string): number {
  return classifyDevice(text)?.partsOutPct ?? 0.25;
}

/**
 * Estimate the parts bill to make an item sellable. Labour is free by design:
 * the operator does their own board-level work.
 *
 * When several faults are described we charge the biggest repair in full and
 * 60% of each additional one - real repairs share teardown and consumables,
 * but two dead subsystems are still two bills.
 */
export function estimateRepair(title: string, description?: string | null): RepairEstimate {
  const text = `${title} ${description ?? ''}`;
  const condition = readCondition(text);
  const device = classifyDevice(text);
  const notes: string[] = [];

  if (condition.saysWorking && !condition.isBroken) {
    return {
      isBroken: false,
      partsCost: 0,
      partsLow: 0,
      partsHigh: 0,
      confidence: 0.8,
      symptoms: [],
      rulesApplied: [],
      partsOnly: false,
      sellerSaysPartsOnly: false,
      notes: ['Seller states it is working - no parts budgeted. Verify before paying.'],
    };
  }

  const matched: { rule: RepairRule; hits: string[] }[] = [];
  for (const rule of rulesFile.rules) {
    if (device && rule.deviceClass !== device.id) continue;
    const hits = containsAnyPositive(text, rule.symptoms);
    if (hits.length > 0) matched.push({ rule, hits });
  }

  if (matched.length > 0) {
    matched.sort((a, b) => b.rule.parts.typical - a.rule.parts.typical);
    let typical = 0;
    let low = 0;
    let high = 0;
    let confidence = 1;
    const symptoms: string[] = [];
    const rulesApplied: string[] = [];
    let partsOnly = false;

    matched.forEach(({ rule, hits }, i) => {
      const weight = i === 0 ? 1 : 0.6;
      typical += rule.parts.typical * weight;
      low += rule.parts.low * weight;
      high += rule.parts.high * weight;
      // Several independent guesses are not more certain than the worst one.
      confidence = Math.min(confidence, rule.confidence);
      symptoms.push(...hits);
      rulesApplied.push(rule.id);
      if (rule.partsOnly) partsOnly = true;
      notes.push(rule.note);
    });

    if (matched.length > 1) confidence *= 0.85;
    if (!description) confidence *= 0.85; // title-only diagnosis is thinner

    return {
      isBroken: true,
      partsCost: round2(typical),
      partsLow: round2(low),
      partsHigh: round2(high),
      confidence: clamp01(confidence),
      symptoms: [...new Set(symptoms)],
      rulesApplied,
      partsOnly,
      sellerSaysPartsOnly: condition.sellerSaysPartsOnly,
      notes: [...new Set(notes)],
    };
  }

  // Nothing specific matched. If the seller flagged it at all, budget the
  // class fallback; a vague "as-is" earns a big discount on confidence.
  if (condition.isBroken || condition.isUntested) {
    const fallback =
      (device && rulesFile.classFallbacks.find((f) => f.deviceClass === device.id)) ||
      rulesFile.genericFallback;
    const vague = condition.isUntested && !condition.isBroken;
    notes.push(
      vague
        ? 'Seller says untested with no symptom - the parts figure is a class average, not a diagnosis.'
        : 'Broken but no recognised symptom in the text. Ask the seller what it does before you drive out.',
    );
    return {
      isBroken: true,
      partsCost: fallback.parts.typical,
      partsLow: fallback.parts.low,
      partsHigh: fallback.parts.high,
      confidence: clamp01(fallback.confidence * (vague ? 0.6 : 1)),
      symptoms: condition.signals,
      rulesApplied: ['fallback'],
      partsOnly: false,
      sellerSaysPartsOnly: condition.sellerSaysPartsOnly,
      notes,
    };
  }

  // No condition language at all: assume working but stay unsure about it.
  return {
    isBroken: false,
    partsCost: 0,
    partsLow: 0,
    partsHigh: 0,
    confidence: 0.45,
    symptoms: [],
    rulesApplied: [],
    partsOnly: false,
    sellerSaysPartsOnly: false,
    notes: ['Condition not stated. Treated as working - confirm before buying.'],
  };
}

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, Math.round(n * 100) / 100));
}
