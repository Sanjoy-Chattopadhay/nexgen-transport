/**
 * The spatial index, drawn over India.
 *
 * Four layers, each answering a question the prose can only assert:
 *
 *   fences       where the 4,500 geofences actually are — and that they are
 *                heavily clustered, which is the fact the index design turns on
 *   node boxes   what one level of the R-tree looks like. Tight, compact boxes
 *                mean the Hilbert sort put neighbours in the same node; that is
 *                what a query gets to reject in four comparisons
 *   Hilbert      the curve's path through the fence centres, in the order the
 *                tree stores them. It should read as one thread that visits
 *                each cluster once and leaves
 *   query        this trip's chunk boxes, and which fences they selected
 *
 * Level and layers are switchable because the point is to let somebody look,
 * not to show them one picture and be believed.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import { Boxes, Spline, Waypoints } from 'lucide-react';
import { api } from '../../lib/api';
import { useApi } from '../../hooks/useApi';
import GeoMap from '../map/GeoMap';
import { PALETTE } from '../../lib/theme';
import { ErrorBox, Spinner, Toggle } from '../ui';
import { fmtInt } from '../../lib/format';

export default function IndexMap({ uploadId }: { uploadId: number | string }) {
  const [level, setLevel] = useState<number | undefined>(undefined);
  const { data, loading, error } = useApi(() => api.uploadIndexMap(uploadId, level), [uploadId, level]);
  const [map, setMap] = useState<L.Map | null>(null);
  const [layers, setLayers] = useState({ nodes: true, curve: false, fences: true, query: true });
  const set = (k: keyof typeof layers) => (v: boolean) => setLayers(s => ({ ...s, [k]: v }));
  const drawn = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!map || !data) return;
    const group = L.layerGroup().addTo(map);
    drawn.current = group;

    if (layers.nodes) {
      for (const b of data.node_boxes) {
        L.rectangle([[b[1], b[0]], [b[3], b[2]]], {
          color: PALETTE.purple, weight: 0.7, fillOpacity: 0.04, fillColor: PALETTE.purple,
          interactive: false,
        }).addTo(group);
      }
    }
    if (layers.curve && data.curve?.length) {
      // Drawn in two passes. The curve is continuous in the lattice, but it is
      // only drawn through fences that exist, so where it crosses empty ground
      // the chord can span a state. Those crossings are true and there are few
      // of them — one per cluster — but at full weight they dominate the
      // picture and make a locality-preserving order look like a scribble.
      // So the local runs are drawn solid and the crossings faint.
      const LOCAL_DEG = 0.6;                         // ~65 km
      let run: [number, number][] = [];
      const flush = () => {
        if (run.length > 1) {
          L.polyline(run, { color: PALETTE.amber, weight: 1.2, opacity: 0.85, interactive: false })
            .addTo(group);
        }
        run = [];
      };
      let prev: number[] | null = null;
      for (const p of data.curve) {
        if (prev && (Math.abs(p[0] - prev[0]) > LOCAL_DEG || Math.abs(p[1] - prev[1]) > LOCAL_DEG)) {
          flush();
          L.polyline([[prev[1], prev[0]], [p[1], p[0]]], {
            color: PALETTE.amber, weight: 0.6, opacity: 0.18, dashArray: '3 5', interactive: false,
          }).addTo(group);
        }
        run.push([p[1], p[0]]);
        prev = p;
      }
      flush();
    }
    if (layers.fences) {
      // Selected and hit fences drawn last, so they sit on top of the mass.
      const order = [...data.fences].sort((a: any[], b: any[]) => a[3] - b[3]);
      for (const f of order) {
        const kind = f[3];
        L.circleMarker([f[1], f[0]], {
          radius: kind === 2 ? 4 : kind === 1 ? 2.5 : 1.5,
          color: kind === 2 ? PALETTE.green : kind === 1 ? PALETTE.cyan : PALETTE.gray,
          weight: kind ? 1 : 0,
          fillColor: kind === 2 ? PALETTE.green : kind === 1 ? PALETTE.cyan : PALETTE.gray,
          fillOpacity: kind ? 0.9 : 0.45,
        }).addTo(group);
      }
    }
    if (layers.query && data.chunk_boxes?.length) {
      for (const b of data.chunk_boxes) {
        L.rectangle([[b[1], b[0]], [b[3], b[2]]], {
          color: PALETTE.blue, weight: 1.6, fillOpacity: 0.06, fillColor: PALETTE.blue,
          dashArray: '5 4', interactive: false,
        }).addTo(group);
      }
    }
    return () => { group.remove(); };
  }, [map, data, layers]);

  const levels = data?.levels ?? 0;
  const legend = useMemo(() => ([
    { c: PALETTE.gray, t: 'a geofence in the master' },
    { c: PALETTE.cyan, t: 'offered as a candidate by the index' },
    { c: PALETTE.green, t: 'the truck was actually inside it' },
    { c: PALETTE.purple, t: 'an R-tree node box at this level' },
    { c: PALETTE.blue, t: "this trip's query boxes, one per chunk of trail" },
    { c: PALETTE.amber, t: 'the Hilbert curve — solid within a cluster, faint where it crosses empty ground' },
  ]), []);

  if (error) return <ErrorBox error={error} />;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-500">Tree level</span>
          <div className="flex gap-0.5 bg-gray-900 border border-gray-800 rounded-md p-0.5">
            {Array.from({ length: levels }, (_, i) => (
              <button key={i} onClick={() => setLevel(i)}
                className={`px-2 py-0.5 rounded text-xs font-medium ${
                  data?.level === i ? 'bg-blue-600/15 text-blue-400' : 'text-gray-500 hover:text-gray-300'}`}>
                {i === 0 ? 'leaves' : i === levels - 1 ? 'root' : i}
              </button>
            ))}
          </div>
          {data && <span className="text-xs text-gray-500">
            {data.level_label} · {fmtInt(data.node_boxes.length)} boxes
          </span>}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <Toggle checked={layers.fences} onChange={set('fences')} label="Fences" />
          <Toggle checked={layers.nodes} onChange={set('nodes')} label="Node boxes" />
          <Toggle checked={layers.curve} onChange={set('curve')} label="Hilbert curve" />
          <Toggle checked={layers.query} onChange={set('query')} label="This trip's query" />
        </div>
      </div>

      {loading && !data ? <Spinner label="Building the index" /> : <GeoMap height={520} onReady={setMap} />}

      <div className="flex flex-wrap gap-x-5 gap-y-1.5">
        {legend.map(l => (
          <span key={l.t} className="flex items-center gap-1.5 text-xs text-gray-500">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: l.c }} />{l.t}
          </span>
        ))}
      </div>

      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {[
            { icon: Boxes, label: 'Fences in the master', value: fmtInt(data.counts.master) },
            { icon: Waypoints, label: 'Offered as candidates', value: fmtInt(data.counts.candidates) },
            { icon: Spline, label: 'Actually entered', value: fmtInt(data.counts.hit) },
            { icon: Boxes, label: `Node boxes at level ${data.level}`, value: fmtInt(data.counts.nodes_at_level) },
          ].map(s => (
            <div key={s.label} className="bg-gray-800/50 rounded-lg px-3 py-2">
              <p className="text-xs text-gray-500 flex items-center gap-1"><s.icon className="w-3 h-3" />{s.label}</p>
              <p className="text-sm text-gray-100 font-medium tabular mt-0.5">{s.value}</p>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-gray-500 leading-relaxed">
        Switch to <span className="text-gray-300">leaves</span> and the boxes are the fences themselves; move up a
        level and each box covers sixteen of them. The thing to look for is that a parent box stays
        <em className="text-gray-300"> small</em> — that is the Hilbert sort having put neighbours together, and it is
        exactly what lets a query reject a whole subtree on four float comparisons. Turn the curve on to see the order
        the tree stores them in: one thread that enters each cluster, covers it, and leaves.
      </p>
    </div>
  );
}
