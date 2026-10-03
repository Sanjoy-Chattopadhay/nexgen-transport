import { useEffect, useState } from 'react';

const STORAGE_KEY = 'st.transporter.minTrips.v1';

/** Matches DEFAULT_MIN_TRIPS in backend/app/services/transporter_insights.py. */
export const DEFAULT_MIN_TRIPS = 3;

/**
 * Trips a carrier needs before it gets scored, graded and ranked.
 *
 * Persisted and shared, so the league table and every carrier profile agree on
 * who qualifies — a carrier ranked #2 in the table must not read "Unranked" on
 * its own page.
 */
export function useMinTrips() {
  const [minTrips, setMinTrips] = useState<number>(() => {
    try {
      const raw = Number(localStorage.getItem(STORAGE_KEY));
      if (Number.isFinite(raw) && raw >= 1) return raw;
    } catch { /* ignore unavailable storage */ }
    return DEFAULT_MIN_TRIPS;
  });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, String(minTrips)); } catch { /* ignore */ }
  }, [minTrips]);

  return { minTrips, setMinTrips };
}
