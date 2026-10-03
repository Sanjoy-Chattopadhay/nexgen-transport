/**
 * A trip's detailed report: loading, transit and unloading, and everything
 * in between, as a number line, as phase totals, in 3D, and as a table that
 * downloads.
 */
import { lazy, Suspense, useMemo, useState } from 'react';
import { Box, Clock, Download, Factory, Gauge, PackageCheck, PackageOpen, Route, Ruler, Square, Timer, WifiOff } from 'lucide-react';
import { api, tripUrls } from '../../lib/api';
import { useApi } from '../../hooks/useApi';
import { Card, DataTable, ErrorBox, KPI, Note, Pagination, Spinner } from '../ui';
import { KPIGrid } from '../drill';
import NumberLine, { type Segment } from './NumberLine';
import { segmentColor, PHASE_LABEL, KIND_TEXT } from './phaseColors';
import { fmtDateTime, fmtDuration, fmtKm, fmtNum, fmtPct, share } from '../../lib/format';

const SpaceTimeCube = lazy(() => import('../three/SpaceTimeCube'));

function SegmentList({ segments, onSelect }: { segments: Segment[]; onSelect?: (s: Segment) => void }) {
  const [page, setPage] = useState(1);
  const size = 12;
  const pages = Math.max(1, Math.ceil(segments.length / size));
  return (
    <>
      <DataTable dense rows={segments.slice((page - 1) * size, page * size)} rowKey={r => r.seq}
        onRowClick={onSelect} empty="Nothing in this phase"
        columns={[
          { key: 'seq', label: '#', render: r => <span className="text-gray-500">{r.seq}</span> },
          { key: 'phase', label: 'Phase', render: r => <span className="text-xs">{PHASE_LABEL[r.phase]}</span> },
          { key: 'kind', label: 'What', render: r => (
            <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
              <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: segmentColor(r.phase, r.kind) }} />
              {KIND_TEXT[r.kind] || r.kind}
            </span>) },
          { key: 'name', label: 'Place', render: r => <span className="text-xs text-gray-300">{r.name || ''}</span> },
          { key: 'start', label: 'From', render: r => <span className="text-xs">{fmtDateTime(r.start)}</span> },
          { key: 'end', label: 'To', render: r => <span className="text-xs">{r.open ? <span className="text-amber-400">still there</span> : fmtDateTime(r.end)}</span> },
          { key: 'duration_s', label: 'For', align: 'right', render: r => <span className="text-gray-100">{fmtDuration(r.duration_s)}</span> },
          { key: 'km', label: 'Km', align: 'right', render: r => r.km_from == null ? '—' : (
            <span className="text-xs tabular">{r.km_from.toFixed(1)} → {r.km_to!.toFixed(1)}</span>) },
        ]} />
      <Pagination page={page} pages={pages} total={segments.length} onPage={setPage} />
    </>
  );
}

