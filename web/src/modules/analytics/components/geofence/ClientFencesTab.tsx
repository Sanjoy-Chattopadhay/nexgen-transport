import { useState } from 'react';
import {
  Upload, CheckCircle2, XCircle, AlertTriangle, ArrowRight,
  FileWarning, MapPin, Columns3,
} from 'lucide-react';
import Spinner from '../ui/Spinner';
import DataTable from '../ui/DataTable';
import GeofenceMap from './GeofenceMap';
import type { FenceShape } from './GeofenceMap';
import { previewClientFences, importClientFences } from '../../services/geofence';
import type { ImportPreview } from '../../services/geofence';

/**
 * The client handover, made visible before it is committed.
 *
 * Every fence in the system today is derived from the GPS trail, because the
 * eTrans feed carries node names and no geometry at all. Those derived fences
 * are a stand-in. This is the screen where the client's real geofences replace
 * them — and, more usefully in a demo, where the client can drop their own file
 * in and watch it be read, checked and priced before anything is written.
 *
 * Three things are shown that the import itself cannot tell you:
 *
 *   * which of THEIR columns we read as what. A silent guess about which column
 *     held the latitude is how you get a confident, wrong fence.
 *   * how far each fence moves from the derived one it replaces. A small shift
 *     says our stand-in was already right; a 1,000 km shift says it was not.
 *   * which names match no trip. The node name is the only join available, and
 *     "PITHAMPUR" against a feed carrying "PITHAMPUR (M.P.)" silently stores a
 *     fence that nothing is ever measured against.
 */

/** Minimal RFC-4180 CSV reader — quoted fields, embedded commas and newlines. */
function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;

  const src = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n'); // BOM + CRLF
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (quoted) {
      if (c === '"') {
        if (src[i + 1] === '"') { cell += '"'; i++; } else { quoted = false; }
      } else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }

  const nonEmpty = rows.filter(r => r.some(v => v.trim() !== ''));
  if (nonEmpty.length < 2) return [];
  const header = nonEmpty[0].map(hd => hd.trim());
  return nonEmpty.slice(1).map(r =>
    Object.fromEntries(header.map((hd, i) => [hd, (r[i] ?? '').trim()])),
  );
}

function readRows(text: string): { rows: Record<string, unknown>[]; error: string | null } {
  const trimmed = text.trim();
  if (!trimmed) return { rows: [], error: null };
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return { rows: parsed, error: null };
      for (const k of ['geofences', 'fences', 'data', 'results']) {
        if (Array.isArray(parsed?.[k])) return { rows: parsed[k], error: null };
      }
      return { rows: [parsed], error: null };
    } catch (e) {
      return { rows: [], error: `That is not valid JSON: ${(e as Error).message}` };
    }
  }
  const rows = parseCsv(trimmed);
  return {
    rows,
    error: rows.length ? null : 'No data rows found — the file needs a header row and at least one fence.',
  };
}

const SAMPLE = `Location Name,Latitude,Longitude,Geofence Radius,Type
JAMSHEDPUR,22.783481,86.214248,15000,origin
SANAND,22.985000,72.383000,1500,destination
FARIDABAD,28.408900,77.317700,1200,destination
PITHAMPUR,22.602000,75.681000,1800,destination
BAD ROW SWAPPED,86.214248,22.783481,1500,destination`;

const FIELD_LABEL: Record<string, string> = {
  name: 'Location name',
  lat: 'Latitude',
  lon: 'Longitude',
  radius_m: 'Radius (m)',
  role: 'Role',
};

function ColumnMapping({ mapping }: { mapping: Record<string, string | null> }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
      {Object.entries(FIELD_LABEL).map(([field, label]) => {
        const col = mapping[field];
        const required = ['name', 'lat', 'lon'].includes(field);
        const missing = !col;
        return (
          <div
            key={field}
            className={`rounded-lg border px-3 py-2 ${
              missing && required
                ? 'border-red-900/60 bg-red-950/20'
                : missing
                  ? 'border-gray-800 bg-gray-950'
                  : 'border-emerald-900/50 bg-emerald-950/15'
            }`}
          >
            <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
            <p
              className={`text-sm font-medium mt-0.5 truncate ${
                missing && required ? 'text-red-400'
                  : missing ? 'text-gray-600' : 'text-emerald-300'
              }`}
              title={col ?? undefined}
            >
              {col ?? (required ? 'not found' : 'default')}
            </p>
          </div>
        );
      })}
    </div>
  );
}

