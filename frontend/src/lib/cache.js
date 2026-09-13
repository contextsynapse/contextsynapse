/**
 * PMS Session Cache — stale-while-revalidate pattern.
 * Uses sessionStorage so cache clears on tab close.
 */
const PREFIX = 'pms_c_';

export const cache = {
  get(key) {
    try {
      const raw = sessionStorage.getItem(PREFIX + key);
      if (!raw) return null;
      const { data, exp } = JSON.parse(raw);
      if (Date.now() > exp) return { data, stale: true };
      return { data, stale: false };
    } catch { return null; }
  },

  set(key, data, ttlMs = 120000) {
    try {
      sessionStorage.setItem(PREFIX + key, JSON.stringify({ data, exp: Date.now() + ttlMs }));
    } catch {}
  },

  remove(key) {
    sessionStorage.removeItem(PREFIX + key);
  },

  /** Get cached value or fetch fresh. Shows cached instantly, refreshes in background if stale. */
  async getOrFetch(key, fetchFn, { ttlMs = 120000, onData } = {}) {
    const cached = cache.get(key);
    if (cached) {
      onData?.(cached.data);
      if (!cached.stale) return cached.data;
    }
    const data = await fetchFn();
    cache.set(key, data, ttlMs);
    onData?.(data);
    return data;
  },
};
