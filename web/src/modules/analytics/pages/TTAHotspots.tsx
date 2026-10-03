import { useState, useMemo } from 'react';
import {
  ShieldAlert, MapPin, Users, Clock, RefreshCw, Download, AlertTriangle,
  Truck, Moon, Crosshair, ExternalLink,
} from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import Spinner from '../components/ui/Spinner';
import DataTable from '../components/ui/DataTable';
import KPICard from '../components/ui/KPICard';
import Badge from '../components/ui/Badge';
import ChartCard from '../components/ui/ChartCard';
import DonutChart from '../components/charts/DonutChart';
import HotspotMap from '../components/tta/HotspotMap';
import { useApi } from '../hooks/useApi';
import { formatNumber, formatDateTime } from '../lib/formatters';
import { useDrillDown } from '../context/DrillDownContext';
import {
  getHotspots, getHotspotDetail, getHotspotStatus, refreshHotspots,
  extractStopEvents, labelHotspot, getHotspotStops,
  type Hotspot, type HotspotDetailResponse, type HotspotMember,
  type HotspotStopRecord, type HotspotRollup,
} from '../services/hotspots';
import { tc } from '../../../core/theme';

const LABEL_OPTIONS = [
  { value: 'investigating', label: 'Investigating', variant: 'warning' as const },
  { value: 'known_facility', label: 'Known facility', variant: 'neutral' as const },
  { value: 'rest_stop', label: 'Rest stop', variant: 'neutral' as const },
  { value: 'cleared', label: 'Cleared', variant: 'success' as const },
  { value: 'confirmed', label: 'Confirmed', variant: 'danger' as const },
];

const labelVariant = (status?: string) =>
  LABEL_OPTIONS.find(o => o.value === status)?.variant ?? 'neutral';

const satelliteUrl = (lat: number, lng: number) =>
  `https://www.google.com/maps/@${lat},${lng},350m/data=!3m1!1e3`;