/** Pull a readable message out of an axios error without reaching for `any`. */
function errMessage(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || (e as Error)?.message || fallback;
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div className="bg-gray-950 rounded-lg border border-gray-800 px-4 py-3">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-xl font-bold mt-0.5 ${tone}`}>{value}</p>
    </div>
  );
}

export default function ClientFencesTab() {
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [committed, setCommitted] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const runPreview = async (raw: string) => {
    setError(null);
    setCommitted(null);
    setConfirming(false);
    const { rows, error: parseError } = readRows(raw);
    if (parseError) { setError(parseError); setPreview(null); return; }
    if (!rows.length) { setPreview(null); return; }
    setBusy(true);
    try {
      const res = await previewClientFences(rows);
      setPreview(res.data);
    } catch (e: unknown) {
      setError(errMessage(e, 'Preview failed'));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    const { rows } = readRows(text);
    setBusy(true);
    setError(null);
    try {
      const res = await importClientFences(rows, false);
      setCommitted(
        `${res.data.inserted} fence(s) added, ${res.data.updated} replaced. ` +
        'Re-run the backfill so the stamps reflect the new geometry.',
      );
      setConfirming(false);
    } catch (e: unknown) {
      setError(errMessage(e, 'Import failed'));
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (file: File) => {
    const raw = await file.text();
    setText(raw);
    await runPreview(raw);
  };

  const shapes: FenceShape[] = (preview?.accepted ?? []).map(f => ({
    key: f.key, name: f.name, role: f.role, lat: f.lat, lon: f.lon,
    radius_m: f.radius_m, source: 'client', proposed: true,
    label: f.action === 'new'
      ? 'New — no fence exists for this node today'
      : `Replaces the ${f.replaces_source} fence, moving it ${f.shift_km} km`,
  }));

  return (
    <>
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-white">Bring your own geofences</h2>
          <p className="text-xs text-gray-500 mt-1 max-w-4xl">
            The eTrans feed carries node names and no geometry, so every fence in this system
            today is derived from the GPS trail — a stand-in until you hand over the real ones.
            Drop your export here and it will be read, checked and costed before anything is
            written. CSV or JSON; the column names are yours, not ours.
          </p>
        </div>

        <div
          onDragOver={e => e.preventDefault()}
          onDrop={e => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) onFile(f);
          }}
          className="border border-dashed border-gray-700 rounded-lg p-4 mb-3"
        >
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            onBlur={e => runPreview(e.target.value)}
            spellCheck={false}
            placeholder={'Paste your CSV or JSON here, or drop the file onto this box…'}
            className="w-full h-40 bg-gray-950 border border-gray-800 rounded-md px-3 py-2
                       text-xs font-mono text-gray-200 placeholder-gray-600
                       focus:outline-none focus:border-blue-700 resize-y"
          />
          <div className="flex items-center gap-3 flex-wrap mt-3">
            <label className="px-3 py-1.5 rounded-md text-sm font-medium bg-blue-600/15
                              text-blue-400 hover:bg-blue-600/25 cursor-pointer transition-colors
                              flex items-center gap-2">
              <Upload className="w-4 h-4" />
              Choose a file
              <input
                type="file"
                accept=".csv,.json,.txt"
                className="hidden"
                onChange={e => {
                  const f = e.target.files?.[0];
                  if (f) onFile(f);
                }}
              />
            </label>
            <button
              onClick={() => runPreview(text)}
              disabled={!text.trim() || busy}
              className="px-3 py-1.5 rounded-md text-sm font-medium bg-gray-800 text-gray-200
                         hover:bg-gray-700 disabled:opacity-40 transition-colors"
            >
              Check it
            </button>
            <button
              onClick={() => { setText(SAMPLE); runPreview(SAMPLE); }}
              className="text-xs text-gray-500 hover:text-gray-300 underline underline-offset-4"
            >
              use a sample file
            </button>
            <span className="text-xs text-gray-600 ml-auto">
              Nothing is written until you press Import.
            </span>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-900/60 bg-red-950/20 p-3 flex items-start gap-2">
            <XCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}
        {committed && (
          <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/20 p-3 flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
            <p className="text-sm text-emerald-300">{committed}</p>
          </div>
        )}
      </div>

      {busy && <Spinner />}

      {preview && !busy && (
        <>
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
            <div className="flex items-center gap-2 mb-1">
              <Columns3 className="w-4 h-4 text-gray-400" />
              <h2 className="text-lg font-semibold text-white">How we read your columns</h2>
            </div>
            <p className="text-xs text-gray-500 mb-4 max-w-4xl">
              Your headers, matched to the fields we need. Check these before importing — a
              wrong guess about which column held the latitude produces a fence that is
              confidently in the wrong place, and no error anywhere.
            </p>
            <ColumnMapping mapping={preview.columns_detected} />
            {preview.unreadable_fields.length > 0 && (
              <div className="mt-3 rounded-lg border border-red-900/60 bg-red-950/20 p-3 flex items-start gap-2">
                <FileWarning className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
                <p className="text-sm text-red-400">
                  No column supplies{' '}
                  <b>{preview.unreadable_fields.map(f => FIELD_LABEL[f] ?? f).join(', ')}</b>.
                  Nothing can be imported until a header names one.
                </p>
              </div>
            )}
          </div>

          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
            <h2 className="text-lg font-semibold text-white mb-4">What importing this would do</h2>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
              <Stat label="Rows read" value={preview.rows_read} tone="text-gray-200" />
              <Stat label="Accepted" value={preview.accepted_count} tone="text-emerald-400" />
              <Stat label="Rejected" value={preview.rejected_count}
                tone={preview.rejected_count ? 'text-red-400' : 'text-gray-500'} />
              <Stat label="New fences" value={preview.new_count} tone="text-blue-400" />
              <Stat label="Trips affected" value={preview.trips_affected.toLocaleString()}
                tone="text-purple-400" />
            </div>

            <DataTable<Record<string, unknown>>
              columns={[
                { key: 'name', label: 'Location' },
                { key: 'role', label: 'Role' },
                { key: 'coords', label: 'Centre' },
                { key: 'radius', label: 'Radius' },
                { key: 'effect', label: 'Effect' },
                { key: 'trips', label: 'Trips' },
              ]}
              data={(preview.accepted ?? []).map(f => ({
                ...f,
                coords: (
                  <span className="font-mono text-xs text-gray-400">
                    {f.lat.toFixed(4)}, {f.lon.toFixed(4)}
                  </span>
                ),
                radius: <span className="text-gray-300">{(f.radius_m / 1000).toFixed(1)} km</span>,
                effect: f.action === 'new'
                  ? <span className="text-blue-400 text-xs">new fence</span>
                  : (
                    <span className="text-xs text-gray-300 flex items-center gap-1.5">
                      replaces <span className="text-gray-500">{f.replaces_source}</span>
                      <ArrowRight className="w-3 h-3 text-gray-600" />
                      <span className={
                        (f.shift_km ?? 0) > 25 ? 'text-amber-400 font-medium' : 'text-gray-400'
                      }>
                        moves {f.shift_km} km
                      </span>
                    </span>
                  ),
                trips: f.matches_trips
                  ? <span className="text-gray-300">{f.trips_affected.toLocaleString()}</span>
                  : (
                    <span className="text-amber-400 text-xs flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3" />
                      name matches no trip
                    </span>
                  ),
              }))}
              emptyMessage="No valid fences in this file."
            />
          </div>

          {preview.unmatched.length > 0 && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
              <div className="mb-3">
                <h2 className="text-lg font-semibold text-white">
                  Names that match no trip
                </h2>
                <p className="text-xs text-gray-500 mt-1 max-w-4xl">
                  The node name is the only join between your geofences and the trip feed. These
                  would be stored and then never measured against anything, because no trip
                  carries that spelling. Where the feed has something close, it is named below.
                </p>
              </div>
              <ul className="space-y-2">
                {preview.unmatched.map(u => (
                  <li key={u.name} className="flex items-start gap-2 text-sm">
                    <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
                    <span className="text-gray-300">
                      <b>{u.name}</b>
                      {u.did_you_mean.length > 0 ? (
                        <>
                          {' '}— the feed spells this{' '}
                          <span className="text-emerald-300 font-mono text-xs">
                            {u.did_you_mean.join('  ·  ')}
                          </span>
                        </>
                      ) : (
                        <span className="text-gray-500">
                          {' '}— nothing similar in the feed. Either no trip has run there yet,
                          or the name differs from the one the TMS uses.
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.rejected_count > 0 && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
              <div className="mb-3">
                <h2 className="text-lg font-semibold text-white">Rejected rows</h2>
                <p className="text-xs text-gray-500 mt-1 max-w-4xl">
                  These are refused rather than warned about. A geofence with swapped lat/lon is
                  a perfectly valid coordinate somewhere in the world — Jamshedpur written
                  (86.2, 22.8) is in Somalia — so importing it would raise nothing and quietly
                  report every truck as absent, zeroing the detention figures.
                </p>
              </div>
              <ul className="space-y-1.5">
                {preview.rejected.map(r => (
                  <li key={r.row} className="flex items-start gap-2 text-sm">
                    <XCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
                    <span className="text-gray-300">
                      <span className="text-gray-500">row {r.row}:</span> {r.reason}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {shapes.length > 0 && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
              <div className="mb-4 flex items-center gap-2">
                <MapPin className="w-4 h-4 text-gray-400" />
                <h2 className="text-lg font-semibold text-white">Where your fences land</h2>
              </div>
              <GeofenceMap fences={shapes} height={460} />
            </div>
          )}

          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-base font-semibold text-white">Import these fences</h2>
                <p className="text-xs text-gray-500 mt-1 max-w-3xl">
                  Imported fences are marked as yours and are never overwritten by our
                  derivation again. Derived fences for every node you have <i>not</i> sent stay
                  active, so a partial handover cannot blind the rest of the fleet. The stamps
                  update on the next backfill run.
                </p>
              </div>
              {confirming ? (
                <div className="flex items-center gap-2">
                  <button
                    onClick={commit}
                    disabled={busy}
                    className="px-3 py-1.5 rounded-md text-sm font-medium bg-emerald-600/20
                               text-emerald-300 hover:bg-emerald-600/30 transition-colors"
                  >
                    Yes, write {preview.accepted_count} fence(s)
                  </button>
                  <button
                    onClick={() => setConfirming(false)}
                    className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirming(true)}
                  disabled={busy || preview.accepted_count === 0
                    || preview.unreadable_fields.length > 0}
                  className="px-4 py-2 rounded-md text-sm font-medium bg-blue-600/15 text-blue-400
                             hover:bg-blue-600/25 disabled:opacity-40 transition-colors"
                >
                  Import {preview.accepted_count} fence(s)
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}
