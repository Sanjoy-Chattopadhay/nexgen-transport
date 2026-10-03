/**
 * How fresh the figures on screen are, and keeping them fresh.
 *
 * The background scheduler refreshes the published run every few minutes
 * (pipeline/scheduler.py) and moves the data version. This provider polls
 * the cheap status endpoint once a minute; when the version moves, every
 * `useApi` on the page refetches -- quietly, keeping what is on screen until
 * the new answer arrives -- so an open page is never more than a minute and
 * one refresh behind, and nobody has to press reload.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from './api';

export interface Freshness {
  /** Counts the times the published figures changed while the app was open. */
  epoch: number;
  status: any | null;
  /** Ask the scheduler for a pass now. */
  requestRefresh: () => Promise<void>;
  requesting: boolean;
}

const Ctx = createContext<Freshness>({
  epoch: 0, status: null, requestRefresh: async () => undefined, requesting: false,
});

const POLL_MS = 60_000;
const POLL_BUSY_MS = 10_000;

export function FreshnessProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<any | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [requesting, setRequesting] = useState(false);
  const seen = useRef<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const s = await api.status();
      setStatus(s);
      const run = s?.published_run;
      const v = `${run?.i_run_id ?? ''}:${run?.dt_summarised ?? ''}:${s?.refresh?.data_version ?? ''}`;
      // The first answer only records the version: pages already fetched
      // what is current. A later change refetches them.
      if (seen.current !== null && seen.current !== v) setEpoch(e => e + 1);
      seen.current = v;
    } catch { /* keep the last status */ }
  }, []);

  const busy = !!(status?.refresh?.running || status?.refresh?.requested || requesting);
  useEffect(() => {
    poll();
    const id = window.setInterval(poll, busy ? POLL_BUSY_MS : POLL_MS);
    const onFocus = () => poll();
    window.addEventListener('focus', onFocus);
    return () => { window.clearInterval(id); window.removeEventListener('focus', onFocus); };
  }, [poll, busy]);

  const requestRefresh = useCallback(async () => {
    setRequesting(true);
    try {
      await api.requestRefresh();
      await poll();
    } finally {
      window.setTimeout(() => setRequesting(false), 15_000);
    }
  }, [poll]);

  const value = useMemo(() => ({ epoch, status, requestRefresh, requesting }),
    [epoch, status, requestRefresh, requesting]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useFreshness = () => useContext(Ctx);
