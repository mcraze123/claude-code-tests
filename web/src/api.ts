import type { Deal, Profile, Section, Settings, SortKey, Status } from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail.slice(0, 200)}` : ''}`);
  }
  return (await res.json()) as T;
}

export const api = {
  status: (): Promise<Status> => request('/status'),

  deals: (
    section: Section,
    opts: { sort?: SortKey; includeRejected?: boolean; limit?: number } = {},
  ): Promise<{ deals: Deal[] }> => {
    const params = new URLSearchParams({ section });
    if (opts.sort) params.set('sort', opts.sort);
    if (opts.includeRejected) params.set('includeRejected', 'true');
    if (opts.limit) params.set('limit', String(opts.limit));
    return request(`/deals?${params}`);
  },

  hideDeal: (key: string): Promise<{ ok: boolean }> =>
    request(`/deals/${encodeURIComponent(key)}/hide`, {
      method: 'POST',
      body: JSON.stringify({ hidden: true }),
    }),

  profiles: (): Promise<{ profiles: Profile[] }> => request('/profiles'),

  createProfile: (body: Partial<Profile>): Promise<Profile> =>
    request('/profiles', { method: 'POST', body: JSON.stringify(body) }),

  updateProfile: (id: number, body: Partial<Profile>): Promise<Profile> =>
    request(`/profiles/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  deleteProfile: (id: number): Promise<{ ok: boolean }> =>
    request(`/profiles/${id}`, { method: 'DELETE' }),

  settings: (): Promise<Settings> => request('/settings'),

  updateSettings: (body: Partial<Settings>): Promise<Settings> =>
    request('/settings', { method: 'PATCH', body: JSON.stringify(body) }),

  setLocation: (lat: number, lon: number): Promise<Settings> =>
    request('/location', { method: 'POST', body: JSON.stringify({ lat, lon }) }),

  sweep: (): Promise<{ dealsFound: number; newListings: number; errors: string[] }> =>
    request('/sweep', { method: 'POST' }),

  pushKey: (): Promise<{ publicKey: string | null; channels: string[] }> => request('/push/key'),

  subscribe: (sub: PushSubscriptionJSON): Promise<{ ok: boolean }> =>
    request('/push/subscribe', { method: 'POST', body: JSON.stringify(sub) }),

  testPush: (key?: string): Promise<{ delivered: boolean; channels: string[]; errors: string[] }> =>
    request('/push/test', { method: 'POST', body: JSON.stringify({ key }) }),
};
