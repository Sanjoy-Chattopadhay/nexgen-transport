/**
 * One plant: who was inside it at any moment, where, and when it overloaded.
 *
 * The page is built around a moment (?at=, the window's busiest by default).
 * The timeline shows how many tracked vehicles the plant held, split by the
 * kind of zone each was in (its innermost zone: a truck at the gate is at the
 * gate, not "in the works"); clicking it moves the moment. The map and the
 * table then show every vehicle inside at that moment -- its zone, how long it
 * had been there, where its fitted trail put it and how fresh that fix was --
 * and the zones list says which of them were over their threshold.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import L from 'leaflet';
import {
  Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis,
  YAxis,
} from 'recharts';
import {
  Activity, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Clock, Crosshair, Factory, Hexagon, Layers,
  MapPinned, Radar, Satellite, Timer, Truck, Users,
} from 'lucide-react';
import KPICard from '../../analytics/components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, DateInput, Empty, EntityLink, ErrorBox, Note, PageHeader, ScaleBadge, Spinner, Toggle,
  Toolbar, TripRef,
} from '../components/ui';
import GeoMap from '../components/map/GeoMap';
import { escapeHtml, fencePolygon } from '../components/map/layers';
import { PALETTE } from '../lib/theme';
import { fmtArea, fmtDuration, fmtInt } from '../lib/format';
import {
  KINDS, KIND_PLURAL, KIND_TEXT, KindBadge, SceneCard, fmtMin, fmtWhen, hhmm, kindColor, minutesOf, plantProof,
  toLocalInput,
} from '../components/plants';

const pad = (n: number) => String(n).padStart(2, '0');
const toDate = (v: string) => new Date(v.replace(' ', 'T'));
const fromDate = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const shift = (v: string, minutes: number) => fromDate(new Date(toDate(v).getTime() + minutes * 60000));
/** A position older than this at the moment shown is drawn as a last-known place, not a live one. */
const STALE_S = 600;

