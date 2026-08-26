/**
 * Text helpers for turning a seller's title into a usable comp query and for
 * judging how well an eBay comp actually matches the item in front of us.
 *
 * Marketplace titles are noisy ("**MUST GO TODAY** RTX 3080 no display obo").
 * The signal is almost entirely in brand names and alphanumeric model tokens,
 * so those are what we keep and what we weight.
 */

/** Words that carry no product information in a classified-ad title. */
const NOISE = new Set([
  'obo', 'obro', 'firm', 'cash', 'only', 'pickup', 'pick', 'up', 'local', 'delivery',
  'must', 'go', 'today', 'asap', 'price', 'reduced', 'sale', 'selling', 'sell', 'new',
  'brand', 'like', 'excellent', 'great', 'good', 'condition', 'used', 'lightly', 'barely',
  'nice', 'clean', 'rare', 'vintage', 'read', 'description', 'text', 'call', 'no', 'lowball',
  'lowballers', 'scammers', 'trades', 'trade', 'free', 'the', 'a', 'an', 'and', 'or', 'for',
  'with', 'w', 'in', 'on', 'of', 'to', 'my', 'your', 'this', 'that', 'is', 'are', 'inch', 'in.',
  // Left behind when overlapping symptom phrases are stripped out.
  'out', 'but', 'still', 'needs', 'need', 'anymore', 'sometimes', 'now', 'then',
]);

/** Symptom words are stripped from comp queries but kept for repair matching. */
const SYMPTOM_WORDS = new Set([
  'broken', 'parts', 'repair', 'as-is', 'asis', 'untested', 'cracked', 'damaged', 'dead',
  'faulty', 'defective', 'not', 'working', 'works', 'doesnt', "doesn't", 'won', 'wont',
  "won't", 'issue', 'issues', 'problem', 'problems', 'fixer', 'project', 'salvage', 'spares',
]);

export function normalize(s: string): string {
  return s
    .toLowerCase()
    .replace(/[‘’]/g, "'")
    .replace(/[^a-z0-9'\-.+ ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function tokenize(s: string): string[] {
  return normalize(s).split(' ').filter(Boolean);
}

/** Tokens that mix letters and digits, or are pure multi-digit numbers. */
export function isModelToken(t: string): boolean {
  const hasDigit = /\d/.test(t);
  if (!hasDigit) return false;
  if (/^\d{1,2}$/.test(t)) return false; // "2", "16" alone are usually sizes
  if (/^(19|20)\d{2}$/.test(t)) return false; // years are handled separately
  return t.length >= 3;
}

export function modelTokens(s: string): string[] {
  return [...new Set(tokenize(s).filter(isModelToken))];
}

/** Four-digit years, useful for vehicles and for dating a TV/console. */
export function extractYear(s: string): number | null {
  const m = normalize(s).match(/\b(19[5-9]\d|20[0-4]\d)\b/);
  return m ? Number(m[1]) : null;
}

export function contentTokens(s: string): string[] {
  return tokenize(s).filter(
    (t) => t.length > 1 && !NOISE.has(t) && !SYMPTOM_WORDS.has(t),
  );
}

/**
 * Build the query we send to the comp source. Model tokens come first because
 * eBay's relevance ranking weights leading terms, then the most informative
 * remaining words in original order, capped so the search does not over-narrow.
 */
export function buildCompQuery(title: string, maxTerms = 6): string {
  const models = modelTokens(title);
  const words = contentTokens(title).filter((t) => !isModelToken(t));
  const picked: string[] = [];
  for (const t of [...models, ...words]) {
    if (picked.includes(t)) continue;
    picked.push(t);
    if (picked.length >= maxTerms) break;
  }
  return picked.join(' ');
}

/**
 * 0..1 similarity between the source title and a comp title.
 *
 * Model tokens dominate: an "RTX 3080" comp matching an "RTX 3080" listing is
 * a real comp, while a "graphics card" comp for the same listing is noise.
 */
export function titleSimilarity(a: string, b: string): number {
  const aModels = new Set(modelTokens(a));
  const bModels = new Set(modelTokens(b));
  const aWords = new Set(contentTokens(a));
  const bWords = new Set(contentTokens(b));

  const overlap = <T>(x: Set<T>, y: Set<T>): number => {
    if (x.size === 0 || y.size === 0) return 0;
    let hits = 0;
    for (const v of x) if (y.has(v)) hits += 1;
    return hits / Math.min(x.size, y.size);
  };

  const wordScore = overlap(aWords, bWords);
  if (aModels.size === 0) return wordScore;

  const modelScore = overlap(aModels, bModels);
  // A comp that shares no model token with a model-bearing listing is weak
  // no matter how many generic words it shares.
  return modelScore === 0 ? wordScore * 0.35 : 0.7 * modelScore + 0.3 * wordScore;
}

/** Does the haystack contain the phrase as whole words? */
export function containsPhrase(haystack: string, phrase: string): boolean {
  const h = ` ${normalize(haystack)} `;
  const p = ` ${normalize(phrase)} `;
  return h.includes(p);
}

export function containsAny(haystack: string, phrases: string[]): string[] {
  return phrases.filter((p) => containsPhrase(haystack, p));
}

/** Words that flip the meaning of the phrase that follows them. */
const NEGATORS = new Set(['no', 'not', 'never', 'without', 'zero']);

/**
 * containsPhrase, ignoring negated occurrences: "no artifacts" must not count
 * as the "artifacts" symptom. A phrase that begins with its own negator
 * ("no display", "not working") is exempt - the negation is the symptom.
 */
export function containsPhrasePositive(haystack: string, phrase: string): boolean {
  const p = normalize(phrase);
  if (!p) return false;
  const firstWord = p.split(' ')[0] ?? '';
  if (NEGATORS.has(firstWord)) return containsPhrase(haystack, p);

  const padded = ` ${normalize(haystack)} `;
  const needle = ` ${p} `;
  let from = 0;
  for (;;) {
    const at = padded.indexOf(needle, from);
    if (at === -1) return false;
    const before = padded.slice(0, at + 1).trim().split(' ');
    const prev = before[before.length - 1] ?? '';
    if (!NEGATORS.has(prev)) return true;
    from = at + 1;
  }
}

export function containsAnyPositive(haystack: string, phrases: string[]): string[] {
  return phrases.filter((p) => containsPhrasePositive(haystack, p));
}
