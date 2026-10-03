import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import L from 'leaflet';
import { Activity, AlertTriangle, Factory, Gauge, Radio, Truck } from 'lucide-react';
import { api } from '../lib/api';
import { usePolling } from '../hooks/useApi';
import { Badge, Card, DataTable, KPI, Note, PageHeader, SearchInput } from '../components/ui';
import { KPIGrid } from '../components/drill';
import GeoMap from '../components/map/GeoMap';
import { escapeHtml, fencePolygon } from '../components/map/layers';
import { PALETTE } from '../components/charts';
import { fmtDateTime, fmtDuration, fmtInt, fmtTime } from '../lib/format';

const RING_ZOOM = 12;

function vehicleColor(v: any): string {
  if (v.cat === 'restricted' || v.cat === 'high_risk') return PALETTE.red;
  if (v.at_site) return PALETTE.green;
  if (v.moving) return PALETTE.blue;
  return PALETTE.stopped;
}

export default function LiveMap() {
  const [map, setMap] = useState<L.Map | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const maxId = useRef(0);
  const vehicles = usePolling(() => api.liveVehicles(), 3000);
  const stats = usePolling(() => api.liveStats(), 5000);
  const trail = usePolling(() => (selected ? api.liveTrail(selected) : Promise.resolve(null)), 10000, [selected]);

  // Incremental event tail.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await api.liveEvents(maxId.current);
        if (!alive || !d.events.length) return;
        maxId.current = Math.max(maxId.current, d.max_id);
        setEvents(prev => [...d.events, ...prev].slice(0, 300));
      } catch { /* keep the last tail */ }
    };
    tick();
    const id = window.setInterval(tick, 3000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  // Vehicles.
  useEffect(() => {
    if (!map || !vehicles.data) return;
    const group = L.layerGroup().addTo(map);
    const needle = q.trim().toLowerCase();
    for (const v of vehicles.data.vehicles) {
      if (needle && !`${v.asset} ${v.site_name || ''}`.toLowerCase().includes(needle)) continue;
      const m = L.circleMarker([v.lat, v.lon], {
        radius: v.asset === selected ? 7 : 4, color: PALETTE.markerOutline, weight: 1,
        fillColor: vehicleColor(v), fillOpacity: 0.95,
      });
      m.bindTooltip(`<b>${escapeHtml(v.asset)}</b><br>${v.speed ?? 0} km/h · ${escapeHtml(fmtTime(v.ts))}` +
        (v.site_name ? `<br>${escapeHtml(v.site_name)}` : ''), { className: 'geo-tip' });
      m.on('click', () => setSelected(v.asset));
      m.addTo(group);
    }
    return () => { group.remove(); };
  }, [map, vehicles.data, selected, q]);

  // Fence outlines for the viewport, once zoomed in far enough to read them.
  useEffect(() => {
    if (!map) return;
    const group = L.layerGroup().addTo(map);
    let token = 0;
    const refresh = async () => {
      group.clearLayers();
      if (map.getZoom() < RING_ZOOM) return;
      const b = map.getBounds();
      const mine = ++token;
      try {
        const d = await api.fenceRingsInView([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], 600);
        if (mine !== token) return;
        for (const f of d.fences) {
          fencePolygon({ site_id: f.site, name: f.name, category: f.cat, scale: f.scale,
            ring: f.ring.map((p: number[]) => [p[1], p[0]]) }).addTo(group);
        }
      } catch { /* outlines are context, not critical */ }
    };
    map.on('moveend', refresh);
    refresh();
    return () => { map.off('moveend', refresh); group.remove(); };
  }, [map]);

  // Selected vehicle's recent trail.
  useEffect(() => {
    if (!map || !trail.data?.points?.length) return;
    const line = L.polyline(trail.data.points.map((p: number[]) => [p[1], p[0]]), {
      color: PALETTE.amber, weight: 2.5, opacity: 0.9,
    }).addTo(map);
    return () => { line.remove(); };
  }, [map, trail.data]);

  const s = stats.data;
  const sel = vehicles.data?.vehicles?.find((v: any) => v.asset === selected);
  const pick = (v: any) => { setSelected(v.asset); map?.setView([v.lat, v.lon], Math.max(map.getZoom(), 13)); };
  const idle = s && !s.detector_running;

  return (
    <div className="animate-fade-in">
      <PageHeader title="Live map"
        subtitle="Where every vehicle is now, and the confirmed crossings and breaches as they happen. Positions and events come from the live detector; analytics pages read the reconciled runs."
        badges={s && (s.detector_running
          ? <Badge variant="success"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse-dot" /> live · feed {fmtDateTime(s.feed_time)}</Badge>
          : <Badge variant="warning">detector idle{s.feed_time ? ` · last fix ${fmtDateTime(s.feed_time)}` : ''}</Badge>)} />

      {idle && (
        <div className="mb-4">
          <Note tone="warn" title="The live detector is not running.">
            Start it with <code>python -m geofencing.cli live --mode tail</code> for a real feed, or <code>--mode replay</code> to
            replay recorded GPS. The map shows the last positions it wrote.
          </Note>
        </div>
      )}

      <KPIGrid className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        <KPI label="Vehicles" value={fmtInt(s?.vehicles)} icon={Truck} color="blue"
          details={() => <LiveTable rows={vehicles.data?.vehicles ?? []} onPick={pick} />} />
        <KPI label="At a facility" value={fmtInt(s?.at_site)} icon={Factory} color="green"
          details={() => <LiveTable rows={(vehicles.data?.vehicles ?? []).filter((v: any) => v.at_site)} onPick={pick} />} />
        <KPI label="Moving" value={fmtInt(s?.moving)} icon={Activity} color="cyan"
          details={() => <LiveTable rows={(vehicles.data?.vehicles ?? []).filter((v: any) => v.moving)} onPick={pick} />} />
        <KPI label="Warnings" value={fmtInt(s?.events_by_severity?.warn)} icon={Gauge} color="amber"
          details={() => <EventTable rows={events.filter(e => e.s_severity === 'warn')} />} />
        <KPI label="Alerts" value={fmtInt(s?.events_by_severity?.alert)} icon={AlertTriangle} color="red"
          details={() => <EventTable rows={events.filter(e => e.s_severity === 'alert')} />} />
      </KPIGrid>

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
        <Card className="xl:col-span-3" pad={false}>
          <div className="p-3 flex items-center justify-between gap-3 flex-wrap">
            <SearchInput value={q} onChange={setQ} placeholder="Find vehicle or site" />
            <div className="flex items-center gap-3 text-xs text-gray-400">
              <Legend color={PALETTE.green} label="at facility" />
              <Legend color={PALETTE.blue} label="moving" />
              <Legend color={PALETTE.stopped} label="stopped" />
              <Legend color={PALETTE.red} label="restricted zone" />
              <span className="text-gray-600">fence outlines from zoom {RING_ZOOM}</span>
            </div>
          </div>
          <GeoMap height="calc(100vh - 330px)" onReady={setMap} className="rounded-t-none border-x-0 border-b-0" />
        </Card>

        <div className="space-y-6">
          {sel && (
            <Card title={sel.asset} icon={Truck}
              actions={<button onClick={() => setSelected(null)} className="text-xs text-gray-400 hover:text-gray-200">close</button>}>
              <div className="space-y-1.5 text-sm">
                <p className="text-gray-300">{sel.speed ?? 0} km/h · {fmtDateTime(sel.ts)}</p>
                <p className="text-gray-400 text-xs">{sel.site_name ? `Inside ${sel.site_name}${sel.inside > 1 ? ` (+${sel.inside - 1} outer zones)` : ''}` : 'Not inside a facility'}</p>
                <div className="flex gap-3 pt-2 text-xs">
                  <Link to={`/geo/vehicles/${encodeURIComponent(sel.asset)}`} className="text-blue-400 hover:text-blue-300">Vehicle page</Link>
                  {sel.trip && <Link to={`/geo/trips/${sel.trip}`} className="text-blue-400 hover:text-blue-300">Trip {sel.trip}</Link>}
                </div>
              </div>
            </Card>
          )}
          <Card title="Event stream" icon={Radio}>
            <div className="space-y-1.5 max-h-[calc(100vh-360px)] overflow-y-auto pr-1">
              {events.length === 0 && <p className="text-sm text-gray-500 py-6 text-center">No events yet.</p>}
              {events.map(e => (
                <button key={e.id} onClick={() => { setSelected(e.s_asset_id); map?.setView([e.d_lat, e.d_long], Math.max(map.getZoom(), 13)); }}
                  className="w-full text-left rounded-md border-l-2 bg-gray-800/40 hover:bg-gray-800 px-2.5 py-1.5"
                  style={{ borderColor: e.s_severity === 'alert' ? PALETTE.red : e.s_severity === 'warn' ? PALETTE.amber : PALETTE.blue }}>
                  <div className="flex justify-between text-xs text-gray-500">
                    <span className="text-gray-300 font-medium">{e.s_asset_id}</span>
                    <span>{fmtTime(e.dt_event)}</span>
                  </div>
                  <p className="text-xs text-gray-300 truncate">
                    {e.s_event === 'enter' ? 'entered' : e.s_event === 'exit' ? 'left' : e.s_event} {e.s_site_name}
                    {e.i_gap_seconds > 900 && <span className="text-amber-400"> ±{fmtDuration(e.i_gap_seconds)}</span>}
                  </p>
                </button>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: color }} />{label}</span>;
}

/** Vehicles on the live map, as a list: click one to find it. */
function LiveTable({ rows, onPick }: { rows: any[]; onPick: (v: any) => void }) {
  const [n, setN] = useState(15);
  const sorted = [...rows].sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  return (
    <>
      <DataTable dense rows={sorted.slice(0, n)} rowKey={r => r.asset} onRowClick={onPick} empty="None right now"
        columns={[
          { key: 'asset', label: 'Vehicle', render: r => <span className="text-gray-100">{r.asset}</span> },
          { key: 'speed', label: 'km/h', align: 'right', render: r => r.speed ?? 0 },
          { key: 'site_name', label: 'Where', render: r => <span className="text-xs text-gray-400">{r.site_name || 'not inside a facility'}</span> },
          { key: 'ts', label: 'Last fix', render: r => <span className="text-xs">{fmtDateTime(r.ts)}</span> },
        ]} />
      {rows.length > n && <button className="text-xs text-blue-400 mt-2" onClick={() => setN(x => x + 30)}>show more ({rows.length - n} left)</button>}
    </>
  );
}

function EventTable({ rows }: { rows: any[] }) {
  return (
    <div className="max-h-[320px] overflow-y-auto">
      <DataTable dense rows={rows} rowKey={r => r.id} empty="None since this page opened"
        columns={[
          { key: 'dt_event', label: 'When', render: r => <span className="text-xs">{fmtDateTime(r.dt_event)}</span> },
          { key: 's_asset_id', label: 'Vehicle', render: r => <span className="text-gray-100 text-xs">{r.s_asset_id}</span> },
          { key: 's_event', label: 'What', render: r => <span className="text-xs">{r.s_event} {r.s_site_name}</span> },
          { key: 's_detail', label: 'Detail', render: r => <span className="text-xs text-gray-400">{r.s_detail || ''}</span> },
        ]} />
    </div>
  );
}
