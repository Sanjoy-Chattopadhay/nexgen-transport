import { backendApi } from './api';

/** Build a date-range params object, omitting empty bounds. */
function dp(from?: string, to?: string): Record<string, string> {
  const p: Record<string, string> = {};
  if (from) p.date_from = from;
  if (to) p.date_to = to;
  return p;
}

export const getFleetSummary = (from?: string, to?: string) =>
  backendApi.get('/dashboard/summary', { params: dp(from, to) });
export const getDailyTrend = (days = 30, from?: string, to?: string) =>
  backendApi.get('/dashboard/daily-trend', { params: { days, ...dp(from, to) } });
export const getTopDrivers = (limit = 10, from?: string, to?: string) =>
  backendApi.get('/dashboard/top-drivers', { params: { limit, ...dp(from, to) } });
export const getRouteHeatmap = (limit = 20, from?: string, to?: string) =>
  backendApi.get('/dashboard/route-heatmap', { params: { limit, ...dp(from, to) } });
export const getRecentAlerts = (limit = 10) =>
  backendApi.get('/dashboard/alerts/recent', { params: { limit } });

// ── Drill-down lists (date-aware, consignor-scoped) ──────────────────────────
export const getActiveDrivers = (from?: string, to?: string, limit = 200) =>
  backendApi.get('/dashboard/active-drivers', { params: { limit, ...dp(from, to) } });
export const getActiveVehicles = (from?: string, to?: string, limit = 200) =>
  backendApi.get('/dashboard/active-vehicles', { params: { limit, ...dp(from, to) } });
export const getRecentTrips = (from?: string, to?: string, limit = 200) =>
  backendApi.get('/dashboard/recent-trips', { params: { limit, ...dp(from, to) } });
