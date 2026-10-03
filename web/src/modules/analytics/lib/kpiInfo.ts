/**
 * Central copy for the KPI "eye" explainers. Each entry says, in plain language,
 * what the metric means and exactly how it is computed. Keeping it here keeps the
 * wording consistent across the Dashboard, the analytics suite and Waypoints.
 */
export interface KpiInfo {
  what: string;
  formula?: string;
}

export const KPI_INFO = {
  // ── Main Dashboard ──────────────────────────────────────────────
  totalTrips: {
    what: 'Trips that departed within the selected date range.',
    formula: 'COUNT(trips) WHERE trip_start ∈ [from, to]',
  },
  activeDrivers: {
    what: 'Distinct drivers who ran at least one trip in the selected range.',
    formula: 'COUNT(DISTINCT driver_id)',
  },
  vehicles: {
    what: 'Distinct vehicles that ran at least one trip in the selected range.',
    formula: 'COUNT(DISTINCT vehicle_id)',
  },
  totalDistance: {
    what: 'Total kilometres covered by all trips in the range.',
    formula: 'SUM(trip_km)',
  },
  avgSpeed: {
    what: 'Average of each trip’s own average speed (not distance ÷ time).',
    formula: 'AVG(trip.avg_speed_kmph)',
  },
  etaSuccess: {
    what: 'Share of trips that arrived on or before their promised ETA.',
    formula: 'SUM(eta_met = 1) ÷ COUNT(*) × 100',
  },

  // ── Analytics · Executive Overview ──────────────────────────────
  otd: {
    what: 'On-time delivery — share of delivered trips that arrived by their ETA.',
    formula: 'on_time_deliveries ÷ delivered_trips × 100',
  },
  avgTransit: {
    what: 'Average door-to-door transit time, from plant dispatch to destination arrival.',
    formula: 'AVG(ata_dt − dept_dt) in hours',
  },
  medianTransit: {
    what: 'Median transit time — half of trips are faster, half slower. Robust to outliers.',
    formula: 'MEDIAN(ata_dt − dept_dt) in hours',
  },
  totalKm: {
    what: 'Total distance across all filtered trips.',
    formula: 'SUM(distance_km)',
  },
  transporters: {
    what: 'Distinct transporters (carriers) appearing in the filtered window.',
    formula: 'COUNT(DISTINCT transporter)',
  },
  avgDetention: {
    what: 'Average time trucks sat detained at plant/destination (loading & unloading waits).',
    formula: 'AVG(detention_hours)',
  },
  violationsPerTrip: {
    what: 'Average number of speed-limit violations recorded per trip.',
    formula: 'SUM(speed_violations) ÷ trips',
  },
  delayWhenLate: {
    what: 'Among trips that were late, the average size of the delay.',
    formula: 'AVG(ata_dt − eta_dt) for late trips, in hours',
  },
  plantVivo: {
    what: 'Average in-plant dwell time (arrival at plant to dispatch), as reported by the provider.',
    formula: 'AVG(plant_vivo_hours)',
  },
  dispatchLead: {
    what: 'Average lead time from booking to actual dispatch.',
    formula: 'AVG(dept_dt − booking_dt) in hours',
  },
  gpsUptime: {
    what: 'Average share of the journey with live GPS coverage (tracking quality).',
    formula: 'AVG(gps_uptime_pct)',
  },
  marketShare: {
    what: 'This consignor’s share of trips versus the full dataset.',
    formula: 'scoped_trips ÷ all_trips × 100',
  },

  // ── Waypoint Analysis ───────────────────────────────────────────
  waypointsRegistered: {
    what: 'Distinct waypoints ever seen in GPS data, each with an accumulated behaviour pattern.',
    formula: 'COUNT(DISTINCT waypoint)',
  },
  stopEvents: {
    what: 'Total number of discrete stop events recorded across all waypoints.',
    formula: 'SUM(stop_events)',
  },
  totalStandstill: {
    what: 'Total standstill (stopped) time accumulated across every waypoint.',
    formula: 'SUM(stopped_min)',
  },
  longestStop: {
    what: 'The single longest continuous stop recorded at any waypoint.',
    formula: 'MAX(longest_stop_min)',
  },
  worstWaypoint: {
    what: 'The waypoint with the most accumulated standstill time — the biggest time sink.',
    formula: 'waypoint with MAX(stopped_min)',
  },
} satisfies Record<string, KpiInfo>;

export type KpiInfoKey = keyof typeof KPI_INFO;
