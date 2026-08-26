const USER_AGENT =
  'Scavenger/0.1 (personal deal-finder; contact via the operator of this instance)';

export interface FetchOptions {
  timeoutMs?: number;
  retries?: number;
  headers?: Record<string, string>;
}

/** Fetch with a timeout and bounded exponential backoff on 5xx/network errors. */
export async function fetchText(url: string, opts: FetchOptions = {}): Promise<string> {
  const { timeoutMs = 15_000, retries = 2 } = opts;
  let lastError: unknown;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    if (attempt > 0) await sleep(500 * 2 ** (attempt - 1));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        signal: controller.signal,
        headers: { 'User-Agent': USER_AGENT, Accept: '*/*', ...opts.headers },
      });
      if (res.status === 429 || res.status >= 500) {
        lastError = new Error(`HTTP ${res.status} from ${hostOf(url)}`);
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status} from ${hostOf(url)}`);
      return await res.text();
    } catch (err) {
      lastError = err;
      if (err instanceof Error && err.name === 'AbortError') {
        lastError = new Error(`timeout after ${timeoutMs}ms from ${hostOf(url)}`);
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

export async function fetchJson<T>(url: string, opts: FetchOptions = {}): Promise<T> {
  const text = await fetchText(url, {
    ...opts,
    headers: { Accept: 'application/json', ...opts.headers },
  });
  return JSON.parse(text) as T;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

/**
 * Serialises calls to one host with a minimum gap between them. Classifieds
 * sites will happily rate-limit or ban a client that fans out in parallel.
 */
export class PoliteQueue {
  private chain: Promise<unknown> = Promise.resolve();
  private lastAt = 0;

  constructor(private readonly minGapMs: number) {}

  run<T>(task: () => Promise<T>): Promise<T> {
    const next = this.chain.then(async () => {
      const wait = this.minGapMs - (Date.now() - this.lastAt);
      if (wait > 0) await sleep(wait);
      try {
        return await task();
      } finally {
        this.lastAt = Date.now();
      }
    });
    // Keep the chain alive even when a task rejects.
    this.chain = next.catch(() => undefined);
    return next;
  }
}
