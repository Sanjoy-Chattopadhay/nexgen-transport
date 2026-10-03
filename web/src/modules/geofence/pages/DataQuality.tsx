import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Activity, BarChart3, Building2, CheckCircle2, Cpu, EyeOff, GitCompareArrows, Network, Server, ShieldCheck,
  SlidersHorizontal, XCircle,
} from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, ErrorBox, Field, KPI, Note, PageHeader, QualityBadge, Spinner,
} from '../components/ui';
import { KPIGrid } from '../components/drill';
import { PALETTE, SeriesChart } from '../components/charts';
import {
  QUALITY_TEXT, fmtDateTime, fmtDuration, fmtInt, fmtNum, fmtPct, share, fmtDayShort,
} from '../lib/format';

export default function DataQuality() {
  const navigate = useNavigate();
  const q = useApi(() => api.quality(), []);
  const runs = useApi(() => api.runs(), []);
  const [probe, setProbe] = useState<any>(null);
  const [probing, setProbing] = useState(false);

  const raw = q.data?.comparable_runs?.find((r: any) => r.s_variant === 'raw');
  const compare = useApi(() => (q.data && raw && q.data.run.s_variant === 'fitted'
    ? api.compare(raw.i_run_id, q.data.run_id) : Promise.resolve(null)), [q.data?.run_id, raw?.i_run_id]);

  const gapRows = useMemo(() => {
    const buckets = ['5-15 min', '15-30 min', '30-60 min', '1-4 h', '4 h+'];
    return buckets.map(b => ({
      bucket: b,
      stationary: q.data?.gaps?.find((g: any) => g.bucket === b && g.s_kind === 'stationary')?.gaps ?? 0,
      moving: q.data?.gaps?.find((g: any) => g.bucket === b && g.s_kind === 'moving')?.gaps ?? 0,
    }));
  }, [q.data]);

  if (q.loading && !q.data) return <Spinner label="Loading data quality" />;
  if (q.error) return <ErrorBox error={q.error} onRetry={q.reload} />;
  if (!q.data) return null;
  const d = q.data;
  const r = d.run;
  const p = d.params;
  const totalTrips = d.quality_mix.reduce((s: number, x: any) => s + x.trips, 0);

  const runProbe = async () => {
    setProbing(true);
    try { setProbe(await api.osrmHealth()); } catch (e: any) { setProbe({ status: 'error', error: e.message }); }
    setProbing(false);
  };

  return (
    <div className="animate-fade-in">
      <PageHeader title="Data quality"
        subtitle="How far every other page can be trusted: what the GPS feed looked like, what the filter-and-fit stage did to it before any geofence was checked, and how the published run differs from detection on raw GPS." />

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
        <KPI label="Fixes read" value={fmtInt(r.i_pings_read)} icon={Activity} color="blue" 
          drill={{ dataset: 'trips', params: { measure: 'pings' }, groups: ['quality', 'transporter', 'day'], sort: 'pings' }} />
        <KPI label="Refused as impossible" value={fmtInt(r.i_pings_dropped)} icon={XCircle} color="red"
          hint={fmtPct(share(r.i_pings_dropped, r.i_pings_read), 2)} 
          drill={{ dataset: 'rejects', params: {}, groups: ['reason', 'transporter', 'vehicle'], sort: 'count' }} />
        <KPI label="Standstill fixes smoothed" value={fmtInt(r.i_medians)} icon={SlidersHorizontal} color="purple"
          hint={fmtPct(share(r.i_medians, r.i_pings_read), 0)} 
          drill={{ dataset: 'trips', params: { measure: 'medians', preset: 'medians' }, groups: ['transporter', 'quality'], sort: 'medians' }} />
        <KPI label="Spikes corrected" value={fmtInt(r.i_spikes)} icon={CheckCircle2} color="amber" 
          drill={{ dataset: 'trips', params: { measure: 'spikes', preset: 'spikes' }, groups: ['transporter', 'vehicle', 'quality'], sort: 'spikes' }} />
        <KPI label="Road-snapped (OSRM)" value={fmtInt(r.i_snapped)} icon={Network} color={r.i_snapped ? 'green' : 'gray'} 
          drill={{ dataset: 'trips', params: { measure: 'snapped', preset: 'snapped' }, groups: ['transporter'], sort: 'snapped' }} />
        <KPI label="Stops found" value={fmtInt(r.i_stops)} icon={Activity} color="cyan" 
          drill={{ dataset: 'trips', params: { measure: 'stops', preset: 'stops' }, groups: ['transporter', 'quality'], sort: 'stops', note: 'per trip, as the run counts them' }} />
        <KPI label="Holes: truck moved" value={fmtInt(r.i_moving_gaps)} icon={EyeOff} color="amber" hint={`of ${fmtInt(r.i_gaps)} holes`} 
          drill={{ dataset: 'trips', params: { measure: 'moving_gaps', preset: 'moving_gaps' }, groups: ['quality', 'transporter'], sort: 'moving_gaps', note: 'per trip, as the run counts them' }} />
        <KPI label="Inferred passages" value={fmtInt(r.i_inferred)} icon={EyeOff} color={r.i_inferred ? 'purple' : 'gray'} hint="needs OSRM" 
          drill={{ dataset: 'trips', params: { preset: 'inferred' }, groups: ['transporter'], sort: 'inferred' }} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="Published run" icon={Server}>
          <div className="grid grid-cols-2 gap-2.5">
            <Field label="Run" value={`#${r.i_run_id} · ${r.s_scope || ''}`} />
            <Field label="Positions used" value={<Badge variant={r.s_variant === 'fitted' ? 'success' : 'warning'}>{r.s_variant}</Badge>} />
            <Field label="Trips" value={fmtInt(r.i_trips)} />
            <Field label="Visits" value={fmtInt(r.i_visits)} />
            <Field label="Finished" value={fmtDateTime(r.dt_finished)} />
            <Field label="Took" value={`${fmtDuration(r.d_seconds)} · ${fmtInt(r.d_pings_per_sec)} fixes/s`} />
          </div>
          <p className="text-xs text-gray-500 mt-3">
            Every page reads this run. A new run can be produced and checked before it is published.
          </p>
        </Card>

        <Card title="OSRM road matching" icon={Network}
          actions={<button onClick={runProbe} disabled={probing}
            className="text-xs px-2.5 py-1 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300 disabled:opacity-50">
            {probing ? 'Checking…' : 'Check now'}</button>}>
          <div className="grid grid-cols-2 gap-2.5">
            <Field label="In this run" value={<Badge variant={r.s_osrm === 'ok' ? 'success' : r.s_osrm === 'off' ? 'neutral' : 'warning'}>{r.s_osrm || 'off'}</Badge>} />
            <Field label="Server" value={p.osrm_url || 'not configured'} mono />
            <Field label="Max snap" value={`${p.osrm_max_snap_m ?? '—'} m`} />
            <Field label="GPS radius" value={`${p.osrm_radius_m ?? '—'} m`} />
          </div>
          {probe && (
            <div className="mt-3">
              <Note tone={probe.status === 'ok' ? 'good' : probe.status === 'off' ? 'info' : 'warn'} title={`Probe: ${probe.status}.`}>
                {probe.status === 'off' ? 'OSRM_URL is not set.' : probe.error || `${probe.latency_ms} ms, nearest road ${probe.probe_road || '(unnamed)'}`}
              </Note>
            </div>
          )}
          <p className="text-xs text-gray-500 mt-3">
            Optional. Without it, parked-truck scatter and spikes are still fitted; with it, moving fixes are snapped to
            the road (never more than {p.osrm_max_snap_m ?? 30} m) and the road path across GPS holes names the fences a
            truck passed unseen.
          </p>
        </Card>

        <Card title="Fit settings" icon={Cpu} subtitle="Stored on the run, so any result can be reproduced exactly.">
          <div className="grid grid-cols-2 gap-2 text-xs">
            {[
              ['Standstill speed', `≤ ${p.fit_still_kmph} km/h`],
              ['Median window', `±${p.fit_window_fixes} fixes / ${fmtDuration(p.fit_window_seconds)}`],
              ['Spike distance', `≥ ${p.fit_spike_m} m`],
              ['Hysteresis', `≤ ${p.hysteresis_m} m, adaptive ${p.adaptive_band ? 'on' : 'off'}`],
              ['Band fraction', `${p.band_fraction} × inscribed radius`],
              ['Confirm dwell', `${p.confirm_seconds} s`],
              ['Escape distance', `${p.escape_m} m`],
              ['Max plausible speed', `${p.max_plausible_kmph} km/h`],
            ].map(([k, v]) => (
              <div key={k} className="bg-gray-800/40 rounded-md px-2.5 py-1.5">
                <p className="text-gray-500">{k}</p><p className="text-gray-200">{v}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {raw && (
        <Card title={`What fitting changed: raw run #${raw.i_run_id} vs published run #${d.run_id}`} icon={GitCompareArrows}
          className="mb-6"
          subtitle="The same trips, the same fences, the same detector — only the positions differ. There is no ground truth to score against, so this counts the signatures noise leaves in a ledger: re-entries within 10 minutes, crossings confirmed by distance while the truck reported zero speed, and facility visits shorter than 3 minutes.">
          {compare.loading && !compare.data ? <Spinner label="Comparing runs (reads every visit of both)" /> :
            compare.error ? <ErrorBox error={compare.error} /> : compare.data && (
              <CompareView c={compare.data} />
            )}
        </Card>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card title="Trail quality" icon={ShieldCheck}
          subtitle="A verdict per trip. Holes count against a trail only when the truck moved across them: 99.5% of this feed's 1-4 hour holes are a tracker asleep while the truck stood still.">
          <DataTable dense rows={d.quality_mix}
            onRowClick={x => navigate(`/geo/trips?quality=${x.s_quality}`)}
            columns={[
              { key: 's_quality', label: 'Verdict', render: x => <QualityBadge quality={x.s_quality} /> },
              { key: 'meaning', label: 'Meaning', render: x => <span className="text-xs text-gray-400">{QUALITY_TEXT[x.s_quality]}</span> },
              { key: 'trips', label: 'Trips', align: 'right', render: x => fmtInt(x.trips) },
              { key: 'share', label: 'Share', align: 'right', render: x => fmtPct(share(x.trips, totalTrips), 0) },
              { key: 'facility_visits', label: 'Facility visits', align: 'right', render: x => fmtInt(x.facility_visits) },
            ]} />
        </Card>
        <Card title="GPS holes" icon={EyeOff}
          subtitle="Holes of five minutes or more, split by whether the truck moved across them. Only moving holes can hide a visit.">
          <SeriesChart data={gapRows} x="bucket" height={220}
            series={[
              { key: 'stationary', label: 'Truck did not move', color: PALETTE.gray, stack: 'g' },
              { key: 'moving', label: 'Truck moved', color: PALETTE.amber, stack: 'g' },
            ]} />
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card title="Day by day" icon={BarChart3} subtitle="Fixes read, refused and corrected per day.">
          <SeriesChart data={d.daily} x="d_day" height={220} xFormat={v => fmtDayShort(v)}
            yFormat={v => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(v))}
            series={[
              { key: 'i_pings', label: 'Fixes', color: PALETTE.blue },
              { key: 'i_spikes', label: 'Spikes corrected', color: PALETTE.amber, type: 'line', axis: 'right' },
              { key: 'i_pings_rejected', label: 'Refused', color: PALETTE.red, type: 'line', axis: 'right' },
            ]} />
        </Card>
        <Card title="Why fixes were refused" icon={XCircle} iconClass="text-red-400"
          subtitle="Refused fixes never reach the detector. A teleport is a fix implying an impossible speed that nothing after it agrees with.">
          <DataTable dense rows={d.rejects}
            columns={[
              { key: 's_reason', label: 'Reason', render: x => x.s_reason.replace(/_/g, ' ') },
              { key: 'fixes', label: 'Fixes', align: 'right', render: x => fmtInt(x.fixes) },
              { key: 'trips', label: 'Trips', align: 'right', render: x => fmtInt(x.trips) },
            ]} />
          <div className="mt-4">
            <Note>
              {fmtInt(d.master.small)} active fences have an inscribed radius under 25 m and{' '}
              {fmtInt(d.master.crossing)} rings cross themselves. The first are detectable only because the band scales to
              fence size; the second are flagged on their pages.
            </Note>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <Card title="Transporters by GPS reliability" icon={Building2}
          subtitle="Carriers with 10+ trips, worst first by share of trips whose truck moved during an hour-long hole.">
          <DataTable dense rows={d.by_transporter}
            onRowClick={x => navigate(`/geo/transporters/detail?name=${encodeURIComponent(x.transporter)}`)}
            columns={[
              { key: 'transporter', label: 'Transporter', render: x => <span className="text-gray-100">{x.transporter}</span> },
              { key: 'trips', label: 'Trips', align: 'right', render: x => fmtInt(x.trips) },
              { key: 'broken', label: 'Broken', align: 'right', render: x => fmtPct(share(x.broken, x.trips), 0) },
              { key: 'good', label: 'Good', align: 'right', render: x => fmtPct(share(x.good, x.trips), 0) },
              { key: 'spikes', label: 'Spikes /1k fixes', align: 'right', render: x => fmtNum(x.pings ? (1000 * x.spikes) / x.pings : null, 2) },
            ]} />
        </Card>
        <Card title="Trips with the most unobserved movement" icon={EyeOff}>
          <DataTable dense rows={d.worst_trips} onRowClick={x => navigate(`/geo/trips/${x.i_trip_no}`)}
            columns={[
              { key: 'i_trip_no', label: 'Trip', render: x => <span className="text-blue-400">{x.i_trip_no}</span> },
              { key: 's_asset_id', label: 'Vehicle' },
              { key: 's_quality', label: 'Verdict', render: x => <QualityBadge quality={x.s_quality} /> },
              { key: 'i_moving_gap_s', label: 'Moving while silent', align: 'right', render: x => fmtDuration(x.i_moving_gap_s) },
              { key: 's_quality_reason', label: 'Why', render: x => <span className="text-xs text-gray-400">{x.s_quality_reason}</span> },
            ]} />
        </Card>
      </div>

      <Card title="All runs" icon={Server}>
        <DataTable dense rows={runs.data?.runs ?? []} loading={runs.loading}
          columns={[
            { key: 'i_run_id', label: 'Run', render: x => <span className="text-gray-100">#{x.i_run_id}</span> },
            { key: 'b_published', label: '', render: x => x.b_published ? <Badge variant="success">published</Badge> : null },
            { key: 's_status', label: 'Status', render: x => <Badge variant={x.s_status === 'ok' ? 'neutral' : x.s_status === 'running' ? 'info' : 'danger'}>{x.s_status}</Badge> },
            { key: 's_variant', label: 'Positions' },
            { key: 's_scope', label: 'Scope', render: x => <span className="text-xs text-gray-400">{x.s_scope}</span> },
            { key: 'i_visits', label: 'Visits', align: 'right', render: x => fmtInt(x.i_visits) },
            { key: 'i_spikes', label: 'Spikes', align: 'right', render: x => fmtInt(x.i_spikes) },
            { key: 's_osrm', label: 'OSRM' },
            { key: 'dt_finished', label: 'Finished', render: x => fmtDateTime(x.dt_finished) },
          ]} />
      </Card>
    </div>
  );
}

function CompareView({ c }: { c: any }) {
  const a = c.signatures.a;
  const b = c.signatures.b;
  const rows = [
    ['Visits (all fences)', a.visits, b.visits, 'none'],
    ['Crossings', a.crossings, b.crossings, 'none'],
    ['Re-entered the same fence within 10 min', a.quick_reentries, b.quick_reentries, 'down'],
    ['Crossings confirmed by distance at zero speed', a.escape_at_standstill, b.escape_at_standstill, 'down'],
    ['Facility visits shorter than 3 min', a.blink_visits, b.blink_visits, 'down'],
  ] as const;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      <div>
        <DataTable dense rows={rows.map(([label, x, y, good]) => ({ label, x, y, good }))}
          columns={[
            { key: 'label', label: 'Signature', render: r => <span className="text-gray-200">{r.label}</span> },
            { key: 'x', label: 'Raw GPS', align: 'right', render: r => fmtInt(r.x) },
            { key: 'y', label: 'Fitted', align: 'right', render: r => fmtInt(r.y) },
            { key: 'd', label: 'Change', align: 'right', render: r => {
              const pct = r.x ? (100 * (r.y - r.x)) / r.x : null;
              const cls = r.good === 'down' ? (pct != null && pct < 0 ? 'text-emerald-400' : 'text-red-400') : 'text-gray-400';
              return <span className={cls}>{pct == null ? '—' : `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`}</span>;
            } },
          ]} />
        <p className="text-xs text-gray-500 mt-3">
          {fmtInt(c.common_trips)} common trips · {fmtInt(c.matched_visits)} visits found by both ·
          entry time unchanged for {fmtInt(c.entry_shift_s.unchanged)}, moved ≤2 min for {fmtInt(c.entry_shift_s.within_2min)},
          more for {fmtInt(c.entry_shift_s.over_2min)}.
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 content-start">
        <Field label="Visits only on raw GPS" value={fmtInt(c.only_in_a.visits)} />
        <Field label="…their median stay" value={fmtDuration(c.only_in_a.facility_dwell_median_s)} />
        <Field label="Visits only on fitted GPS" value={fmtInt(c.only_in_b.visits)} />
        <Field label="…their median stay" value={fmtDuration(c.only_in_b.facility_dwell_median_s)} />
        <div className="col-span-2">
          <Note>
            A visit only raw GPS finds is usually a spike carrying a parked truck across a fence line; a visit only fitted GPS
            finds is usually one the noise had split or hidden. Open a trip from either run to inspect it fix by fix.
          </Note>
        </div>
      </div>
    </div>
  );
}
