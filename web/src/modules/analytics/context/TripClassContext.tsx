import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode,
} from 'react';
import { backendApi, setActiveTripClass } from '../services/api';
import {
  readStoredTripClass, writeStoredTripClass, type TripClassValue,
} from '../lib/tripClass';

export interface TripClassCount {
  key: 'zonal' | 'local';
  label: string;
  description: string;
  trips: number;
}

interface TripClassContextValue {
  /** Active class filter, or null for "all classes". */
  tripClass: TripClassValue;
  /** Trip counts per class, for the filter chips. */
  counts: TripClassCount[];
  total: number;
  loading: boolean;
  /** Switch the filter. Every page below refetches. */
  setTripClass: (value: TripClassValue) => void;
  /**
   * Bumped on every change. App uses it as a React `key` so the routed subtree
   * remounts and every useApi/usePolling call re-runs — the alternative would be
   * threading a dependency through every hook call site in the app.
   */
  epoch: number;
}

const TripClassContext = createContext<TripClassContextValue | undefined>(undefined);

export function TripClassProvider({ children }: { children: ReactNode }) {
  const [tripClass, setValue] = useState<TripClassValue>(() => readStoredTripClass());
  const [counts, setCounts] = useState<TripClassCount[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [epoch, setEpoch] = useState(0);

  // Keep the API layer in sync before any child renders/fetches.
  useEffect(() => { setActiveTripClass(tripClass); }, [tripClass]);

  // Counts are deliberately fetched UNSCOPED by class (the endpoint groups by
  // class), so each chip always shows its own total rather than 0 for whichever
  // class is currently filtered out.
  useEffect(() => {
    let cancelled = false;
    backendApi
      .get<{ classes: TripClassCount[]; total: number }>('/tta/trip-classes')
      .then(res => {
        if (cancelled) return;
        setCounts(res.data.classes || []);
        setTotal(res.data.total || 0);
      })
      .catch(() => { if (!cancelled) setCounts([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [epoch]);

  const setTripClass = useCallback((next: TripClassValue) => {
    setValue(prev => {
      if (prev === next) return prev;
      setActiveTripClass(next);   // set before the remount so refetches use it
      writeStoredTripClass(next);
      setEpoch(e => e + 1);
      return next;
    });
  }, []);

  const value = useMemo<TripClassContextValue>(
    () => ({ tripClass, counts, total, loading, setTripClass, epoch }),
    [tripClass, counts, total, loading, setTripClass, epoch],
  );

  return <TripClassContext.Provider value={value}>{children}</TripClassContext.Provider>;
}

export function useTripClass() {
  const ctx = useContext(TripClassContext);
  if (!ctx) throw new Error('useTripClass must be used within a TripClassProvider');
  return ctx;
}
