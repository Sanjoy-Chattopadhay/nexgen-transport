import { useState } from 'react';
import {
  Satellite, SatelliteDish, Clock, Timer, MapPinOff, AlertTriangle,
  Building2, Globe, Route as RouteIcon, MapPinned, Upload,
} from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import KPICard from '../components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import DataTable from '../components/ui/DataTable';
import Spinner from '../components/ui/Spinner';
import { useApi } from '../hooks/useApi';
import {
  getDetentionSummary, getDetentionBy, getGpsSummary, getGpsTransporters,
  getGpsRegions, getRouteEndGap, getUngeofenced,
} from '../services/geofence';
import type { TripClass } from '../services/geofence';
import { formatNumber } from '../lib/formatters';
import TripProofTab from '../components/geofence/TripProofTab';
import ClientFencesTab from '../components/geofence/ClientFencesTab';
import GpsQualityAnalytics from '../components/geofence/GpsQualityAnalytics';
import DestinationCoverageAnalytics from '../components/geofence/DestinationCoverageAnalytics';

const TABS = [
  { id: 'detention', label: 'Works Detention', icon: Timer },
  { id: 'gps', label: 'GPS Performance', icon: SatelliteDish },
  { id: 'destination', label: 'Destination Coverage', icon: MapPinOff },
  { id: 'proof', label: 'Trip on the Map', icon: MapPinned },
  { id: 'client', label: 'Your Geofences', icon: Upload },
] as const;
type TabId = typeof TABS[number]['id'];

const TRIP_CLASSES: { id: TripClass; label: string }[] = [
  { id: 'zonal', label: 'Zonal' },
  { id: 'local', label: 'Local' },
  { id: 'all', label: 'All' },
];

const h = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)} h`);
const pct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(1)}%`);