export default function PlantDetail() {
  const { siteId = '' } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const from = params.get('from') || '';
  const to = params.get('to') || '';
  const atParam = params.get('at') || '';
  const setParam = (k: string, v: string) => setParams(p => {
    const n = new URLSearchParams(p);
    if (v) n.set(k, v); else n.delete(k);
    return n;
  }, { replace: true });

  const detail = useApi(() => api.plant(siteId, { from, to }), [siteId, from, to]);
  const w = detail.data?.window;
  const at = atParam || w?.peak_at || '';
  const inside = useApi(() => (at ? api.plantInside(siteId, at) : Promise.resolve(null)), [siteId, at]);

  const [zoneSel, setZoneSel] = useState<number | null>(null);
  const [kindFilter, setKindFilter] = useState<string>('');
  const [showTracked, setShowTracked] = useState(false);
  const [map, setMap] = useState<L.Map | null>(null);
  const mapCard = useRef<HTMLDivElement>(null);

  const d = detail.data;
  const plant = d?.plant;
  const zones: any[] = useMemo(() => d?.zones ?? [], [d]);
  const insideNow = inside.data;
  const zoneInside = useMemo(() => {
    const m = new Map<number, number>();
    for (const z of insideNow?.zones ?? []) m.set(z.site_id, z.inside);
    return m;
  }, [insideNow]);

  // A zone asked for by its own site id opens selected.
  useEffect(() => { if (d?.focus) setZoneSel(d.focus.site_id); }, [d?.focus?.site_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // The plant, its zones and the vehicles at the moment, on the map.
  useEffect(() => {
    if (!map || !plant) return;
    const group = L.layerGroup().addTo(map);
    fencePolygon({ site_id: plant.site_id, name: plant.name, category: plant.category, scale: plant.scale, ring: plant.ring },
      { focus: true }).addTo(group);
    // Largest first, so a gate draws on top of the area it sits in. Works-sized
    // (campus) zones are outlines only: filled, a handful of overlapping
    // drawings of the works would bury every gate and vehicle under them.
    for (const z of [...zones].sort((a, b) => b.area_m2 - a.area_m2)) {
      if (!z.ring?.length) continue;
      const n = zoneInside.get(z.site_id) ?? 0;
      const over = n >= z.threshold;
      const sel = zoneSel === z.site_id;
      const large = z.scale === 'campus';
      const poly = L.polygon(z.ring, {
        color: kindColor(z.kind), weight: sel ? 3 : over ? 2 : 1, opacity: large && !sel ? 0.5 : 0.95,
        fillColor: kindColor(z.kind), fillOpacity: large ? (sel ? 0.08 : 0) : over ? 0.4 : n ? 0.22 : 0.05,
        dashArray: large || !n ? '3 3' : undefined,
      });
      poly.bindTooltip(`${escapeHtml(z.name)} · ${escapeHtml(KIND_TEXT[z.kind] || z.kind)}<br/>${n} inside at ${hhmm(at)}`
        + ` · usual ${z.usual ?? '—'} · overloaded at ${z.threshold}`, { className: 'geo-tip', sticky: true });
      poly.on('click', () => setZoneSel(s => (s === z.site_id ? null : z.site_id)));
      poly.addTo(group);
    }
    for (const v of insideNow?.vehicles ?? []) {
      const p = v.position;
      if (!p) continue;
      const stale = p.age_s > STALE_S;
      const m = L.circleMarker([p.lat, p.lon], {
        radius: 5, color: stale ? kindColor(v.kind) : PALETTE.markerOutline, weight: stale ? 1.5 : 1,
        fillColor: kindColor(v.kind), fillOpacity: stale ? 0.15 : 0.95, dashArray: stale ? '2 2' : undefined,
      });
      m.bindTooltip(`<b>${escapeHtml(v.vehicle)}</b> · ${escapeHtml(v.zone?.name ?? 'elsewhere in the plant')}<br/>`
        + `in the plant ${fmtDuration(v.inside_s)}${v.entry_observed ? '' : '+'} · ${p.role === 'move' ? 'moving' : 'standing'}`
        + (stale ? `<br/>last fix ${fmtDuration(p.age_s)} before ${hhmm(at)}` : ''), { className: 'geo-tip' });
      m.on('click', () => navigate(`/geo/trips/${p.trip}`));
      m.addTo(group);
    }
    return () => { group.remove(); };
  }, [map, plant, zones, insideNow, zoneInside, zoneSel, at, navigate]);

  // Frame the plant once per plant.
  useEffect(() => {
    if (!map || !plant?.ring?.length) return;
    map.fitBounds(L.latLngBounds(plant.ring), { padding: [30, 30], maxZoom: 17 });
  }, [map, plant?.site_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (detail.loading && !d) return <Spinner label="Reading the plant" />;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={detail.reload} />;
  if (!d || !plant) return null;

  const sid = plant.site_id;
  const sel = zones.find(z => z.site_id === zoneSel) || null;
  const vehicles: any[] = (insideNow?.vehicles ?? []).filter((v: any) => !kindFilter || v.kind === kindFilter);
  const moment = (v: string) => setParam('at', v);
  const focusScene = (s: any) => {
    moment(s.peak_at);
    setZoneSel(s.zone.site_id === sid ? null : s.zone.site_id);
    mapCard.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const positioned = insideNow?.positioned ?? 0;
  const stale = (insideNow?.vehicles ?? []).filter((v: any) => v.position && v.position.age_s > STALE_S).length;

  return (
    <div className="animate-fade-in">
      <PageHeader back={{ to: '/geo/plants', label: 'Plants & congestion' }}
        title={plant.name}
        subtitle={`Site ${sid}${plant.type ? ` · ${plant.type}` : ''} · ${fmtArea(plant.area_m2)} · ${zones.length} zones${plant.copies.length > 1 ? ` · drawn identically as sites ${plant.copies.join(', ')}` : ''}`}
        badges={<ScaleBadge scale={plant.scale} />}
        actions={<EntityLink to={`/geo/geofences/${sid}`} className="text-sm">The fence →</EntityLink>} />

      {d.focus && (
        <div className="mb-4"><Note title={`${d.focus.name} is a ${KIND_TEXT[d.focus.kind]?.toLowerCase() || 'zone'} of this plant.`}>
          It is selected below; the figures at the top are the whole plant's.</Note></div>
      )}

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <Toolbar>
          <DateInput label="From" value={from} onChange={v => setParam('from', v)} />
          <DateInput label="To" value={to} onChange={v => setParam('to', v)} />
          {(from || to) && <button className="text-xs text-blue-400" onClick={() => setParams(new URLSearchParams(), { replace: true })}>reset</button>}
        </Toolbar>
        <span className="text-xs text-gray-500">Showing {fmtWhen(w.from)} → {fmtWhen(w.to)} · one point every {w.resolution_minutes} min</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4 rounded-xl border border-gray-800 bg-gray-900 px-3 py-2">
        <Clock className="w-4 h-4 text-amber-400" />
        <span className="text-sm text-gray-300">Moment:</span>
        <span className="text-sm font-semibold text-white tabular">{fmtWhen(at)}</span>
        {!atParam && <span className="text-xs text-gray-500">(the busiest of the window)</span>}
        <div className="flex items-center gap-1 ml-2">
          <MomentButton icon={ChevronsLeft} title="1 hour earlier" onClick={() => moment(shift(at, -60))} />
          <MomentButton icon={ChevronLeft} title="10 minutes earlier" onClick={() => moment(shift(at, -10))} />
          <input type="datetime-local" value={toLocalInput(at)} onChange={e => e.target.value && moment(`${e.target.value.replace('T', ' ')}:00`)}
            className="bg-field border border-gray-700 rounded-lg text-sm text-gray-200 px-2 py-1 [color-scheme:dark]" />
          <MomentButton icon={ChevronRight} title="10 minutes later" onClick={() => moment(shift(at, 10))} />
          <MomentButton icon={ChevronsRight} title="1 hour later" onClick={() => moment(shift(at, 60))} />
        </div>
        <button onClick={() => moment(w.peak_at)} className="ml-2 inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300">
          <Crosshair className="w-3.5 h-3.5" /> busiest moment
        </button>
        <span className="text-xs text-gray-500 ml-auto">Click the timeline to move the moment.</span>
      </div>

      <ProofGrid className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-6 gap-4 mb-6">
        <KPICard label={`Inside at ${hhmm(at)}`} value={insideNow ? fmtInt(insideNow.count) : '…'} icon={Truck}
          color={insideNow && insideNow.count >= w.threshold ? 'red' : 'blue'}
          proof={insideNow ? plantProof('inside', { site_id: sid, at: insideNow.at }, insideNow.count) : undefined} />
        <KPICard label={`Tracked fleet-wide at ${hhmm(at)}`} value={insideNow ? fmtInt(insideNow.tracked) : '…'} icon={Satellite} color="cyan"
          proof={insideNow ? plantProof('tracked', { at: insideNow.at }, insideNow.tracked) : undefined} />
        <KPICard label={`Busiest: ${fmtWhen(w.peak_at)}`} value={fmtInt(w.peak)} icon={Activity} color="amber"
          proof={plantProof('peak', { site_id: sid, from: w.from, to: w.to }, w.peak)} />
        <KPICard label={`Usual level · overloaded at ${w.threshold}`} value={plant.usual ?? '—'} icon={Layers} color="purple"
          proof={plantProof('usual', { site_id: sid }, plant.usual)} />
        <KPICard label="Usual stay in the plant" value={fmtMin(plant.stay_p50_s)} icon={Timer} color="green"
          proof={plantProof('stays', { site_id: sid }, minutesOf(plant.stay_p50_s))} />
        <KPICard label="Overload scenes in the window" value={fmtInt(d.scenes.length)} icon={Radar}
          color={d.scenes.some((s: any) => s.severity === 'high') ? 'red' : 'amber'}
          proof={plantProof('scenes', { site_id: sid, from: w.from, to: w.to }, d.scenes.length)} />
      </ProofGrid>

      <Card title="Vehicles inside, by where they were" icon={Activity} className="mb-6"
        subtitle="Tracked vehicles inside the plant at each point, each counted once in the innermost zone it was in. Shaded bands are the overloads the scan found here (coloured by the kind of zone); the dashed lines are the plant's usual level and the count it is overloaded at. Click anywhere to move the moment."
        actions={<Toggle checked={showTracked} onChange={setShowTracked} label="Vehicles tracked fleet-wide" />}>
        <OccupancyChart rows={d.timeline} usual={w.usual} threshold={w.threshold} scenes={d.scenes} at={at}
          showTracked={showTracked} onPick={moment} />
      </Card>

      <div ref={mapCard} className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6 scroll-mt-4">
        <Card title={`Where they were at ${hhmm(at)}`} icon={MapPinned} className="xl:col-span-2"
          subtitle={`Each dot is a vehicle at its last fitted fix at or before the moment, coloured by its zone; a hollow dashed dot was last seen more than ${STALE_S / 60} minutes earlier. Zones holding vehicles are filled; a thick outline is over its threshold. Click a zone to select it, a vehicle to open its trip.`}>
          <GeoMap height={480} onReady={setMap} />
          <p className="text-xs text-gray-500 mt-2">
            {insideNow ? <>{positioned} of {insideNow.count} vehicles placed from their fitted trails
              {stale > 0 && <>; {stale} last seen more than {STALE_S / 60} min before the moment</>}.</> : 'Loading…'}
          </p>
        </Card>
        <Card title="At that moment" icon={Layers}
          subtitle="Vehicles inside by the kind of zone they were in, and every zone holding vehicles against its threshold.">
          {inside.error ? <ErrorBox error={inside.error} onRetry={inside.reload} /> : !insideNow ? <Spinner /> : (
            <>
              <div className="grid grid-cols-2 gap-2 mb-4">
                {KINDS.filter(k => insideNow.by_kind[k]).map(k => (
                  <button key={k} onClick={() => setKindFilter(f => (f === k ? '' : k))}
                    className={`rounded-lg border px-3 py-2 text-left ${kindFilter === k ? 'border-blue-500/60 bg-blue-600/10' : 'border-gray-800 bg-gray-800/40 hover:border-gray-700'}`}>
                    <KindBadge kind={k} />
                    <p className="text-xl font-bold text-white tabular mt-0.5">{insideNow.by_kind[k]}</p>
                  </button>
                ))}
              </div>
              <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
                {fullest(insideNow.zones.filter((z: any) => z.scale !== 'campus')).map((z: any) => {
                  const over = z.inside >= z.threshold;
                  return (
                    <button key={z.site_id} onClick={() => setZoneSel(s => (s === z.site_id ? null : z.site_id))}
                      className={`w-full flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs ${zoneSel === z.site_id ? 'bg-blue-600/15 ring-1 ring-blue-500/50' : 'hover:bg-gray-800/60'}`}>
                      <span className="flex items-center gap-2 min-w-0">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: kindColor(z.kind) }} />
                        <span className="truncate text-gray-200" title={z.name}>{z.name}</span>
                      </span>
                      <span className={`tabular shrink-0 ${over ? 'text-red-400 font-semibold' : 'text-gray-400'}`}>
                        {z.inside} <span className="text-gray-600">/ {z.threshold}</span>
                      </span>
                    </button>
                  );
                })}
                {insideNow.zones.length === 0 && <p className="text-xs text-gray-500">No zone held a vehicle.</p>}
              </div>
              {insideNow.zones.some((z: any) => z.scale === 'campus') && (
                <details className="mt-3">
                  <summary className="text-xs text-gray-400 cursor-pointer">Works-sized areas inside the plant ({insideNow.zones.filter((z: any) => z.scale === 'campus').length})</summary>
                  <div className="space-y-1 mt-2">
                    {fullest(insideNow.zones.filter((z: any) => z.scale === 'campus')).map((z: any) => (
                      <button key={z.site_id} onClick={() => setZoneSel(s => (s === z.site_id ? null : z.site_id))}
                        className={`w-full flex items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-xs ${zoneSel === z.site_id ? 'bg-blue-600/15' : 'hover:bg-gray-800/60'}`}>
                        <span className="truncate text-gray-300" title={z.name}>{z.name}</span>
                        <span className={`tabular shrink-0 ${z.inside >= z.threshold ? 'text-red-400 font-semibold' : 'text-gray-400'}`}>{z.inside} <span className="text-gray-600">/ {z.threshold}</span></span>
                      </button>
                    ))}
                  </div>
                </details>
              )}
              <p className="text-xs text-gray-500 mt-3">Inside / the count each zone is overloaded at, fullest first. The counts are each zone's own and are never added together: a gate inside a larger area is in both.</p>
            </>
          )}
        </Card>
      </div>

      <Card title={`Who was inside at ${fmtWhen(at)}`} icon={Users} className="mb-6"
        subtitle="Every tracked vehicle inside the plant at that moment, longest inside first. In the plant for counts from its observed entry; + means the trail began with it already inside, so it had been there at least that long. The GPS column says how fresh its position is."
        actions={kindFilter ? <button className="text-xs text-blue-400" onClick={() => setKindFilter('')}>only {KIND_PLURAL[kindFilter].toLowerCase()} · show all</button> : undefined}>
        {!insideNow ? <Spinner /> : vehicles.length === 0 ? <Empty>No tracked vehicle was inside at this moment.</Empty> : (
          <DataTable dense rows={vehicles} rowKey={(r: any) => r.vehicle}
            onRowClick={(r: any) => r.trip && navigate(`/geo/trips/${r.trip}`)}
            columns={[
              { key: 'vehicle', label: 'Vehicle', render: (r: any) => <Link to={`/geo/vehicles/${encodeURIComponent(r.vehicle)}`} onClick={e => e.stopPropagation()} className="text-gray-100 hover:text-blue-300">{r.vehicle}</Link> },
              { key: 'where', label: 'Where', render: (r: any) => (
                <div className="min-w-0">
                  <KindBadge kind={r.kind} />
                  {r.zone && <p className="text-xs text-gray-500 truncate max-w-[16rem]" title={r.zone.name}>{r.zone.name}</p>}
                </div>
              ) },
              { key: 'zone_for', label: 'In the zone for', align: 'right', render: (r: any) => r.zone_inside_s != null ? fmtDuration(r.zone_inside_s) : '—' },
              { key: 'plant_for', label: 'In the plant for', align: 'right', render: (r: any) => <span>{fmtDuration(r.inside_s)}{r.entry_observed ? '' : '+'}</span> },
              { key: 'gps', label: 'GPS at the moment', render: (r: any) => {
                const p = r.position;
                if (!p) return <span className="text-xs text-gray-600">no fitted fix</span>;
                const old = p.age_s > STALE_S;
                return (
                  <span className={`text-xs ${old ? 'text-amber-400' : 'text-gray-300'}`}>
                    {p.role === 'move' ? 'moving' : 'standing'} · {p.age_s <= 120 ? 'fix just before' : `last fix ${fmtDuration(p.age_s)} earlier`}
                  </span>
                );
              } },
              { key: 'confidence', label: '', render: (r: any) => (
                r.uncertain ? <Badge variant="warning" title={`Entry or exit inside a GPS gap (in ${fmtDuration(r.enter_gap_s)}, out ${fmtDuration(r.exit_gap_s)})`}>gap</Badge>
                  : r.open ? <Badge variant="neutral" title="The trail ended with it still inside">trail ended</Badge> : null
              ) },
              { key: 'transporter', label: 'Transporter', render: (r: any) => <span className="text-xs text-gray-400">{r.transporter || '—'}</span> },
              { key: 'trip', label: 'Trip', render: (r: any) => r.trip ? <TripRef trip={r.trip} trips={r.trips} /> : '—' },
            ]} />
        )}
      </Card>

      <Card title="Zones" icon={Hexagon} className="mb-6"
        subtitle="Every visited fence inside the plant, by kind (named from its fence name). Usual is what the zone holds nine minutes in ten while in use; it is overloaded at its threshold. Busiest is within the window. Select a zone for the proof of its figures.">
        <DataTable dense rows={zones} rowKey={(r: any) => r.site_id}
          onRowClick={(r: any) => setZoneSel(s => (s === r.site_id ? null : r.site_id))}
          columns={[
            { key: 'kind', label: 'Kind', render: (r: any) => <KindBadge kind={r.kind} /> },
            { key: 'name', label: 'Zone', render: (r: any) => (
              <span className={`text-xs ${zoneSel === r.site_id ? 'text-blue-300 font-semibold' : 'text-gray-100'}`}>
                {r.name} <Link to={`/geo/geofences/${r.site_id}`} onClick={e => e.stopPropagation()} className="text-gray-500 hover:text-blue-300">#{r.site_id}</Link>
                {r.copies.length > 1 && <span className="text-gray-600"> · drawn {r.copies.length}×</span>}
              </span>
            ) },
            { key: 'scale', label: 'Scale', render: (r: any) => <ScaleBadge scale={r.scale} /> },
            { key: 'now', label: `At ${hhmm(at)}`, align: 'right', render: (r: any) => {
              const n = zoneInside.get(r.site_id) ?? 0;
              return <span className={`tabular ${n >= r.threshold ? 'text-red-400 font-semibold' : n ? 'text-gray-100' : 'text-gray-600'}`}>{n}</span>;
            } },
            { key: 'usual', label: 'Usual', align: 'right', render: (r: any) => <span className="tabular">{r.usual ?? '—'}{r.baseline_thin && <span className="text-xs text-amber-400 ml-1" title="Under 6 hours of busy time: the kind's minimum applies">thin</span>}</span> },
            { key: 'threshold', label: 'Overloaded at', align: 'right', render: (r: any) => <span className="tabular">{r.threshold}</span> },
            { key: 'peak', label: 'Busiest', align: 'right', render: (r: any) => <span className="tabular">{r.peak} <span className="text-xs text-gray-500">{r.peak_at ? fmtWhen(r.peak_at) : ''}</span></span> },
            { key: 'stay', label: 'Usual stay', align: 'right', render: (r: any) => fmtMin(r.stay_p50_s) },
            { key: 'scenes', label: 'Scenes', align: 'right', render: (r: any) => r.scenes ? <Badge variant="warning">{r.scenes}</Badge> : <span className="text-gray-600">0</span> },
          ]} />
        {sel && (
          <div className="mt-4 pt-4 border-t border-gray-800">
            <p className="text-sm text-gray-300 mb-3 flex items-center gap-2"><KindBadge kind={sel.kind} /> <b className="text-white">{sel.name}</b>
              <button className="text-xs text-blue-400 ml-2" onClick={() => setZoneSel(null)}>close</button></p>
            <ProofGrid className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
              <KPICard label={`Inside at ${hhmm(at)}`} value={zoneInside.get(sel.site_id) ?? 0} icon={Truck} color="blue"
                proof={insideNow ? plantProof('inside', { site_id: sel.site_id, at: insideNow.at }, zoneInside.get(sel.site_id) ?? 0) : undefined} />
              <KPICard label={`Busiest in the window${sel.peak_at ? ` · ${fmtWhen(sel.peak_at)}` : ''}`} value={sel.peak} icon={Activity} color="amber"
                proof={plantProof('peak', { site_id: sel.site_id, from: w.from, to: w.to }, sel.peak)} />
              <KPICard label={`Usual level · overloaded at ${sel.threshold}`} value={sel.usual ?? '—'} icon={Layers} color="purple"
                proof={plantProof('usual', { site_id: sel.site_id }, sel.usual)} />
              <KPICard label="Usual stay" value={fmtMin(sel.stay_p50_s)} icon={Timer} color="green"
                proof={plantProof('stays', { site_id: sel.site_id }, minutesOf(sel.stay_p50_s))} />
            </ProofGrid>
          </div>
        )}
      </Card>

      <Card title="Overloads in this window" icon={Radar}
        subtitle="What the scan found at this plant and its zones in the window, in time order. Each opens to the proof of its numbers; 'Who was inside at the peak' moves the page to that moment.">
        {d.scenes.length === 0 ? <Empty>The plant and every zone stayed within their usual levels in this window.</Empty> : (
          <div className="space-y-3">
            {d.scenes.map((s: any) => <SceneCard key={s.id} scene={s} showPlant={false} onFocus={focusScene} />)}
          </div>
        )}
      </Card>
      <p className="text-xs text-gray-600 mt-4 flex items-center gap-1.5"><Factory className="w-3.5 h-3.5" />
        Counted from run {d.run_id}, computed {fmtWhen(d.computed_at)}. Only vehicles on trips with GPS can be seen.</p>
    </div>
  );
}

/** Zones by how close they are to their threshold, then by how many they hold. */
function fullest(zones: any[]): any[] {
  return [...zones].sort((a, b) => (b.inside / b.threshold) - (a.inside / a.threshold) || b.inside - a.inside);
}

/** Ticks at every midnight and every few hours between: day boundaries always labelled. */
function timeTicks(a: number, b: number): number[] {
  const hours = (b - a) / 3600000;
  const every = hours <= 30 ? 3 : hours <= 60 ? 6 : hours <= 240 ? 12 : 24;
  const t = new Date(a);
  t.setMinutes(0, 0, 0);
  t.setHours(Math.ceil(t.getHours() / every) * every);
  const out: number[] = [];
  for (let x = t.getTime(); x <= b; x += every * 3600000) out.push(x);
  return out;
}

function MomentButton({ icon: Icon, title, onClick }: { icon: typeof ChevronLeft; title: string; onClick: () => void }) {
  return (
    <button onClick={onClick} title={title} className="p-1.5 rounded-md text-gray-400 hover:text-white hover:bg-gray-800">
      <Icon className="w-4 h-4" />
    </button>
  );
}

function OccupancyChart({ rows, usual, threshold, scenes, at, showTracked, onPick }: {
  rows: any[]; usual: number | null; threshold: number; scenes: any[]; at: string; showTracked: boolean;
  onPick: (v: string) => void;
}) {
  const data = useMemo(() => rows.map(r => ({ ...r, x: toDate(r.t).getTime() })), [rows]);
  const kinds = useMemo(() => KINDS.filter(k => rows.some(r => r[k] > 0)), [rows]);
  if (!rows.length) return <Empty>No vehicles inside in this window.</Empty>;
  const atX = at ? toDate(at).getTime() : null;
  const tick = { fill: PALETTE.axis, fontSize: 12 };
  const ticks = timeTicks(data[0].x, data[data.length - 1].x);
  const fmtX = (x: number) => {
    const t = new Date(x);
    return t.getHours() === 0 && t.getMinutes() === 0
      ? t.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }) : `${pad(t.getHours())}:00`;
  };
  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={data} margin={{ top: 12, right: 12, left: -8, bottom: 0 }} style={{ cursor: 'crosshair' }}
        onClick={(e: any) => {
          const i = Number(e?.activeTooltipIndex ?? e?.activeIndex);
          const r = Number.isFinite(i) ? data[i] : null;
          if (r) onPick(r.t);
        }}>
        <CartesianGrid stroke={PALETTE.grid} vertical={false} />
        <XAxis dataKey="x" type="number" scale="time" domain={['dataMin', 'dataMax']} ticks={ticks} tickFormatter={fmtX}
          tick={tick} axisLine={{ stroke: PALETTE.grid }} tickLine={false} minTickGap={20} />
        <YAxis allowDecimals={false} tick={tick} axisLine={false} tickLine={false} width={44} />
        <Tooltip content={<TimelineTip kinds={kinds} />} cursor={{ stroke: PALETTE.axis, strokeDasharray: '3 3' }} />
        <Legend wrapperStyle={{ fontSize: 12, color: PALETTE.legend }} iconSize={10} />
        {scenes.map(s => (
          <ReferenceArea key={s.id} x1={toDate(s.start).getTime()} x2={toDate(s.end).getTime()}
            fill={kindColor(s.kind)} fillOpacity={0.14} stroke="none" ifOverflow="hidden" />
        ))}
        {kinds.map(k => (
          <Area key={k} type="stepAfter" dataKey={k} stackId="k" name={KIND_PLURAL[k]} stroke={kindColor(k)}
            fill={kindColor(k)} fillOpacity={0.5} strokeWidth={1} isAnimationActive={false} />
        ))}
        {showTracked && <Line dataKey="tracked" name="Tracked fleet-wide" stroke={PALETTE.gray} strokeDasharray="4 4"
          dot={false} isAnimationActive={false} />}
        {usual != null && <ReferenceLine y={usual} stroke={PALETTE.blue} strokeDasharray="6 4"
          label={{ value: `usual ${usual}`, fill: PALETTE.axis, fontSize: 12, position: 'insideBottomLeft' }} />}
        <ReferenceLine y={threshold} stroke={PALETTE.red} strokeDasharray="6 4"
          label={{ value: `overloaded at ${threshold}`, fill: PALETTE.axis, fontSize: 12, position: 'insideTopLeft' }} />
        {atX != null && <ReferenceLine x={atX} stroke={PALETTE.amber} strokeWidth={2} />}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function TimelineTip({ active, payload, kinds }: any) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="rounded-lg border px-3 py-2 text-xs" style={{ background: PALETTE.tooltipBg, borderColor: PALETTE.tooltipBorder, color: PALETTE.tooltipText }}>
      <p className="font-semibold mb-1">{fmtWhen(r.t)}</p>
      <p>Inside: <b>{r.total}</b> <span style={{ color: PALETTE.legend }}>(up to {r.max} in the next interval)</span></p>
      {(kinds as string[]).filter(k => r[k]).map(k => (
        <p key={k} className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full" style={{ background: kindColor(k) }} />{KIND_PLURAL[k]}: {r[k]}
        </p>
      ))}
      <p style={{ color: PALETTE.legend }} className="mt-1">Tracked fleet-wide: {r.tracked} · click to show this moment</p>
    </div>
  );
}