function downloadCsv(filename: string, rows: Record<string, any>[]) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const esc = (v: any) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [cols.join(','), ...rows.map(r => cols.map(c => esc(r[c])).join(','))].join('\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Minutes are unreadable as a duration past an hour or so — "874 min" of
 *  standstill means nothing until it reads "14h 34m". */
function fmtMins(mins?: number | null): string {
  if (mins == null) return '—';
  const m = Math.round(Number(mins));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h}h ${r}m` : `${h}h`;
}

const pct = (v?: number | null) => `${Math.round(Number(v ?? 0) * 100)}%`;

/**
 * A count that opens its own evidence.
 *
 * Every number on this page is an aggregate over stop events, and an aggregate
 * nobody can open is one nobody can check. `stopPropagation` because these sit
 * inside rows that are themselves clickable — drilling a count must not also
 * select the row behind it.
 */
function DrillNum({ value, onClick, className = 'text-gray-200' }:
  { value: React.ReactNode; onClick: () => void; className?: string }) {
  return (
    <button
      onClick={e => { e.stopPropagation(); onClick(); }}
      title="Show the records behind this number"
      className={`${className} decoration-dotted underline underline-offset-4 decoration-gray-600 hover:decoration-current`}>
      {value}
    </button>
  );
}

/** A stop row from either source: a cluster's own members, or the corpus
 *  reader (which adds cluster context). The corpus-only fields are optional so
 *  one set of columns renders both. */
type StopRow = HotspotMember & Partial<HotspotStopRecord>;

/** 22:00–05:00, the same boundary the backend's night_share uses. `night`
 *  arrives pre-computed from the corpus reader; cluster members carry the hour
 *  only, so derive it there rather than showing two different definitions. */
const isNight = (r: StopRow) => r.night ?? (r.i_hour < 5 || r.i_hour >= 22);

/** Columns shared by every stop-level drill-down: when, how long, who. */
const STOP_COLUMNS = [
  { key: 'dt_start', label: 'When', render: (r: StopRow) => formatDateTime(r.dt_start) },
  {
    key: 'd_duration_min', label: 'Dwell', align: 'right' as const,
    render: (r: StopRow) => (
      <span className={Number(r.d_duration_min) >= 60 ? 'text-amber-400' : 'text-gray-300'}>
        {fmtMins(r.d_duration_min)}
      </span>
    ),
  },
  { key: 's_asset_id', label: 'Vehicle', render: (r: StopRow) => r.s_asset_id ?? '—' },
  {
    key: 's_trans_name', label: 'Carrier',
    render: (r: StopRow) => <span className="text-amber-400 text-xs">{r.s_trans_name ?? '—'}</span>,
  },
  {
    key: 'i_hour', label: 'Time of day', align: 'right' as const,
    render: (r: StopRow) => (
      <span className={isNight(r) ? 'text-indigo-400' : 'text-gray-400'}>
        {String(r.i_hour).padStart(2, '0')}:00{isNight(r) ? ' night' : ''}
      </span>
    ),
  },
  { key: 'route', label: 'Route', render: (r: StopRow) => <span className="text-xs text-gray-400">{r.route}</span> },
];

/** Columns for a per-vehicle / per-carrier rollup. */
function rollupColumns(keyField: 's_asset_id' | 's_trans_name', label: string) {
  return [
    { key: keyField, label, render: (r: HotspotRollup) => r[keyField] ?? '—' },
    { key: 'stops', label: 'Stops', align: 'right' as const },
    {
      key: 'total_dwell_min', label: 'Total halted', align: 'right' as const,
      render: (r: HotspotRollup) => (
        <span className="text-amber-400 font-medium">{fmtMins(r.total_dwell_min)}</span>
      ),
    },
    {
      key: 'median_dwell_min', label: 'Median', align: 'right' as const,
      render: (r: HotspotRollup) => fmtMins(r.median_dwell_min),
    },
    {
      key: 'longest_dwell_min', label: 'Longest', align: 'right' as const,
      render: (r: HotspotRollup) => fmtMins(r.longest_dwell_min),
    },
    {
      key: 'night_share', label: 'Night', align: 'right' as const,
      render: (r: HotspotRollup) => (
        <span className={r.night_share > 0.3 ? 'text-indigo-400' : 'text-gray-400'}>{pct(r.night_share)}</span>
      ),
    },
    {
      key: 'first_seen', label: 'First seen',
      render: (r: HotspotRollup) => <span className="text-xs">{formatDateTime(r.first_seen)}</span>,
    },
    {
      key: 'last_seen', label: 'Last seen',
      render: (r: HotspotRollup) => <span className="text-xs">{formatDateTime(r.last_seen)}</span>,
    },
  ];
}

/** 24-bar hour-of-day strip. Night hours (22:00-05:00) are tinted so the
 *  "stops here happen after dark" pattern is readable without a legend. */
function HourStrip({ profile }: { profile: number[] }) {
  const max = Math.max(...profile, 1);
  return (
    <div>
      <div className="flex items-end gap-[3px] h-24">
        {profile.map((n, h) => {
          const night = h < 5 || h >= 22;
          return (
            <div key={h} className="flex-1 flex flex-col justify-end" title={`${h}:00 — ${n} stop(s)`}>
              <div
                className={`rounded-t ${night ? 'bg-indigo-500' : 'bg-blue-600'} ${n ? '' : 'opacity-20'}`}
                style={{ height: `${Math.max((n / max) * 100, n ? 6 : 2)}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-gray-600 mt-1">
        <span>00</span><span>06</span><span>12</span><span>18</span><span>23</span>
      </div>
    </div>
  );
}