function SectionCard({ title, subtitle, children }: {
  title: string; subtitle?: string; children: React.ReactNode;
}) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        {subtitle && <p className="text-xs text-gray-500 mt-1 max-w-4xl">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Works detention
// ---------------------------------------------------------------------------

function DetentionTab({ tripClass }: { tripClass: TripClass }) {
  const { data: sum, loading, error } = useApi(() => getDetentionSummary(tripClass), [tripClass]);
  const { data: byTrans, loading: tLoading } = useApi(
    () => getDetentionBy('transporter', tripClass), [tripClass]);

  if (loading) return <Spinner />;
  if (error) return <p className="text-red-400 text-sm">{error}</p>;
  if (!sum) return null;

  return (
    <>
      <ProofGrid className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KPICard label="Declared detention (median)" color="blue" icon={Clock}
          value={h(sum.declared_works_detention_h.median)}
          proof={{ dataset: 'detention.declared', params: { trip_class: tripClass }, value: sum.declared_works_detention_h.median }} />
        <KPICard label="Hidden tail after gate-out" color="amber" icon={Timer}
          value={h(sum.hidden_tail_h.median)}
          proof={{ dataset: 'detention.tail', params: { trip_class: tripClass }, value: sum.hidden_tail_h.median }} />
        <KPICard label="True total (median)" color="purple" icon={Clock}
          value={h(sum.true_total_detention_h.median)}
          proof={{ dataset: 'detention.total', params: { trip_class: tripClass }, value: sum.true_total_detention_h.median }} />
        <KPICard label="Trips with 4h+ hidden tail" color="red" icon={AlertTriangle}
          value={formatNumber(sum.tail_over_4h)}
          proof={{ dataset: 'detention.tail4h', params: { trip_class: tripClass }, value: sum.tail_over_4h }} />
      </ProofGrid>

      <SectionCard
        title="The three detention windows"
        subtitle={
          'Plant entry (dt_booking) → the gate-out stamp (dt_trip_start) is the detention the system reports today. ' +
          'But the truck is still physically inside the origin geofence for a while after that stamp fires — that is ' +
          'the hidden tail, and it is only visible from the GPS trail. True total = declared + hidden tail.'
        }>
        <DataTable
          columns={[
            { key: 'window', label: 'Window' },
            { key: 'p25', label: 'P25' },
            { key: 'median', label: 'Median', className: 'text-white' },
            { key: 'p75', label: 'P75' },
            { key: 'p90', label: 'P90' },
          ]}
          data={[
            { window: '1. Declared works detention (booking → gate-out)', ...sum.declared_works_detention_h },
            { window: '2. Hidden tail (gate-out → geofence exit)', ...sum.hidden_tail_h },
            { window: '3. True total (booking → geofence exit)', ...sum.true_total_detention_h },
          ].map(r => ({
            window: r.window,
            p25: h(r.p25), median: h(r.median), p75: h(r.p75), p90: h(r.p90),
          }))}
        />
        <p className="text-xs text-gray-500 mt-4">
          Based on {formatNumber(sum.declared_works_detention_h.n)} trips with a confirmed geofence exit.
          Medians, not means — detention is heavily right-skewed and a few multi-day holds would otherwise
          set the "typical" figure. {formatNumber(sum.tail_over_12h)} trips exceed 12 h of hidden tail.
        </p>
        {(sum.excluded.wide_gps_gap > 0 || sum.excluded.negative_tail_late_stamp > 0) && (
          <div className="mt-4 rounded-lg border border-amber-900/50 bg-amber-950/20 p-3">
            <p className="text-xs text-amber-300 font-medium mb-1">Excluded from the figures above</p>
            <p className="text-xs text-gray-400">
              {sum.excluded.negative_tail_late_stamp} trips cleared the fence <em>before</em> their own
              gate-out stamp (the stamp was keyed in late — a data-entry defect, not detention).{' '}
              {sum.excluded.wide_gps_gap} trips had a GPS gap wide enough that the exit time is uncertain.
              Both are reported rather than averaged in.
            </p>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Hidden tail by transporter"
        subtitle="Which carriers sit inside the works longest after being stamped as departed. Minimum 5 trips.">
        <DataTable
          loading={tLoading}
          columns={[
            { key: 'transporter', label: 'Transporter', className: 'text-white' },
            { key: 'trips', label: 'Trips' },
            { key: 'declared', label: 'Declared (median)' },
            { key: 'tail', label: 'Hidden tail (median)' },
            { key: 'p90', label: 'Hidden tail (P90)' },
            { key: 'under', label: 'Understated by' },
          ]}
          data={(byTrans || []).map(r => ({
            transporter: r.transporter,
            trips: r.trips,
            declared: h(r.declared_median_h),
            tail: <span className="text-amber-400 font-medium">{h(r.hidden_tail_median_h)}</span>,
            p90: h(r.hidden_tail_p90_h),
            under: r.understatement_pct == null ? '—'
              : <span className="text-red-400">+{r.understatement_pct.toFixed(0)}%</span>,
          }))}
          emptyMessage="No transporter has enough trips with a confirmed exit."
        />
      </SectionCard>
    </>
  );
}

// ---------------------------------------------------------------------------
// GPS performance
// ---------------------------------------------------------------------------

function GpsTab({ tripClass }: { tripClass: TripClass }) {
  const { data: sum, loading, error } = useApi(() => getGpsSummary(tripClass), [tripClass]);
  const { data: trans, loading: tLoading } = useApi(() => getGpsTransporters(tripClass), [tripClass]);
  const { data: regions, loading: rLoading } = useApi(() => getGpsRegions(tripClass), [tripClass]);

  if (loading) return <Spinner />;
  if (error) return <p className="text-red-400 text-sm">{error}</p>;
  if (!sum) return null;

  return (
    <>
      <ProofGrid className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <KPICard label="GPS healthy" color="green" icon={Satellite} value={pct(sum.gps_ok_pct)}
          proof={{ dataset: 'gpsperf.ok', params: { trip_class: tripClass }, value: sum.gps_ok_pct }} />
        <KPICard label="Silent (no data received)" color="red" icon={SatelliteDish}
          value={formatNumber(sum.silent_trips)}
          proof={{ dataset: 'gpsperf.silent', params: { trip_class: tripClass }, value: sum.silent_trips }} />
        <KPICard label="Died at origin" color="amber" icon={AlertTriangle}
          value={formatNumber(sum.died_at_origin)}
          proof={{ dataset: 'gpsperf.died', params: { trip_class: tripClass }, value: sum.died_at_origin }} />
        <KPICard label="GPS on time" color="blue" icon={Clock} value={pct(sum.gps_on_time_pct)}
          proof={{ dataset: 'gpsperf.ontime', params: { trip_class: tripClass }, value: sum.gps_on_time_pct }} />
        <KPICard label="Median pings / trip" color="purple" icon={Satellite}
          value={formatNumber(sum.median_ping_count)}
          proof={{ dataset: 'gpsperf.pings', params: { trip_class: tripClass }, value: sum.median_ping_count }} />
      </ProofGrid>

      <SectionCard
        title="What each failure mode means"
        subtitle="Four different problems hide behind “GPS not working”, and each has a different owner — so they are counted separately rather than rolled into one uptime number.">
        <div className="grid gap-3 md:grid-cols-2">
          {[
            ['Silent', `${sum.silent_trips} trips`, 'Trip exists, zero pings ever arrived. The device never reported at all — a placement or hardware problem.'],
            ['Died at origin', `${sum.died_at_origin} trips (${pct(sum.died_at_origin_pct)})`, 'Pings arrived, then stopped while the truck was still inside the origin fence — on a trip whose destination is hundreds of km away. The truck went; the tracker did not. This is the one that looks healthy in a ping-count report.'],
            ['Late start', `${sum.late_start} trips`, 'First ping arrived more than 30 min after the trip started, so the loading and gate-out window went unobserved. This is what “GPS on time” measures.'],
            ['Gappy', `${sum.gappy} trips`, 'Pings throughout, but with long holes in the middle of the run.'],
          ].map(([name, count, desc]) => (
            <div key={name} className="rounded-lg border border-gray-800 bg-gray-950/40 p-3">
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span className="text-sm font-medium text-white">{name}</span>
                <span className="text-xs text-amber-400 shrink-0">{count}</span>
              </div>
              <p className="text-xs text-gray-500">{desc}</p>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title="GPS performance by transporter"
        subtitle="Minimum 5 trips per carrier. Every mark opens the trips behind it.">
        <GpsQualityAnalytics
          rows={trans as never}
          loading={tLoading}
          dim="transporter"
          tripClass={tripClass}
          note="Carrier names are not normalised upstream — the same company can appear under two
                spellings (e.g. “Utility Transport Company” and “UTILITY TRANSPORT COMPANY”), which
                splits its trips across two rows and widens both intervals."
        />
      </SectionCard>

      <SectionCard
        title="GPS performance by region"
        subtitle="Region is the state the GPS trail ended in, from the waypoint state code. Trips whose tracker died at the origin therefore land in Jharkhand — which is itself the finding.">
        <GpsQualityAnalytics
          rows={regions as never}
          loading={rLoading}
          dim="region"
          tripClass={tripClass}
        />
      </SectionCard>
    </>
  );
}

// ---------------------------------------------------------------------------
// Destination coverage
// ---------------------------------------------------------------------------

function DestinationTab({ tripClass }: { tripClass: TripClass }) {
  const { data: gaps, loading, error } = useApi(() => getRouteEndGap(tripClass), [tripClass]);
  const { data: ung, loading: uLoading, error: uError } = useApi(
    () => getUngeofenced(tripClass), [tripClass]);

  if (loading) return <Spinner />;
  if (error) return <p className="text-red-400 text-sm">{error}</p>;

  const conclusive = (gaps || []).filter(g => g.gap_is_conclusive && g.median_end_gap_km > 25);

  // A count that has not arrived is NOT zero. Rendering `?? 0` here would state
  // "0 destinations need a geofence" whenever the request is merely slow or
  // failed — a wrong answer presented with the same confidence as a right one,
  // on a screen whose whole purpose is to say how many need fixing.
  const ungValue = (n: number | undefined) =>
    uError ? '!' : uLoading || ung == null ? '—' : formatNumber(n ?? 0);

  return (
    <>
      <ProofGrid className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <KPICard label="Lanes stopping short" color="red" icon={RouteIcon}
          value={formatNumber(conclusive.length)}
          proof={{ dataset: 'coverage.short', params: { trip_class: tripClass }, value: conclusive.length }} />
        <KPICard label="Destinations needing a geofence" color="amber" icon={MapPinOff}
          value={ungValue(ung?.destinations_needing_a_geofence)}
          proof={{ dataset: 'coverage.need', params: { trip_class: tripClass }, value: ung?.destinations_needing_a_geofence ?? null }} />
        <KPICard label="Trips affected" color="purple" icon={Building2}
          value={ungValue(ung?.trips_affected)}
          proof={{ dataset: 'coverage.affected', params: { trip_class: tripClass }, value: ung?.trips_affected ?? null }} />
        <KPICard label="Lanes checked" color="blue" icon={Globe}
          value={formatNumber((gaps || []).length)}
          proof={{ dataset: 'coverage.checked', params: { trip_class: tripClass }, value: (gaps || []).length }} />
      </ProofGrid>

      {uError && (
        <div className="mb-6 rounded-lg border border-red-900/50 bg-red-950/20 p-3">
          <p className="text-xs text-red-300 font-medium mb-1">
            Could not load the un-geofenced destinations report
          </p>
          <p className="text-xs text-gray-400">{uError}</p>
        </div>
      )}

      <SectionCard
        title="Where the GPS trail stops short of the destination"
        subtitle="Distance from each trip's last ping to the destination fence. The conclusive/uncertain split is the point: a gap against a town-centroid fence means the customer's gate is not precisely known, not that the truck fell short. Every mark opens the trips behind it.">
        <DestinationCoverageAnalytics
          rows={gaps as never}
          loading={loading}
          tripClass={tripClass}
        />
      </SectionCard>

      <SectionCard
        title="Delivery locations that need a geofence"
        subtitle={
          ung
            ? `Trips where GPS was healthy and the truck covered at least ${ung.criteria.distance_threshold} — so it plainly arrived — but the delivery point has no geofence upstream, so the system could not close on arrival and fell back to a non-geofence reason. The suggested centre is derived from the last pings of trips that did arrive, and can be configured directly.`
            : undefined
        }>
        <DataTable
          loading={uLoading}
          columns={[
            { key: 'destination', label: 'Destination', className: 'text-white' },
            { key: 'affected', label: 'Trips affected' },
            { key: 'lane', label: 'Trips on lane' },
            { key: 'dist', label: 'Lane distance' },
            { key: 'centre', label: 'Suggested fence centre' },
            { key: 'closed', label: 'Closed as' },
          ]}
          data={(ung?.findings || []).map(f => ({
            destination: f.destination,
            affected: <span className="text-amber-400 font-medium">{f.trips_ran_full_route_no_geofence_close}</span>,
            lane: f.trips_on_lane,
            dist: `${formatNumber(Math.round(f.median_lane_distance_km))} km`,
            centre: f.suggested_fence_centre
              ? <span className="font-mono text-xs text-gray-300">
                  {f.suggested_fence_centre.lat.toFixed(5)}, {f.suggested_fence_centre.lon.toFixed(5)}
                </span>
              : '—',
            closed: <span className="text-xs text-gray-500">{f.close_reasons.join(', ')}</span>,
          }))}
          emptyMessage={uError
            ? 'Report failed to load — see the error above.'
            : 'Every delivery location on this lane already has a geofence.'}
        />
      </SectionCard>
    </>
  );
}

// ---------------------------------------------------------------------------

export default function Geofence() {
  const [tab, setTab] = useState<TabId>('gps');
  const [tripClass, setTripClass] = useState<TripClass>('zonal');

  // 'proof' is one trip and 'client' is one file; neither is a per-lane report,
  // so the lane switch and the local-lane caveat do not apply to them.
  const perLane = tab !== 'proof' && tab !== 'client';

  return (
    <PageContainer>
      <div className="flex items-center justify-between flex-wrap gap-3 mb-6">
        <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === t.id ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'
              }`}>
              <t.icon className="w-4 h-4" />
              {t.label}
            </button>
          ))}
        </div>
        <div className={`flex items-center gap-2 ${perLane ? '' : 'invisible'}`}>
          <span className="text-xs text-gray-500">Trip class</span>
          <div className="flex gap-1 bg-gray-900 border border-gray-800 rounded-lg p-1">
            {TRIP_CLASSES.map(c => (
              <button key={c.id} onClick={() => setTripClass(c.id)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  tripClass === c.id ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'
                }`}>
                {c.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {perLane && tripClass === 'local' && (
        <div className="mb-6 rounded-lg border border-blue-900/50 bg-blue-950/20 p-3">
          <p className="text-xs text-gray-400">
            <span className="text-blue-300 font-medium">Note on the local lane:</span>{' '}
            local trips run inside the Jamshedpur belt, so almost none of them ever cross the origin
            geofence. Detention figures here are based on a handful of trips — the zonal lane is where
            these metrics apply.
          </p>
        </div>
      )}

      {tab === 'detention' && <DetentionTab tripClass={tripClass} />}
      {tab === 'gps' && <GpsTab tripClass={tripClass} />}
      {tab === 'destination' && <DestinationTab tripClass={tripClass} />}
      {tab === 'proof' && <TripProofTab />}
      {tab === 'client' && <ClientFencesTab />}
    </PageContainer>
  );
}
