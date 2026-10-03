/**
 * The trip in space and time, in 3D (three.js).
 *
 * The map is the floor; time rises. A truck driving draws a line climbing
 * across the floor; a truck standing still draws a vertical column, as tall
 * as it stood -- so loading and unloading are the tall pillars at each end,
 * an overnight halt is a pillar in the middle of nowhere, and a stretch the
 * GPS was silent is a gap in the line. (Hägerstrand's space-time cube: the
 * one picture where waiting is as visible as moving.)
 *
 * The playhead is shared with the number line: the glowing marker is the
 * truck at that moment, and the translucent plane is that moment's slice.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Line2 } from 'three/examples/jsm/lines/Line2.js';
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js';
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js';
import { Pause, Play, RotateCcw } from 'lucide-react';
import { PALETTE } from '../../lib/theme';
import { segmentColor, PHASE_LABEL, KIND_TEXT } from '../trip/phaseColors';
import type { Segment } from '../trip/NumberLine';
import { color, dispose, label, webglAvailable } from './common';
import { fmtDateTime, fmtDuration } from '../../lib/format';

const SIZE = 100;       // floor extent, world units
const HEIGHT = 70;      // time extent

const ms = (s: string) => new Date(s.replace(' ', 'T')).getTime();

export default function SpaceTimeCube({ points, segments, cursor, onCursor, height = 460 }: {
  /** Track points: [ts, lat, lon, speed, fit_lat, fit_lon, role, …] as /trips/{no}/track returns them. */
  points: any[];
  segments: Segment[];
  cursor: number | null;
  onCursor: (t: number) => void;
  height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<{ setCursor: (t: number | null) => void; reset: () => void } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [ok] = useState(webglAvailable);

  const segs = useMemo(() => segments.map(s => ({ ...s, t0: ms(s.start), t1: ms(s.end) })), [segments]);
  const track = useMemo(() => {
    const out: { t: number; lat: number; lon: number }[] = [];
    for (const p of points) {
      if (p[6] === 'reject') continue;
      const lat = p[4] ?? p[1];
      const lon = p[5] ?? p[2];
      if (lat == null || lon == null) continue;
      out.push({ t: ms(p[0]), lat, lon });
    }
    return out;
  }, [points]);
  const T0 = segs.length ? segs[0].t0 : track[0]?.t ?? 0;
  const T1 = segs.length ? segs[segs.length - 1].t1 : track[track.length - 1]?.t ?? 1;

  useEffect(() => {
    if (!ok || !host.current || track.length < 2) return;
    const el = host.current;
    const w = el.clientWidth || 800;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, w / height, 0.5, 2000);
    camera.position.set(115, 95, 125);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(w, height);
    el.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, HEIGHT * 0.42, 0);
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.6;
    controls.addEventListener('start', () => { controls.autoRotate = false; });

    // --- projection: the trip's bounding box onto the floor, time upward
    const lats = track.map(p => p.lat), lons = track.map(p => p.lon);
    const latMid = (Math.min(...lats) + Math.max(...lats)) / 2;
    const lonMid = (Math.min(...lons) + Math.max(...lons)) / 2;
    const kx = Math.cos((latMid * Math.PI) / 180);
    const ext = Math.max((Math.max(...lons) - Math.min(...lons)) * kx, Math.max(...lats) - Math.min(...lats), 1e-4);
    const px = (lon: number) => ((lon - lonMid) * kx / ext) * SIZE;
    const pz = (lat: number) => (-(lat - latMid) / ext) * SIZE;
    const py = (t: number) => ((t - T0) / Math.max(1, T1 - T0)) * HEIGHT;
    const segAt = (t: number) => segs.find(s => t >= s.t0 && t <= s.t1);

    const root = new THREE.Group();
    scene.add(root);
    scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    const sun = new THREE.DirectionalLight(0xffffff, 0.6);
    sun.position.set(60, 120, 40);
    scene.add(sun);

    // floor and walls of the cube
    const grid = new THREE.GridHelper(SIZE * 1.2, 12, color(PALETTE.grid), color(PALETTE.grid));
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.7;
    root.add(grid);
    const box = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(SIZE * 1.2, HEIGHT, SIZE * 1.2)),
      new THREE.LineBasicMaterial({ color: color(PALETTE.grid), transparent: true, opacity: 0.55 }));
    box.position.y = HEIGHT / 2;
    root.add(box);

    // time ticks up one edge
    const span = T1 - T0;
    const steps = [1, 2, 3, 6, 12, 24, 48, 96].map(h => h * 3600_000);
    const step = steps.find(s => span / s <= 8) ?? steps[steps.length - 1];
    for (let t = Math.ceil(T0 / step) * step; t <= T1; t += step) {
      const y = py(t);
      const tick = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-SIZE * 0.6, y, SIZE * 0.6), new THREE.Vector3(-SIZE * 0.6 + 3, y, SIZE * 0.6)]),
        new THREE.LineBasicMaterial({ color: color(PALETTE.axis) }));
      root.add(tick);
      const d = new Date(t);
      const txt = step >= 24 * 3600_000 || d.getHours() === 0
        ? d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
        : d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false });
      const l = label(txt, PALETTE.legend, 3.4);
      l.position.set(-SIZE * 0.6 + 4, y, SIZE * 0.6);
      root.add(l);
    }

    // the path, coloured by what the truck was doing
    const positions: number[] = [];
    const colors: number[] = [];
    const floor: THREE.Vector3[] = [];
    let prev: { t: number } | null = null;
    const lines: Line2[] = [];
    const flush = () => {
      if (positions.length >= 6) {
        const g = new LineGeometry();
        g.setPositions(positions);
        g.setColors(colors);
        const m = new LineMaterial({ vertexColors: true, linewidth: 3, worldUnits: false });
        m.resolution.set(w, height);
        const line = new Line2(g, m);
        line.computeLineDistances();
        lines.push(line);
        root.add(line);
      }
      positions.length = 0;
      colors.length = 0;
    };
    for (const p of track) {
      const s = segAt(p.t);
      // a silence breaks the line: nothing was seen there
      if (prev && s?.kind === 'silent') { flush(); prev = null; continue; }
      const c = color(s ? segmentColor(s.phase, s.kind) : PALETTE.blue);
      if (s && (s.phase === 'before' || s.phase === 'after')) c.multiplyScalar(0.55);
      positions.push(px(p.lon), py(p.t), pz(p.lat));
      colors.push(c.r, c.g, c.b);
      floor.push(new THREE.Vector3(px(p.lon), 0.05, pz(p.lat)));
      prev = p;
    }
    flush();
    const shadow = new THREE.Line(new THREE.BufferGeometry().setFromPoints(floor),
      new THREE.LineBasicMaterial({ color: color(PALETTE.rawTrack), transparent: true, opacity: 0.45 }));
    root.add(shadow);

    // pillars and names at the places
    for (const s of segs) {
      if (s.kind !== 'stay' && !(s.kind === 'halt' && s.duration_s > 3600) && !(s.kind === 'stop' && s.duration_s > 4 * 3600)) continue;
      const mid = track.find(p => p.t >= s.t0) ?? track[0];
      const h = Math.max(0.6, py(s.t1) - py(s.t0));
      const pillar = new THREE.Mesh(new THREE.CylinderGeometry(1.4, 1.4, h, 20, 1, true),
        new THREE.MeshStandardMaterial({ color: color(segmentColor(s.phase, s.kind)), transparent: true, opacity: 0.22,
          side: THREE.DoubleSide, depthWrite: false }));
      pillar.position.set(px(mid.lon), py(s.t0) + h / 2, pz(mid.lat));
      root.add(pillar);
      const base = new THREE.Mesh(new THREE.RingGeometry(1.6, 2.4, 28),
        new THREE.MeshBasicMaterial({ color: color(segmentColor(s.phase, s.kind)), side: THREE.DoubleSide, transparent: true, opacity: 0.7 }));
      base.rotation.x = -Math.PI / 2;
      base.position.set(px(mid.lon), 0.08, pz(mid.lat));
      root.add(base);
      if (s.kind === 'stay' || s.duration_s > 6 * 3600) {
        const name = s.kind === 'stay' ? `${PHASE_LABEL[s.phase]}${s.name ? ` · ${s.name}` : ''}` : KIND_TEXT[s.kind];
        const l = label(`${name} · ${fmtDuration(s.duration_s)}`, segmentColor(s.phase, s.kind), 3.8, 600);
        l.position.set(px(mid.lon) + 2.5, py(s.t0) + h / 2, pz(mid.lat));
        root.add(l);
      }
    }

    // the playhead: the truck, and the moment's slice
    const truck = new THREE.Mesh(new THREE.SphereGeometry(1.6, 24, 16),
      new THREE.MeshStandardMaterial({ color: color(PALETTE.tooltipText), emissive: color(PALETTE.blue), emissiveIntensity: 0.9 }));
    const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color: color(PALETTE.tooltipText), dashSize: 1, gapSize: 1, transparent: true, opacity: 0.6 }));
    const slice = new THREE.Mesh(new THREE.PlaneGeometry(SIZE * 1.2, SIZE * 1.2),
      new THREE.MeshBasicMaterial({ color: color(PALETTE.blue), transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false }));
    slice.rotation.x = -Math.PI / 2;
    truck.visible = drop.visible = slice.visible = false;
    root.add(truck, drop, slice);
    const at = (t: number) => {
      let i = track.findIndex(p => p.t >= t);
      if (i < 0) i = track.length - 1;
      const a = track[Math.max(0, i - 1)], b = track[i];
      const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0;
      return { lat: a.lat + f * (b.lat - a.lat), lon: a.lon + f * (b.lon - a.lon) };
    };
    const setCursor = (t: number | null) => {
      const show = t != null && t >= T0 && t <= T1;
      truck.visible = drop.visible = slice.visible = show;
      if (!show) return;
      const p = at(t!);
      const x = px(p.lon), z = pz(p.lat), y = py(t!);
      truck.position.set(x, y, z);
      slice.position.y = y;
      (drop.geometry as THREE.BufferGeometry).setFromPoints([new THREE.Vector3(x, y, z), new THREE.Vector3(x, 0, z)]);
      drop.computeLineDistances();
    };
    api.current = {
      setCursor,
      reset: () => { camera.position.set(115, 95, 125); controls.target.set(0, HEIGHT * 0.42, 0); controls.autoRotate = true; },
    };

    let raf = 0;
    const render = () => {
      raf = requestAnimationFrame(render);
      controls.update();
      renderer.render(scene, camera);
    };
    render();
    const ro = new ResizeObserver(() => {
      const nw = el.clientWidth || w;
      camera.aspect = nw / height;
      camera.updateProjectionMatrix();
      renderer.setSize(nw, height);
      for (const l of lines) (l.material as LineMaterial).resolution.set(nw, height);
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      dispose(scene);
      renderer.dispose();
      renderer.domElement.remove();
      api.current = null;
    };
  }, [ok, track, segs, T0, T1, height]);

  useEffect(() => { api.current?.setCursor(cursor); }, [cursor]);

  // play: the whole trip in twenty seconds
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let t = cursor != null && cursor < T1 ? cursor : T0;
    const rate = (T1 - T0) / 20_000;
    const tick = (now: number) => {
      t += (now - last) * rate;
      last = now;
      if (t >= T1) { onCursor(T1); setPlaying(false); return; }
      onCursor(t);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  if (!ok) return <p className="text-sm text-gray-500 py-10 text-center">This browser cannot draw 3D (WebGL is off).</p>;
  const seg = cursor != null ? segs.find(s => cursor >= s.t0 && cursor <= s.t1) : null;
  return (
    <div className="relative">
      <div ref={host} style={{ height }} className="w-full rounded-lg overflow-hidden cursor-grab active:cursor-grabbing" />
      <div className="absolute top-2 left-2 flex items-center gap-1.5">
        <button onClick={() => setPlaying(p => !p)} title={playing ? 'Pause' : 'Play the trip'}
          className="p-1.5 rounded-md bg-gray-900/80 border border-gray-800 text-gray-300 hover:text-white">
          {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
        </button>
        <button onClick={() => api.current?.reset()} title="Reset the view"
          className="p-1.5 rounded-md bg-gray-900/80 border border-gray-800 text-gray-300 hover:text-white">
          <RotateCcw className="w-4 h-4" />
        </button>
      </div>
      <div className="absolute bottom-2 left-2 right-2 flex items-center gap-3">
        <input type="range" min={T0} max={T1} step={Math.max(1, (T1 - T0) / 2000)} value={cursor ?? T0}
          onChange={e => onCursor(Number(e.target.value))} className="flex-1 accent-blue-600" />
        <span className="text-xs text-gray-300 bg-gray-900/80 border border-gray-800 rounded-md px-2 py-1 whitespace-nowrap">
          {cursor != null ? fmtDateTime(new Date(cursor).toISOString()) : 'drag to move through the trip'}
          {seg && <> · {PHASE_LABEL[seg.phase]} · {KIND_TEXT[seg.kind]}</>}
        </span>
      </div>
      <p className="absolute top-2 right-3 text-xs text-gray-500 pointer-events-none">floor: the route · up: time · drag to turn</p>
    </div>
  );
}