export default function TripReport({ tripNo, track, onFocus }: {
  tripNo: string;
  /** The /track answer, for the 3D view; null while loading. */
  track: any | null;
  /** Pan the map to a moment of the trip. */
  onFocus?: (t: number) => void;
}) {
  const rep = useApi(() => api.tripReport(tripNo), [tripNo]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [show3d, setShow3d] = useState(true);
  const segs: Segment[] = rep.data?.segments ?? [];
  const by = useMemo(() => {
    const f = (phase?: string, kind?: string) => segs.filter(s => (!phase || s.phase === phase) && (!kind || s.kind === kind));
    return f;
  }, [segs]);

  if (rep.loading && !rep.data) return <Card title="Trip report" icon={Ruler}><Spinner label="Laying out the trip" /></Card>;
  if (rep.error) return <Card title="Trip report" icon={Ruler}><ErrorBox error={rep.error} onRetry={rep.reload} /></Card>;
  if (!rep.data) return null;
  const s = rep.data.summary;
  const loaded = s.shape === 'loaded';
  const select = (seg: Segment) => {
    setSelected(seg.seq);
    const t = new Date(seg.start.replace(' ', 'T')).getTime();
    setCursor(t);
    onFocus?.(t);
  };
  const list = (phase?: string, kind?: string) => () => <SegmentList segments={by(phase, kind)} onSelect={select} />;
  const transit = s.transit_s || 0;

  return (
    <Card title="Trip report — loading, transit, unloading" icon={Ruler} className="mb-6"
      subtitle={loaded
        ? 'The whole trip from its first GPS fix to its last, cut at the loading place (the first place it was at) and the unloading place (the last): every second belongs to one segment, so the segments add up to the trip. Kilometres are on the fitted trail, the same as the trip’s distance. Hover the line for detail; drag it to move the truck in 3D; click a segment to see it on the map.'
        : 'This trip saw fewer than two places, so it has no loading and unloading to separate: the line shows where its GPS time went.'}
      actions={<div className="flex items-center gap-1.5">
        <a href={tripUrls.report(tripNo, 'csv')} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-gray-800 text-gray-300 hover:bg-gray-700">
          <Download className="w-3.5 h-3.5" /> CSV</a>
        <a href={tripUrls.report(tripNo, 'xlsx')} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md bg-gray-800 text-gray-300 hover:bg-gray-700">
          <Download className="w-3.5 h-3.5" /> Excel</a>
      </div>}>

      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-5">
        <KPI label="Loading" value={fmtDuration(s.loading_s)} icon={PackageCheck} color="purple"
          hint={s.loading_site ? `${s.loading_in_seen === false ? '≥ ' : ''}${s.loading_site}` : 'not seen'} details={list('loading')} />
        <KPI label="Transit" value={fmtDuration(s.transit_s)} icon={Route} color="cyan"
          hint={s.transit_km != null ? `${fmtKm(s.transit_km)}${s.transit_kmph ? ` · ${fmtNum(s.transit_kmph)} km/h driving` : ''}` : undefined}
          details={list('transit')} />
        <KPI label="Driving" value={fmtDuration(s.transit_moving_s)} icon={Gauge} color="blue"
          hint={loaded ? `${fmtPct(share(s.transit_moving_s, transit), 0)} of transit` : undefined} details={list('transit', 'moving')} />
        <KPI label="Stopped on the way" value={fmtDuration(s.transit_stop_s)} icon={Square} color="amber"
          hint={loaded ? `${s.transit_stops} stops · ${fmtPct(share(s.transit_stop_s, transit), 0)}` : undefined} details={list('transit', 'stop')} />
        <KPI label="Halts at places" value={fmtDuration(s.transit_halt_s)} icon={Factory} color="green"
          hint={loaded ? `${s.transit_halts} intermediate places` : undefined} details={list('transit', 'halt')} />
        <KPI label="GPS silent, moving" value={fmtDuration(s.transit_silent_s)} icon={WifiOff} color={s.transit_silent_s ? 'red' : 'gray'}
          hint={s.transit_silent_km ? `${fmtKm(s.transit_silent_km)} unobserved` : undefined} details={list('transit', 'silent')} />
        <KPI label="Unloading" value={`${fmtDuration(s.unloading_s)}${s.unloading_open ? '+' : ''}`} icon={PackageOpen} color="cyan"
          hint={s.unloading_site ? `${s.unloading_site}${s.unloading_open ? ' · still there' : ''}` : 'not seen'} details={list('unloading')} />
        <KPI label="Whole trip" value={fmtDuration(s.span_s)} icon={Clock} color="gray"
          hint={`${fmtKm(s.km)} · ${fmtDuration(s.before_s)} before, ${fmtDuration(s.after_s)} after`} details={list()} />
      </KPIGrid>

      {!loaded && <div className="mb-4"><Note tone="warn">One place, or none, was seen on this trip.</Note></div>}

      <NumberLine segments={segs} cursor={cursor} onCursor={t => { setCursor(t); }} onSelect={select} selected={selected} />

      <div className="mt-5 space-y-5">
        <div className="min-w-0">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs text-gray-400 flex items-center gap-1.5"><Box className="w-3.5 h-3.5" /> The trip in space and time</p>
            <button onClick={() => setShow3d(v => !v)} className="text-xs text-blue-400 hover:text-blue-300">{show3d ? 'hide 3D' : 'show 3D'}</button>
          </div>
          {show3d && (track?.points?.length ? (
            <Suspense fallback={<Spinner label="Loading the 3D view" />}>
              <div className="rounded-lg border border-gray-800 bg-gray-950/40">
                <SpaceTimeCube points={track.points} segments={segs} cursor={cursor} onCursor={setCursor} height={540} />
              </div>
            </Suspense>
          ) : <Spinner label="Loading the track" />)}
        </div>
        <div className="min-w-0">
          <p className="text-xs text-gray-400 mb-2 flex items-center gap-1.5"><Timer className="w-3.5 h-3.5" /> Every segment, in order</p>
          <SegmentList segments={segs} onSelect={select} />
          {rep.data.fleet?.gate_out && (
            <p className="text-xs text-gray-500 mt-3">
              Fleet system: gate-out {fmtDateTime(rep.data.fleet.gate_out)}{rep.data.fleet.ata ? ` · ATA ${fmtDateTime(rep.data.fleet.ata)}` : ''}
              {' '}· lane {rep.data.fleet.origin || '?'} → {rep.data.fleet.destination || '?'}
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
