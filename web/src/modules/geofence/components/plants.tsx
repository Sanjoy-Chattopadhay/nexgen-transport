/**
 * Plants & congestion: the pieces the plant list and a plant's page share.
 *
 * A scene is one overload the scan found (nexgen/shared/geoengine/reporting/
 * congestion.py): a gate, weighbridge, parking, loading point, road or area
 * holding more vehicles than it usually does, for long enough to matter. Its
 * card reads as a sentence and opens to the proof of every number in it --
 * the peak, the vehicles in it, the usual level, how long the trucks that
 * arrived during it stayed, and the usual stay -- each recounted on the server
 * from its records (GET /api/v1/geo/plants/proof/{dataset}).
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { LucideIcon } from 'lucide-react';
import {
  Container, DoorOpen, Factory, FlaskConical, Hourglass, Layers, MapPinned, Route, Scale, SquareParking, Timer,
  TriangleAlert, Truck, Users,
} from 'lucide-react';
import KPICard from '../../analytics/components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import type { ProofSpec } from '../../../core/proof/context';
import { PALETTE } from '../lib/theme';
import { Badge } from './ui';

export const PROOF_ENDPOINT = '/geo/plants/proof';

/** A proof of a plant figure: the server recounts `value` from the records. */
export function plantProof(dataset: string, params: Record<string, string | number | null | undefined>,
  value: number | string | null | undefined): ProofSpec {
  return { dataset, endpoint: PROOF_ENDPOINT, params, value: value ?? null };
}

export const KINDS = ['gate', 'weighbridge', 'parking', 'loading', 'road', 'area', 'plant'] as const;

export const KIND_TEXT: Record<string, string> = {
  gate: 'Gate', weighbridge: 'Weighbridge', parking: 'Parking / yard', loading: 'Loading point',
  road: 'Internal road', area: 'Area', plant: 'Elsewhere in the plant',
};

export const KIND_PLURAL: Record<string, string> = {
  gate: 'Gates', weighbridge: 'Weighbridges', parking: 'Parking & yards', loading: 'Loading points',
  road: 'Internal roads', area: 'Other areas', plant: 'Elsewhere in the plant',
};

export const KIND_NOUN: Record<string, string> = {
  gate: 'Gate overloading', weighbridge: 'Weighbridge queue', parking: 'Parking full',
  loading: 'Loading point crowding', road: 'Internal road congestion', area: 'Crowding', plant: 'Plant crowding',
};

export const KIND_ICON: Record<string, LucideIcon> = {
  gate: DoorOpen, weighbridge: Scale, parking: SquareParking, loading: Container, road: Route, area: Layers,
  plant: Factory,
};

/** The live theme's colour for a zone kind: read at render time so a theme switch follows. */
export function kindColor(kind: string): string {
  switch (kind) {
    case 'gate': return PALETTE.red;
    case 'weighbridge': return PALETTE.amber;
    case 'parking': return PALETTE.purple;
    case 'loading': return PALETTE.cyan;
    case 'road': return PALETTE.blue;
    case 'area': return PALETTE.green;
    default: return PALETTE.gray;
  }
}

export function KindBadge({ kind }: { kind: string }) {
  const Icon = KIND_ICON[kind] || Layers;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-gray-300 whitespace-nowrap">
      <Icon className="w-3.5 h-3.5 shrink-0" style={{ color: kindColor(kind) }} />
      {KIND_TEXT[kind] || kind}
    </span>
  );
}

/** Seconds as minutes to one decimal: how the server's proofs state a stay. */
export function minutesOf(seconds: number | null | undefined): number | null {
  return seconds == null ? null : Math.round((seconds / 60) * 10) / 10;
}

/** "84 min", "2 h 05 min" -- the wording of the scene sentences. */
export function fmtMin(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const m = Math.round(seconds / 60);
  return m < 120 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}

/** A server datetime ("2026-10-05 10:57:00") as "Mon 5 Oct 10:57". */
export function fmtWhen(v: string | null | undefined): string {
  if (!v) return '—';
  const d = new Date(v.replace(' ', 'T'));
  if (Number.isNaN(+d)) return v;
  return `${d.toLocaleDateString('en-GB', { weekday: 'short' })} ${d.getDate()} ${d.toLocaleDateString('en-GB', { month: 'short' })} ${hhmm(v)}`;
}

export const hhmm = (v: string | null | undefined) => (v ? v.slice(11, 16) : '—');

/** "2026-10-05" as "Mon 5 Oct". */
export function fmtDayName(day: string): string {
  const d = new Date(`${day}T00:00:00`);
  if (Number.isNaN(+d)) return day;
  return `${d.toLocaleDateString('en-GB', { weekday: 'short' })} ${d.getDate()} ${d.toLocaleDateString('en-GB', { month: 'short' })}`;
}

/** A server datetime as the value of an <input type="datetime-local">. */
export const toLocalInput = (v: string | null | undefined) => (v ? v.slice(0, 16).replace(' ', 'T') : '');

export function SeverityBadge({ severity }: { severity: string }) {
  return severity === 'high'
    ? <Badge variant="danger">high</Badge>
    : <Badge variant="warning">moderate</Badge>;
}

