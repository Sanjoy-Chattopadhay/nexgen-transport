/**
 * The whole feed as a skyline (three.js): one bar for every hour of every
 * day, as tall as the arrivals at facility fences in that hour.
 *
 * Shift changes, the Sunday lull, the day a plant stopped loading, the
 * overnight queue -- patterns a table of 37 × 24 numbers hides -- stand up as
 * ridges and valleys. Hover a bar for its numbers; click it to open that day.
 * Hours with alerts carry a red cap.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { PALETTE } from '../../lib/theme';
import { color, dispose, label, webglAvailable } from './common';
import { fmtDay, fmtInt } from '../../lib/format';

type Metric = 'entries' | 'exits' | 'alerts';

export default function FleetSkyline({ days, selected, onPick, height = 440 }: {
  days: { d_day: string; entries: number[]; exits: number[]; alerts: number[] }[];
  selected?: string;
  onPick?: (day: string) => void;
  height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [metric, setMetric] = useState<Metric>('entries');
  const [hover, setHover] = useState<{ x: number; y: number; day: string; hour: number } | null>(null);
  const [ok] = useState(webglAvailable);
  const pick = useRef(onPick);
  pick.current = onPick;

  const max = useMemo(() => Math.max(1, ...days.flatMap(d => d[metric] || [])), [days, metric]);

  useEffect(() => {
    if (!ok || !host.current || !days.length) return;
    const el = host.current;
    const w = el.clientWidth || 800;
    const n = days.length;
    const cell = 2.2;
    const depth = n * cell;
    const width = 24 * cell;
    const H = 26;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, w / height, 0.5, 3000);
    const dist = Math.max(width, depth) * 1.1;
    camera.position.set(width * 0.75, dist * 0.72, depth * 0.5 + dist * 0.6);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(w, height);
    el.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 2, 2);
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.35;
    controls.addEventListener('start', () => { controls.autoRotate = false; });

    scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(-40, 80, 60);
    scene.add(sun);
    const root = new THREE.Group();
    scene.add(root);

    const base = new THREE.Mesh(new THREE.PlaneGeometry(width + 4, depth + 4),
      new THREE.MeshBasicMaterial({ color: color(PALETTE.heatEmpty), transparent: true, opacity: 0.9 }));
    base.rotation.x = -Math.PI / 2;
    root.add(base);

    const low = color(PALETTE.grid);
    const mid = color(metric === 'alerts' ? PALETTE.amber : PALETTE.blue);
    const high = color(metric === 'alerts' ? PALETTE.red : PALETTE.cyan);
    const geo = new THREE.BoxGeometry(cell * 0.82, 1, cell * 0.82);
    geo.translate(0, 0.5, 0);
    const mat = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.1 });
    const bars = new THREE.InstancedMesh(geo, mat, n * 24);
    const m4 = new THREE.Matrix4();
    const c = new THREE.Color();
    const cells: { day: string; hour: number; v: number }[] = [];
    days.forEach((d, i) => {
      const vals = d[metric] || [];
      for (let h = 0; h < 24; h++) {
        const v = vals[h] || 0;
        const k = i * 24 + h;
        const hh = v ? 0.3 + (H * v) / max : 0.08;
        m4.makeScale(1, hh, 1);
        m4.setPosition(h * cell - width / 2 + cell / 2, 0, i * cell - depth / 2 + cell / 2);
        bars.setMatrixAt(k, m4);
        const f = v / max;
        c.copy(low).lerp(mid, Math.min(1, f * 2)).lerp(high, Math.max(0, f * 2 - 1));
        if (selected && d.d_day === selected) c.lerp(new THREE.Color(0xffffff), 0.25);
        bars.setColorAt(k, c);
        cells.push({ day: d.d_day, hour: h, v });
      }
    });
    bars.instanceMatrix.needsUpdate = true;
    if (bars.instanceColor) bars.instanceColor.needsUpdate = true;
    root.add(bars);

    // red caps on the hours with the most alerts -- the top quarter, and at
    // least two -- as thick as their count, so a cap means "look here"
    if (metric !== 'alerts') {
      const capGeo = new THREE.BoxGeometry(cell * 0.86, 1, cell * 0.86);
      const all = days.flatMap((d, i) => (d.alerts || []).map((a, h) => ({ i, h, a, v: (d[metric] || [])[h] || 0 })))
        .filter(x => x.a > 0);
      const counts = all.map(x => x.a).sort((a, b) => a - b);
      const floor = Math.max(2, counts[Math.floor(counts.length * 0.75)] ?? 2);
      const maxA = Math.max(1, ...counts);
      const alertCells = all.filter(x => x.a >= floor);
      if (alertCells.length) {
        const caps = new THREE.InstancedMesh(capGeo, new THREE.MeshStandardMaterial({ color: color(PALETTE.red), emissive: color(PALETTE.red), emissiveIntensity: 0.35 }), alertCells.length);
        alertCells.forEach((x, k) => {
          const hh = x.v ? 0.3 + (H * x.v) / max : 0.08;
          const t = 0.25 + 1.6 * (x.a / maxA);
          m4.makeScale(1, t, 1);
          m4.setPosition(x.h * cell - width / 2 + cell / 2, hh + t / 2 + 0.05, x.i * cell - depth / 2 + cell / 2);
          caps.setMatrixAt(k, m4);
        });
        caps.instanceMatrix.needsUpdate = true;
        root.add(caps);
      }
    }

    // axes
    for (const h of [0, 6, 12, 18, 23]) {
      const l = label(`${String(h).padStart(2, '0')}:00`, PALETTE.legend, 2.6);
      l.position.set(h * cell - width / 2 + cell / 2 - 1.2, 0.2, depth / 2 + 2.5);
      root.add(l);
    }
    const every = Math.max(1, Math.ceil(n / 8));
    days.forEach((d, i) => {
      if (i % every !== 0 && i !== n - 1) return;
      const l = label(fmtDay(d.d_day), d.d_day === selected ? PALETTE.blue : PALETTE.legend, 2.6, d.d_day === selected ? 700 : 500);
      l.position.set(width / 2 + 1.5, 0.2, i * cell - depth / 2 + cell / 2);
      root.add(l);
    });

    // hover and click
    const ray = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let hovered = -1;
    const baseColor = new THREE.Color();
    const onMove = (e: PointerEvent) => {
      const r = renderer.domElement.getBoundingClientRect();
      mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(mouse, camera);
      const hit = ray.intersectObject(bars)[0];
      const id = hit?.instanceId ?? -1;
      if (id !== hovered) {
        if (hovered >= 0) { bars.setColorAt(hovered, baseColor); bars.instanceColor!.needsUpdate = true; }
        hovered = id;
        if (id >= 0) {
          bars.getColorAt(id, baseColor);
          bars.setColorAt(id, new THREE.Color(PALETTE.tooltipText));
          bars.instanceColor!.needsUpdate = true;
        }
      }
      if (id >= 0) setHover({ x: e.clientX - r.left, y: e.clientY - r.top, day: cells[id].day, hour: cells[id].hour });
      else setHover(null);
      renderer.domElement.style.cursor = id >= 0 ? 'pointer' : 'grab';
    };
    let downAt = 0;
    const onDown = () => { downAt = performance.now(); };
    const onUp = () => {
      if (performance.now() - downAt < 250 && hovered >= 0) pick.current?.(cells[hovered].day);
    };
    const onLeave = () => setHover(null);
    renderer.domElement.addEventListener('pointermove', onMove);
    renderer.domElement.addEventListener('pointerdown', onDown);
    renderer.domElement.addEventListener('pointerup', onUp);
    renderer.domElement.addEventListener('pointerleave', onLeave);

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
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener('pointermove', onMove);
      renderer.domElement.removeEventListener('pointerdown', onDown);
      renderer.domElement.removeEventListener('pointerup', onUp);
      renderer.domElement.removeEventListener('pointerleave', onLeave);
      controls.dispose();
      dispose(scene);
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [ok, days, metric, max, selected, height]);

  if (!ok) return <p className="text-sm text-gray-500 py-10 text-center">This browser cannot draw 3D (WebGL is off).</p>;
  const hd = hover ? days.find(d => d.d_day === hover.day) : null;
  return (
    <div className="relative">
      <div ref={host} style={{ height }} className="w-full" />
      <div className="absolute top-1 left-1 flex gap-1 bg-gray-900/80 border border-gray-800 rounded-lg p-0.5">
        {([['entries', 'Arrivals'], ['exits', 'Departures'], ['alerts', 'Alerts']] as [Metric, string][]).map(([k, l]) => (
          <button key={k} onClick={() => setMetric(k)}
            className={`px-2 py-1 rounded-md text-xs font-medium ${metric === k ? 'bg-blue-600/15 text-blue-400' : 'text-gray-400 hover:text-gray-200'}`}>
            {l}
          </button>
        ))}
      </div>
      <p className="absolute top-2 right-2 text-xs text-gray-500 pointer-events-none">across: hour of day · deep: day · red caps: the hours with the most alerts · click a bar to open its day</p>
      {hover && hd && (
        <div className="absolute z-10 pointer-events-none rounded-lg border px-3 py-2 text-xs shadow-lg"
          style={{ left: Math.max(0, hover.x + 14), top: Math.max(0, hover.y - 10), background: PALETTE.tooltipBg,
            borderColor: PALETTE.tooltipBorder, color: PALETTE.tooltipText }}>
          <p className="font-medium">{fmtDay(hover.day)} · {String(hover.hour).padStart(2, '0')}:00</p>
          <p className="text-gray-400">{fmtInt(hd.entries[hover.hour])} arrivals · {fmtInt(hd.exits[hover.hour])} departures
            {hd.alerts[hover.hour] ? <span className="text-red-400"> · {hd.alerts[hover.hour]} alerts</span> : null}</p>
        </div>
      )}
    </div>
  );
}
