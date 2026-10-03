import { useCallback, useEffect, useRef, useState } from 'react';
import { useFreshness } from '../lib/freshness';

/**
 * Fetch on mount and whenever `deps` change. A response that arrives after
 * the deps have moved on is discarded, so a slow page-2 request can never
 * overwrite the page-3 table the user is looking at.
 *
 * Also refetches when the background refresh changes the published figures
 * (lib/freshness.tsx), keeping the current answer on screen meanwhile.
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const { epoch } = useFreshness();

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fetcher, [...deps, epoch]);

  const reload = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    run()
      .then(d => { if (mine === seq.current) setData(d); })
      .catch(e => { if (mine === seq.current) setError(e?.message || 'Failed to load'); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, [run]);

  useEffect(() => { reload(); }, [reload]);

  return { data, loading, error, reload };
}

/** Poll a fetcher on an interval while the component is mounted. */
export function usePolling<T>(fetcher: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => fetcher()
      .then(d => { if (alive) { setData(d); setError(null); } })
      .catch(e => { if (alive) setError(e?.message || 'Failed'); });
    tick();
    const id = window.setInterval(tick, ms);
    return () => { alive = false; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
  return { data, error };
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}