export default function TTAHotspots() {
  const [limit, setLimit] = useState(50);
  const [includeLowSupport, setIncludeLowSupport] = useState(false);
  const [includeAmenities, setIncludeAmenities] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [labelNote, setLabelNote] = useState('');

  const { data, loading, refetch } = useApi(
    () => getHotspots(limit, includeLowSupport, includeAmenities),
    [limit, includeLowSupport, includeAmenities],
  );
  const { data: status, refetch: refetchStatus } = useApi(() => getHotspotStatus(), []);
  // Explicit type param: the ternary below unions the fetcher's return type,
  // which would otherwise collapse `detail` to `any`.
  const { data: detail, loading: detailLoading, refetch: refetchDetail } =
    useApi<HotspotDetailResponse | null>(
      () => (selectedId
        ? getHotspotDetail(selectedId)
        : Promise.resolve({ data: null })),
      [selectedId],
    );

  const items = data?.items ?? [];
  const kpis = data?.kpis;
  const drill = useDrillDown();

  // ---- Drill-downs: every headline count can produce its own rows -------- //
  // The queue's KPI counts are over ALL clusters in scope, not the filtered
  // table, so these loaders pass include_low_support/include_amenities — a
  // drill-down that returned fewer rows than the number it opened would be
  // worse than no drill-down at all.
  const drillClusters = (title: string, subtitle: string) => drill.open({
    title, subtitle,
    columns: [
      { key: 'd_score', label: 'Score', align: 'right',
        render: (r: Hotspot) => <span className="font-semibold text-white">{Number(r.d_score).toFixed(1)}</span> },
      { key: 's_place_key', label: 'Location',
        render: (r: Hotspot) => (
          <div>
            <div className="font-mono text-xs">{Number(r.d_lat).toFixed(4)}, {Number(r.d_long).toFixed(4)}</div>
            <div className="text-xs text-gray-500 truncate max-w-[200px]">near {r.s_nearest_node ?? '—'}</div>
          </div>
        ) },
      { key: 'i_stops', label: 'Stops', align: 'right' },
      { key: 'i_vehicles', label: 'Vehicles', align: 'right' },
      { key: 'i_carriers', label: 'Carriers', align: 'right' },
      { key: 'd_median_dwell_min', label: 'Dwell', align: 'right',
        render: (r: Hotspot) => fmtMins(r.d_median_dwell_min) },
      { key: 'd_night_share', label: 'Night', align: 'right',
        render: (r: Hotspot) => pct(r.d_night_share) },
      { key: 'flags', label: '',
        render: (r: Hotspot) => (
          <div className="flex gap-1">
            {r.b_low_support ? <Badge label="low support" variant="neutral" /> : null}
            {r.s_amenity_hint && <Badge label={r.s_amenity_hint} variant="info" />}
          </div>
        ) },
    ],
    empty: 'No clusters have been built yet.',
    load: async () => (await getHotspots(500, true, true)).data.items,
  });

  const drillStops = (clustered: boolean, title: string, subtitle: string) => drill.open({
    title, subtitle,
    columns: [
      ...STOP_COLUMNS,
      ...(clustered
        ? [{ key: 's_nearest_node', label: 'Place',
             render: (r: HotspotStopRecord) => (
               <span className="text-xs text-gray-400">{r.s_nearest_node ?? '—'}</span>) }]
        : [{ key: 's_place_class', label: 'Classified as',
             render: (r: HotspotStopRecord) => (
               <span className={`text-xs ${r.s_place_class === 'unclassified' ? 'text-gray-400' : 'text-gray-600'}`}>
                 {r.s_place_class}
               </span>) }]),
    ],
    rowLink: (r: HotspotStopRecord) => `/trips/${r.i_trip_no}/analysis`,
    empty: 'No stop events.',
    load: async () => (await getHotspotStops(clustered, 2000)).data.stops,
  });

  /** The stops at whichever place scored highest — what earned the top score. */
  const drillTopScore = () => drill.open({
    title: 'Top-scoring location',
    subtitle: 'Every stop recorded at the highest-ranked place',
    columns: STOP_COLUMNS,
    rowLink: (r: HotspotMember) => `/trips/${r.i_trip_no}/analysis`,
    empty: 'No clusters have been built yet.',
    load: async () => {
      const all = (await getHotspots(500, true, true)).data.items;
      if (!all.length) return [];
      const top = all.reduce((a, b) => (Number(b.d_score) > Number(a.d_score) ? b : a));
      return (await getHotspotDetail(top.id)).data.members;
    },
  });

  /** Row-level drills. All three read ONE evidence pack, so the stops, the
   *  vehicle rollup and the carrier rollup are guaranteed to agree. */
  const drillRow = (r: Hotspot, kind: 'stops' | 'vehicles' | 'carriers') => {
    const place = `${Number(r.d_lat).toFixed(4)}, ${Number(r.d_long).toFixed(4)}`
      + (r.s_nearest_node ? ` · near ${r.s_nearest_node}` : '');
    if (kind === 'stops') {
      return drill.open({
        title: `${formatNumber(r.i_stops)} stops at this place`,
        subtitle: place,
        columns: STOP_COLUMNS,
        rowLink: (m: HotspotMember) => `/trips/${m.i_trip_no}/analysis`,
        empty: 'No member stops.',
        load: async () => (await getHotspotDetail(r.id)).data.members,
      });
    }
    const vehicles = kind === 'vehicles';
    return drill.open({
      title: vehicles
        ? `${formatNumber(r.i_vehicles)} vehicles stopped here`
        : `${formatNumber(r.i_carriers)} carriers stopped here`,
      subtitle: place,
      columns: vehicles
        ? rollupColumns('s_asset_id', 'Vehicle')
        : rollupColumns('s_trans_name', 'Carrier'),
      empty: 'No records.',
      load: async () => {
        const d = (await getHotspotDetail(r.id)).data;
        return vehicles ? d.vehicle_mix : d.carrier_rollup;
      },
    });
  };

  const run = async (key: string, fn: () => Promise<any>) => {
    setBusy(key);
    try {
      await fn();
      refetch();
      refetchStatus();
      if (selectedId) refetchDetail();
    } finally {
      setBusy(null);
    }
  };

  const submitLabel = async (status: string) => {
    if (!selectedId) return;
    await run('label', () => labelHotspot(selectedId, status, labelNote, 'analyst'));
    setLabelNote('');
  };

  const exportEvidence = () => {
    if (!detail) return;
    const c = detail.cluster;
    downloadCsv(
      `hotspot_${c.s_place_key.replace(/[.,]/g, '_')}.csv`,
      detail.members.map(m => ({
        hotspot_lat: c.d_lat,
        hotspot_lng: c.d_long,
        hotspot_score: c.d_score,
        trip_no: m.i_trip_no,
        vehicle: m.s_asset_id,
        carrier: m.s_trans_name,
        driver: m.s_driver_name,
        route: m.route,
        stop_start: m.dt_start,
        stop_end: m.dt_end,
        duration_min: m.d_duration_min,
        hour: m.i_hour,
        stop_lat: m.d_lat,
        stop_lng: m.d_long,
        nearest_node: m.s_wpnt,
        nearest_node_m: m.i_wpnt_mt,
      })),
    );
  };

  const carrierMix = useMemo(
    () => (detail?.carrier_mix ?? [])
      .slice(0, 8)
      .map(c => ({ name: (c.carrier ?? 'Unknown').slice(0, 24), value: c.stops })),
    [detail],
  );

  const columns = [
    {
      key: 'd_score', label: 'Score',
      render: (r: Hotspot) => (
        <span className="font-semibold text-white">{Number(r.d_score).toFixed(1)}</span>
      ),
    },
    {
      key: 's_place_key', label: 'Location',
      render: (r: Hotspot) => (
        <div>
          <div className="font-mono text-xs text-gray-200">
            {Number(r.d_lat).toFixed(4)}, {Number(r.d_long).toFixed(4)}
          </div>
          <div className="text-xs text-gray-500 truncate max-w-[240px]">
            near {r.s_nearest_node ?? '—'} ({formatNumber(r.i_nearest_node_m ?? 0)} m)
          </div>
        </div>
      ),
    },
    {
      key: 'i_stops', label: 'Stops',
      render: (r: Hotspot) => (
        <DrillNum value={formatNumber(r.i_stops)} onClick={() => drillRow(r, 'stops')} />
      ),
    },
    {
      key: 'i_vehicles', label: 'Vehicles',
      render: (r: Hotspot) => (
        <DrillNum value={formatNumber(r.i_vehicles)} className="text-cyan-400"
          onClick={() => drillRow(r, 'vehicles')} />
      ),
    },
    {
      key: 'i_carriers', label: 'Carriers',
      render: (r: Hotspot) => (
        <DrillNum value={formatNumber(r.i_carriers)} className="text-amber-400 font-medium"
          onClick={() => drillRow(r, 'carriers')} />
      ),
    },
    {
      key: 'd_median_dwell_min', label: 'Dwell',
      render: (r: Hotspot) => `${Math.round(Number(r.d_median_dwell_min ?? 0))} min`,
    },
    {
      key: 'd_night_share', label: 'Night',
      render: (r: Hotspot) => `${(Number(r.d_night_share ?? 0) * 100).toFixed(0)}%`,
    },
    {
      key: 'flags', label: '',
      render: (r: Hotspot) => (
        <div className="flex gap-1">
          {r.label && <Badge label={r.label.status.replace('_', ' ')} variant={labelVariant(r.label.status)} />}
          {r.b_low_support ? <Badge label="low support" variant="neutral" /> : null}
          {r.s_amenity_hint && <Badge label={r.s_amenity_hint} variant="info" />}
        </div>
      ),
    },
  ];

  const cluster = detail?.cluster;

  /**
   * What the queue is worth, in the only units anyone outside this page cares
   * about: hours standing still, and how many trips it touched.
   *
   * Hours are stops x median dwell per cluster — a median rather than a mean
   * because a single 14-hour GPS outage inside a cluster would otherwise carry
   * the whole figure. Trips are summed across clusters, so a trip halting at two
   * different hotspots counts twice; that is the exposure, not a trip count.
   */
  const exposure = useMemo(() => {
    const rows = items ?? [];
    let minutes = 0, nightMinutes = 0, trips = 0;
    for (const r of rows) {
      const m = (r.i_stops ?? 0) * (r.d_median_dwell_min ?? 0);
      minutes += m;
      nightMinutes += m * (r.d_night_share ?? 0);
      trips += r.i_trips ?? 0;
    }
    return {
      hours: minutes / 60,
      trips,
      nightPct: minutes > 0 ? Math.round((100 * nightMinutes) / minutes) : null,
    };
  }, [items]);

  return (
    <PageContainer>
      <p className="text-sm text-gray-500 -mt-4 mb-4">
        Locations that attract repeated unscheduled stops across many vehicles and many carriers.
      </p>

      {/* What the page is FOR.
          The KPI strip below counts clusters and scores, which are properties of
          the algorithm rather than of the business. On their own they invite the
          fair question "so what?" — so the exposure the queue represents is
          stated first, in hours and trips, and the three decisions it feeds are
          named. */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-4">
        <h2 className="text-sm font-semibold text-white mb-1">Why this page exists</h2>
        <p className="text-xs text-gray-400 leading-relaxed mb-3">
          A truck that stops where no plant, customer or named node exists is burning
          time you are paying for, in a place nobody scheduled. One such stop is noise.
          The same spot pulling in many vehicles from many different carriers is a
          property of the <em>location</em>, and that is worth a look.
          {exposure.hours > 0 && (
            <>
              {' '}In this window the queue represents{' '}
              <strong className="text-gray-200">{formatNumber(Math.round(exposure.hours))} hours</strong>{' '}
              of unscheduled standstill across{' '}
              <strong className="text-gray-200">{formatNumber(exposure.trips)} trips</strong>
              {exposure.nightPct != null && (
                <>, {exposure.nightPct}% of it after dark</>
              )}.
            </>
          )}
        </p>
        <div className="grid sm:grid-cols-3 gap-2">
          {[
            ['Security', 'A cluster with high carrier diversity and a high night share is where cargo goes missing. Send someone to look before it becomes a claim.'],
            ['Transit time', 'Unscheduled halts are pure lost hours on the lane. If a spot appears on one route repeatedly, it is a routing or rest-stop conversation.'],
            ['Contract evidence', 'A carrier arguing a lane is simply slow can be shown where its trucks actually stop, and for how long.'],
          ].map(([h, d]) => (
            <div key={h} className="bg-gray-950/60 border border-gray-800 rounded-lg px-3 py-2">
              <p className="text-xs font-semibold text-gray-300">{h}</p>
              <p className="text-xs text-gray-500 leading-snug mt-0.5">{d}</p>
            </div>
          ))}
        </div>
      </div>

      {/* The honest limit belongs in the product, not only the pitch. */}
      <div className="flex gap-3 items-start bg-amber-950/30 border border-amber-900/60 rounded-xl p-4 mb-6">
        <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
        <div className="text-sm text-amber-200/90">
          <span className="font-semibold">This is an investigation queue, not an accusation.</span>{' '}
          It finds anomalous <em>stopping</em>, not theft. Motion is derived from the provider&apos;s
          status text, so traffic, breakdowns, driver rest and GPS outages all look like stops.
          Plants, consignees and named nodes are filtered out, but fuel stops and dhabas are only
          partly caught. Every row needs physical verification before it means anything.
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <KPICard
          label="Hotspots in queue" value={formatNumber(kpis?.clusters ?? 0)}
          icon={ShieldAlert} color="red"
          drillLabel="List every discovered location"
          onDrill={() => drillClusters(
            `${formatNumber(kpis?.clusters ?? 0)} discovered locations`,
            'Every cluster in scope, including the low-support and amenity-adjacent ones the queue hides',
          )}
        />
        <KPICard
          label="Stops clustered" value={formatNumber(kpis?.stops ?? 0)}
          icon={MapPin} color="blue"
          drillLabel="List the stops behind this count"
          onDrill={() => drillStops(
            true,
            `${formatNumber(kpis?.stops ?? 0)} clustered stops`,
            `Which vehicle, which carrier, when it stopped and for how long`
            + ((kpis?.stops ?? 0) > 2000 ? ' · showing the 2,000 most recent' : ''),
          )}
        />
        <KPICard
          label="Top score" value={kpis?.top_score != null ? Number(kpis.top_score).toFixed(1) : '—'}
          icon={Crosshair} color="amber"
          drillLabel="See the stops that earned it"
          onDrill={drillTopScore}
        />
        <KPICard
          label="Stop corpus"
          value={formatNumber(status?.corpus?.stops ?? 0)}
          icon={Truck}
          color="cyan"
          drillLabel="List every extracted stop event"
          onDrill={() => drillStops(
            false,
            `${formatNumber(status?.corpus?.stops ?? 0)} extracted stops`,
            'The whole corpus, including stops masked as a plant, consignee or amenity'
            + ((status?.corpus?.stops ?? 0) > 2000 ? ' · showing the 2,000 most recent' : ''),
          )}
        />
      </div>

      {/* Support disclosure: how much history the ranking rests on. */}
      {status && (
        <div className="text-xs text-gray-500 mb-6 flex flex-wrap gap-x-6 gap-y-1">
          <span>{formatNumber(status.corpus.trips ?? 0)} trips · {formatNumber(status.corpus.vehicles ?? 0)} vehicles · {formatNumber(status.corpus.carriers ?? 0)} carriers</span>
          <span>window {formatDateTime(status.corpus.first_stop)} → {formatDateTime(status.corpus.last_stop)}</span>
          <span>last built {formatDateTime(status.clusters?.last_built)}</span>
          {status.trips_pending > 0 && (
            <span className="text-amber-500">{status.trips_pending} trips pending extraction</span>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="flex items-center gap-2 text-sm text-gray-400">
          <input type="checkbox" checked={includeLowSupport}
            onChange={e => setIncludeLowSupport(e.target.checked)} className="accent-blue-500" />
          Show low-support ({kpis?.low_support ?? 0})
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-400">
          <input type="checkbox" checked={includeAmenities}
            onChange={e => setIncludeAmenities(e.target.checked)} className="accent-blue-500" />
          Show fuel stops / dhabas / tolls ({kpis?.amenity_adjacent ?? 0})
        </label>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))}
          className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-gray-300">
          {[25, 50, 100, 200].map(n => <option key={n} value={n}>Top {n}</option>)}
        </select>

        <div className="ml-auto flex gap-2">
          <button onClick={() => run('extract', () => extractStopEvents(1000))}
            disabled={!!busy}
            className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${busy === 'extract' ? 'animate-spin' : ''}`} />
            Extract new stops
          </button>
          <button onClick={() => run('refresh', () => refreshHotspots(true))}
            disabled={!!busy}
            className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${busy === 'refresh' ? 'animate-spin' : ''}`} />
            Rebuild hotspots
          </button>
        </div>
      </div>

      <ChartCard title="Where they are"
        method={{
          formula: "Stop events are clustered geographically; a cluster becomes a hotspot when repeated unscheduled stops occur there across multiple vehicles and carriers. Score combines dwell length, night share, carrier diversity, vehicle diversity and isolation from any named node.",
          plot: "map",
          caveat: "Plants, consignees and named nodes are filtered out, but fuel stops and dhabas are only partly caught. Motion comes from the provider's status text, so traffic, breakdowns and GPS outages also look like stops.",
        }} icon={MapPin} className="mb-6"
        explain="Each circle is a discovered location. Click one to open its evidence pack.">
        {loading ? <Spinner /> : (
          <HotspotMap hotspots={items} selectedId={selectedId} onSelect={setSelectedId} />
        )}
      </ChartCard>

      <ChartCard title="Investigation queue"
        method={{
          formula: "Hotspots ranked by score. ‘Stops’ is events at that location, ‘median dwell’ the typical standstill, and isolation the distance to the nearest named node.",
          plot: "table",
          caveat: "This is an investigation queue, not an accusation — every row needs physical verification before it means anything.",
        }} icon={ShieldAlert} iconColor="text-red-400" className="mb-6"
        explain="Ranked by distinct carriers, distinct vehicles, isolation from any named node, dwell in the 30–60 min band, and night share.">
        <DataTable
          columns={columns}
          data={items}
          loading={loading}
          onRowClick={(r: Hotspot) => setSelectedId(r.id)}
          emptyMessage="No hotspots found. Run 'Rebuild hotspots' if the corpus has changed."
        />
      </ChartCard>

      {selectedId && (
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          {detailLoading || !cluster ? <Spinner /> : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
                <div>
                  <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                    <Crosshair className="w-5 h-5 text-red-400" />
                    {Number(cluster.d_lat).toFixed(5)}, {Number(cluster.d_long).toFixed(5)}
                    <span className="text-sm font-normal text-gray-500">
                      score {Number(cluster.d_score).toFixed(1)}
                    </span>
                  </h2>
                  <p className="text-xs text-gray-500 mt-1">
                    near {cluster.s_nearest_node ?? '—'} ({formatNumber(cluster.i_nearest_node_m ?? 0)} m)
                    · radius {cluster.i_radius_m ?? 0} m
                    · seen {formatDateTime(cluster.dt_first_seen)} → {formatDateTime(cluster.dt_last_seen)}
                  </p>
                </div>
                <div className="flex gap-2">
                  <a href={satelliteUrl(Number(cluster.d_lat), Number(cluster.d_long))}
                    target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300">
                    <ExternalLink className="w-4 h-4" /> Satellite
                  </a>
                  <button onClick={exportEvidence}
                    className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300">
                    <Download className="w-4 h-4" /> Evidence CSV
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
                {([
                  ['Stops', formatNumber(cluster.i_stops), Clock, () => drillRow(cluster, 'stops')],
                  ['Vehicles', formatNumber(cluster.i_vehicles), Truck, () => drillRow(cluster, 'vehicles')],
                  ['Carriers', formatNumber(cluster.i_carriers), Users, () => drillRow(cluster, 'carriers')],
                  ['Median dwell', fmtMins(cluster.d_median_dwell_min), Clock, null],
                  ['Night share', pct(cluster.d_night_share), Moon, null],
                ] as [string, string, any, (() => void) | null][]).map(([label, value, Icon, onDrill]) => (
                  <div key={label} className="bg-gray-950/60 rounded-lg border border-gray-800 p-3">
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1">
                      <Icon className="w-3.5 h-3.5" /> {label}
                    </div>
                    <div className="text-lg font-semibold text-white">
                      {onDrill
                        ? <DrillNum value={value} onClick={onDrill} className="text-white" />
                        : value}
                    </div>
                  </div>
                ))}
              </div>

              {/* Why this rank — every score is explainable from its components. */}
              {cluster.s_components && (
                <div className="mb-6">
                  <h3 className="text-sm font-semibold text-gray-300 mb-2">Why this rank</h3>
                  <div className="flex gap-1 h-7 rounded-lg overflow-hidden border border-gray-800">
                    {([
                      ['carriers', tc('#f59e0b')], ['vehicles', tc('#06b6d4')], ['isolation', '#8b5cf6'],
                      ['dwell', tc('#10b981')], ['night', '#6366f1'],
                    ] as const).map(([k, color]) => {
                      const v = Number((cluster.s_components as any)[k] ?? 0);
                      return v > 0 ? (
                        <div key={k} style={{ width: `${v}%`, background: color }}
                          className="flex items-center justify-center text-xs text-white/90 font-medium"
                          title={`${k}: ${v.toFixed(1)} points`}>
                          {v >= 8 ? k : ''}
                        </div>
                      ) : null;
                    })}
                    <div className="flex-1 bg-gray-950" />
                  </div>
                  <p className="text-xs text-gray-600 mt-1.5">
                    Contribution of each term to the {Number(cluster.d_score).toFixed(1)} score (out of 100).
                    Night share shrunk to {(Number(cluster.d_night_share_adj ?? 0) * 100).toFixed(0)}% against the fleet baseline.
                  </p>
                </div>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
                <div>
                  <h3 className="text-sm font-semibold text-gray-300 mb-3">When they stop</h3>
                  <HourStrip profile={detail!.hour_profile} />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-gray-300 mb-3">Carrier mix</h3>
                  {carrierMix.length ? <DonutChart data={carrierMix} height={200} innerRadius={45} />
                    : <p className="text-sm text-gray-600">No carrier data.</p>}
                </div>
              </div>

              <div className="mb-6">
                <h3 className="text-sm font-semibold text-gray-300 mb-2">Verdict</h3>
                <div className="flex flex-wrap gap-2 items-center">
                  <input value={labelNote} onChange={e => setLabelNote(e.target.value)}
                    placeholder="Note (optional) — what did the check find?"
                    className="flex-1 min-w-[220px] bg-gray-950 border border-gray-800 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-600" />
                  {LABEL_OPTIONS.map(o => (
                    <button key={o.value} onClick={() => submitLabel(o.value)} disabled={!!busy}
                      className="px-3 py-1.5 text-xs rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50">
                      {o.label}
                    </button>
                  ))}
                </div>
                {cluster.label && (
                  <p className="text-xs text-gray-500 mt-2">
                    Current: <Badge label={cluster.label.status.replace('_', ' ')} variant={labelVariant(cluster.label.status)} />
                    {cluster.label.note ? ` — ${cluster.label.note}` : ''}
                  </p>
                )}
              </div>

              {/* The counts above are sums over these rows. Shown inline rather
                  than only behind a click, because "16 vehicles" is a claim
                  about 16 named trucks and the reader should be able to see
                  them without hunting. Collapsed by default so the evidence
                  table below stays the first thing in view. */}
              <details className="mb-6 rounded-lg border border-gray-800 bg-gray-950/40" open>
                <summary className="cursor-pointer select-none px-4 py-2.5 text-sm font-semibold text-gray-300 hover:text-white">
                  By vehicle ({detail!.vehicle_mix?.length ?? 0})
                  <span className="ml-2 text-xs font-normal text-gray-500">
                    how long each truck stood here, and when
                  </span>
                </summary>
                <div className="px-2 pb-2">
                  <DataTable
                    columns={rollupColumns('s_asset_id', 'Vehicle')}
                    data={detail!.vehicle_mix ?? []}
                    emptyMessage="No vehicle rollup."
                  />
                </div>
              </details>

              <details className="mb-6 rounded-lg border border-gray-800 bg-gray-950/40">
                <summary className="cursor-pointer select-none px-4 py-2.5 text-sm font-semibold text-gray-300 hover:text-white">
                  By carrier ({detail!.carrier_rollup?.length ?? 0})
                  <span className="ml-2 text-xs font-normal text-gray-500">
                    the same stops grouped by who was running them
                  </span>
                </summary>
                <div className="px-2 pb-2">
                  <DataTable
                    columns={rollupColumns('s_trans_name', 'Carrier')}
                    data={detail!.carrier_rollup ?? []}
                    emptyMessage="No carrier rollup."
                  />
                </div>
              </details>

              <h3 className="text-sm font-semibold text-gray-300 mb-2">
                The stops ({detail!.members.length})
              </h3>
              <DataTable
                columns={[
                  { key: 'dt_start', label: 'When', render: (m: HotspotMember) => formatDateTime(m.dt_start) },
                  { key: 'd_duration_min', label: 'Dwell', render: (m: HotspotMember) => fmtMins(m.d_duration_min) },
                  { key: 's_asset_id', label: 'Vehicle' },
                  { key: 's_trans_name', label: 'Carrier', render: (m: HotspotMember) => <span className="text-amber-400">{m.s_trans_name ?? '—'}</span> },
                  { key: 'route', label: 'Route', render: (m: HotspotMember) => <span className="text-xs text-gray-400">{m.route}</span> },
                  {
                    key: 'i_trip_no', label: 'Trip',
                    render: (m: HotspotMember) => (
                      <a href={`/trips/${m.i_trip_no}/analysis`}
                        className="text-blue-400 hover:underline text-xs">{m.i_trip_no}</a>
                    ),
                  },
                ]}
                data={detail!.members}
                emptyMessage="No member stops."
              />
            </>
          )}
        </div>
      )}
    </PageContainer>
  );
}
