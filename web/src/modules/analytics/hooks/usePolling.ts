import { useState, useEffect, useRef } from 'react';

export function usePolling<T>(fetcher: () => Promise<{ data: T }>, intervalMs: number, enabled: boolean) {
  const [data, setData] = useState<T | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!enabled) {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }

    const poll = () => fetcher().then(res => setData(res.data)).catch(() => {});
    poll();
    timerRef.current = setInterval(poll, intervalMs);

    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [enabled, intervalMs]);

  return data;
}