/** The scene's figures, each opening its proof. */
export function SceneProof({ scene }: { scene: any }) {
  const z = scene.zone.site_id;
  const window = { site_id: z, from: scene.start, to: scene.end };
  return (
    <ProofGrid className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
      <KPICard label={`Peak at ${hhmm(scene.peak_at)}`} value={scene.peak} icon={Truck} color="red"
        proof={plantProof('inside', { site_id: z, at: scene.peak_at }, scene.peak)} />
      <KPICard label="Vehicles in the overload" value={scene.vehicles} icon={Users} color="amber"
        proof={plantProof('episode', window, scene.vehicles)} />
      <KPICard label="Usual level" value={scene.usual ?? '—'} icon={Layers} color="blue"
        proof={plantProof('usual', { site_id: z }, scene.usual)} />
      <KPICard label="Stay of those arriving" value={fmtMin(scene.wait_p50_s)} icon={Hourglass} color="purple"
        proof={plantProof('waits', window, minutesOf(scene.wait_p50_s))} />
      <KPICard label="Usual stay" value={fmtMin(scene.usual_stay_p50_s)} icon={Timer} color="cyan"
        proof={plantProof('stays', { site_id: z }, minutesOf(scene.usual_stay_p50_s))} />
    </ProofGrid>
  );
}

/**
 * One overload, as the scan found it. `onFocus` (on a plant's own page) moves
 * the page to the scene's peak; elsewhere the card links there.
 */
export function SceneCard({ scene, showPlant = true, onFocus }: {
  scene: any; showPlant?: boolean; onFocus?: (scene: any) => void;
}) {
  const [open, setOpen] = useState(false);
  const Icon = KIND_ICON[scene.kind] || Layers;
  const others = (scene.members || []).filter((m: any) => m.site_id !== scene.zone.site_id);
  const notes: string[] = [];
  if (scene.baseline_thin) notes.push('the usual level rests on under 6 hours of busy time');
  if (scene.uncertain) notes.push(`${scene.uncertain} of its stays have an entry or exit inside a GPS gap over 10 min`);
  if (scene.open_stays) notes.push(`${scene.open_stays} trails ended inside (their stays are lower bounds)`);
  const peakLink = `/geo/plants/${scene.plant.site_id}?at=${encodeURIComponent(scene.peak_at)}`;
  return (
    <article className="rounded-xl border border-gray-800 bg-gray-900/60 p-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-lg p-2 shrink-0" style={{ background: `${kindColor(scene.kind)}22` }}>
          <Icon className="w-5 h-5" style={{ color: kindColor(scene.kind) }} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-white">{KIND_NOUN[scene.kind] || 'Crowding'}</span>
            <span className="text-sm text-gray-300">at {scene.zone.name}</span>
            {showPlant && scene.plant.name !== scene.zone.name && (
              <Link to={`/geo/plants/${scene.plant.site_id}`} className="text-sm text-blue-400 hover:text-blue-300">
                {scene.plant.name}
              </Link>
            )}
            <SeverityBadge severity={scene.severity} />
          </div>
          <p className="text-xs text-gray-500 mt-0.5 tabular">
            {fmtWhen(scene.start)} → {scene.end.slice(0, 10) === scene.start.slice(0, 10) ? hhmm(scene.end) : fmtWhen(scene.end)}
            {' · '}{fmtMin(scene.minutes * 60)} at {scene.threshold} or more
          </p>
          <p className="text-sm text-gray-300 mt-2 leading-relaxed">{scene.headline}</p>

          <div className="flex flex-wrap gap-x-5 gap-y-1 mt-2 text-xs text-gray-400 tabular">
            <span>Peak <b className="text-gray-100">{scene.peak}</b> at {hhmm(scene.peak_at)}</span>
            <span>Usual <b className="text-gray-100">{scene.usual ?? '—'}</b></span>
            <span>Threshold <b className="text-gray-100">{scene.threshold}</b> <span className="text-gray-600">({scene.threshold_basis})</span></span>
            <span>Vehicles <b className="text-gray-100">{scene.vehicles}</b></span>
            {scene.wait_p50_s != null && (
              <span>Arrivals stayed <b className="text-gray-100">{fmtMin(scene.wait_p50_s)}</b> vs {fmtMin(scene.usual_stay_p50_s)} usual
                <span className="text-gray-600"> ({scene.waits_measured} measured)</span></span>
            )}
            <span>Tracked fleet-wide at the peak <b className="text-gray-100">{scene.tracked_at_peak}</b></span>
          </div>

          {notes.length > 0 && (
            <p className="text-xs text-amber-300/90 mt-2 flex items-start gap-1.5">
              <TriangleAlert className="w-3.5 h-3.5 shrink-0 mt-0.5" /> <span>Confidence: {notes.join('; ')}.</span>
            </p>
          )}
          {others.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 mt-2 text-xs">
              <span className="text-gray-500">Same crowd in:</span>
              {others.map((m: any) => (
                <Link key={`${m.site_id}-${m.start}`} to={`/geo/geofences/${m.site_id}`}
                  className="px-2 py-0.5 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300">
                  {m.name} <span className="text-gray-500">· {m.scale} · peak {m.peak}</span>
                </Link>
              ))}
            </div>
          )}

          <div className="flex items-center gap-4 mt-3 text-sm">
            <button onClick={() => setOpen(o => !o)} className="inline-flex items-center gap-1.5 text-blue-400 hover:text-blue-300">
              <FlaskConical className="w-4 h-4" /> {open ? 'Hide the proof' : 'Show the proof'}
            </button>
            {onFocus ? (
              <button onClick={() => onFocus(scene)} className="inline-flex items-center gap-1.5 text-blue-400 hover:text-blue-300">
                <MapPinned className="w-4 h-4" /> Who was inside at the peak
              </button>
            ) : (
              <Link to={peakLink} className="inline-flex items-center gap-1.5 text-blue-400 hover:text-blue-300">
                <MapPinned className="w-4 h-4" /> Who was inside at the peak
              </Link>
            )}
          </div>
          {open && <div className="mt-3"><SceneProof scene={scene} /></div>}
        </div>
      </div>
    </article>
  );
}
