/**
 * Trip-class scoping (zonal / local) — the client half of
 * backend/app/core/trip_class.py.
 *
 * The two upstream eTrans feeds land in the same tables tagged by
 * `tta_trips.s_trip_class`, so switching between them is a FILTER, not a
 * different app. Unlike consignor scope — which lives in the URL path because
 * it is a tenancy boundary — the class filter is a view preference:
 *
 *   * held in module state so the axios interceptor can read it on every call,
 *   * mirrored to localStorage so a refresh keeps what you were looking at,
 *   * NOT in the URL, so it never has to be threaded through the hundreds of
 *     existing <Link>s.
 */

export type TripClass = 'zonal' | 'local';
/** null = every class (the default, and what every pre-existing URL means). */
export type TripClassValue = TripClass | null;

export const TRIP_CLASS_PARAM = 'trip_class';
const STORAGE_KEY = 'smarttruck.tripClass';

export const TRIP_CLASSES: { key: TripClass; label: string; short: string }[] = [
  { key: 'zonal', label: 'Zonal trips', short: 'Zonal' },
  { key: 'local', label: 'Local trips', short: 'Local' },
];

function isTripClass(v: unknown): v is TripClass {
  return v === 'zonal' || v === 'local';
}

/** Last selection, or null when never set / storage unavailable. */
export function readStoredTripClass(): TripClassValue {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return isTripClass(v) ? v : null;
  } catch {
    // Private mode / blocked site data — fall back to "all", never throw.
    return null;
  }
}

export function writeStoredTripClass(value: TripClassValue): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* non-fatal: the filter still applies for this session */
  }
}
