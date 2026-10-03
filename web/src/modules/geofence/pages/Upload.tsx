/**
 * Upload a trip.
 *
 * Drop a spreadsheet of one trip's GPS and the whole pipeline runs over it,
 * against the same fence master and the same settings as a batch run — but
 * writing only into its own tables, so nothing here can move a published number.
 */
import { useCallback, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertTriangle, CheckCircle2, Clock, Download, FileSpreadsheet, Hexagon, Loader2, MapPinned,
  Trash2, Upload as UploadIcon,
} from 'lucide-react';
import { api, uploadFile, uploadUrls } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, EntityLink, ErrorBox, Note, PageHeader, QualityBadge, Spinner,
} from '../components/ui';
import { fmtDateTime, fmtInt, fmtKm } from '../lib/format';

const ACCEPT = '.xlsx,.xlsm,.csv,.tsv,.txt,.json';

export default function Upload() {
  const navigate = useNavigate();
  const list = useApi(() => api.uploads(), []);
  const sample = useApi(() => api.uploadSample(500).catch(() => null), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [label, setLabel] = useState('');
  const input = useRef<HTMLInputElement>(null);

  const send = useCallback(async (file: File) => {
    setError(null);
    setPct(0);
    setBusy(`Reading ${file.name}`);
    try {
      const res = await uploadFile(file, label, p => {
        setPct(p);
        if (p >= 100) setBusy('Running the pipeline — cleaning, fitting, indexing, detecting');
      });
      navigate(`/geo/upload/${res.upload_id}`);
    } catch (e: any) {
      setError(e?.message || 'the upload failed');
      setBusy(null);
      list.reload();
    }
  }, [label, navigate, list]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) send(f);
  };

  const remove = async (id: number) => {
    await api.uploadDelete(id).catch(() => undefined);
    list.reload();
  };

  const s = sample.data?.trip;

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Upload a trip"
        subtitle="A spreadsheet of one trip's GPS, and every step the engine takes on it — what it
          refused and why, how it corrected the receiver's scatter, which geofences the index offered,
          how each crossing was confirmed, and how long the truck stood inside each fence. Every step
          downloads."
      />

      {error && <div className="mb-6"><ErrorBox error={error} /></div>}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="Drop the file" icon={UploadIcon} className="xl:col-span-2"
          subtitle="Excel (.xlsx), CSV or JSON. One row per GPS fix. Only a timestamp, a latitude and a
            longitude are required — speed is what makes the standstill handling work, so include it if
            the export has it.">
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => !busy && input.current?.click()}
            className={`rounded-xl border-2 border-dashed p-10 text-center transition-colors cursor-pointer ${
              busy ? 'border-gray-800 bg-gray-800/20 cursor-wait'
                : dragging ? 'border-blue-500 bg-blue-950/20' : 'border-gray-700 hover:border-gray-600 hover:bg-gray-800/30'}`}>
            <input ref={input} type="file" accept={ACCEPT} className="hidden" disabled={!!busy}
              onChange={e => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ''; }} />
            {busy ? (
              <div className="space-y-3">
                <Loader2 className="w-8 h-8 animate-spin text-blue-400 mx-auto" />
                <p className="text-sm text-gray-300">{busy}</p>
                {pct > 0 && pct < 100 && (
                  <div className="max-w-xs mx-auto h-1.5 bg-gray-800 rounded-full overflow-hidden">
                    <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
                  </div>
                )}
                <p className="text-xs text-gray-500">
                  A 4,500-fix trip takes a few seconds: cleaning, fitting, one index build, then the
                  detector against every candidate fence.
                </p>
              </div>
            ) : (
              <>
                <FileSpreadsheet className="w-10 h-10 text-gray-600 mx-auto mb-3" />
                <p className="text-sm text-gray-300 font-medium">Drop a trip here, or click to choose one</p>
                <p className="text-xs text-gray-500 mt-1">{ACCEPT.replace(/\./g, '').replace(/,/g, ' · ')}</p>
              </>
            )}
          </div>
          <label className="flex items-center gap-2 mt-4 text-xs text-gray-500">
            Label (optional)
            <input value={label} onChange={e => setLabel(e.target.value)} disabled={!!busy}
              placeholder="e.g. Jamshedpur → Arrah, July run"
              className="flex-1 bg-field border border-gray-700 rounded-lg text-sm text-gray-200 px-3 py-1.5
                focus:outline-none focus:border-blue-500 disabled:opacity-50" />
          </label>
        </Card>

        <Card title="No file to hand?" icon={Download}
          subtitle="Take a real long trip straight out of the feed as a spreadsheet, then upload it back
            here. It is the same layout the parser documents as canonical, so it also serves as the
            example of what an upload should look like.">
          {sample.loading ? <Spinner /> : s ? (
            <>
              <div className="space-y-2 mb-4">
                {[
                  ['Trip', s.i_trip_no],
                  ['Vehicle', s.s_asset_id],
                  ['Lane', `${s.s_origin || '?'} → ${s.s_destination || '?'}`],
                  ['Distance', fmtKm(s.d_distance_km)],
                  ['GPS fixes', fmtInt(s.i_pings_read)],
                  ['Places it visited', fmtInt(s.i_places)],
                  ['Fence visits', fmtInt(s.i_visits)],
                ].map(([k, v]) => (
                  <div key={String(k)} className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="text-gray-500 text-xs">{k}</span>
                    <span className="text-gray-200 tabular text-right">{v ?? '—'}</span>
                  </div>
                ))}
              </div>
              <a href={uploadUrls.sample(500)}
                className="flex items-center justify-center gap-2 w-full px-3 py-2 rounded-lg bg-blue-600/15
                  text-blue-400 hover:bg-blue-600/25 text-sm font-medium">
                <Download className="w-4 h-4" /> Download this trip as Excel
              </a>
              <p className="text-xs text-gray-600 mt-3">
                Uploading it back should reproduce the batch run's numbers for the same trip exactly —
                which is the first thing worth checking.
              </p>
            </>
          ) : (
            <Note tone="warn" title="No sample available.">
              A sample needs a finished batch run to pick a trip from. Run the pipeline first, or upload
              a file of your own.
            </Note>
          )}
        </Card>
      </div>

      <Card title="What the file needs to contain" icon={Hexagon}
        subtitle="The header row is matched against known spellings rather than a fixed layout, so most
          fleet exports load without editing. The mapping it chose is the first thing the analysis shows
          you, before any number is computed from it."
        className="mb-6">
        <DataTable dense rowKey={r => r.field}
          columns={[
            { key: 'field', label: 'What the engine needs' },
            { key: 'need', label: 'Required?' },
            { key: 'names', label: 'Column names it recognises' },
            { key: 'why', label: 'What it is used for' },
          ]}
          rows={[
            { field: 'Timestamp', need: 'yes',
              names: 'dt_message, GPS DateTime, timestamp, device_time, date + time in two columns',
              why: 'Orders the trail and measures every duration. ISO, Excel serial, unix epoch and d/m/y are all read.' },
            { field: 'Latitude', need: 'yes', names: 'd_lat, latitude, lat',
              why: 'Decimal degrees. The India bounds check refuses anything outside 5–38.5°N.' },
            { field: 'Longitude', need: 'yes', names: 'd_long, longitude, lon, lng',
              why: 'Decimal degrees, 66–98.5°E.' },
            { field: 'Speed', need: 'strongly recommended', names: 'i_speed, speed, kmph',
              why: 'km/h. Separates standstills from movement — without it every fix is treated as moving, so standstill scatter is not averaged out and stops are not found.' },
            { field: 'Vehicle', need: 'optional', names: 's_asset_id, vehicle_no, reg_no',
              why: 'Only labels the result. A single value across the file is taken as the trip\'s vehicle.' },
            { field: 'Trip number', need: 'optional', names: 'i_trip_no, trip_no',
              why: 'Recorded so an upload can be tied back to a trip in the fleet system.' },
          ]} />
      </Card>

      <Card title="Uploaded trips" icon={MapPinned} pad={false}
        subtitle={undefined}>
        <div className="px-5 pt-1 pb-3">
          <p className="text-xs text-gray-500">Kept separately from the run ledger — an upload never changes
            what any other page reports.</p>
        </div>
        {list.error ? <div className="p-5"><ErrorBox error={list.error} onRetry={list.reload} /></div> : (
          <DataTable dense loading={list.loading} rows={list.data?.items ?? []}
            rowKey={r => r.i_upload_id}
            onRowClick={r => r.s_status === 'ready' && navigate(`/geo/upload/${r.i_upload_id}`)}
            empty="Nothing uploaded yet"
            columns={[
              { key: 'i_upload_id', label: '#', render: r => <span className="text-gray-500 tabular">{r.i_upload_id}</span> },
              { key: 's_name', label: 'File', render: r => (
                <div className="min-w-0">
                  <p className="text-gray-200 truncate max-w-[280px]">{r.s_label || r.s_name}</p>
                  {r.s_label && <p className="text-xs text-gray-600 truncate max-w-[280px]">{r.s_name}</p>}
                </div>
              ) },
              { key: 's_status', label: 'Status', render: r => r.s_status === 'ready'
                ? <Badge variant="success"><CheckCircle2 className="w-3 h-3" />ready</Badge>
                : r.s_status === 'failed'
                  ? <Badge variant="danger" title={r.s_error || undefined}><AlertTriangle className="w-3 h-3" />failed</Badge>
                  : <Badge variant="warning">{r.s_status}</Badge> },
              { key: 's_asset_id', label: 'Vehicle', render: r => r.s_asset_id || '—' },
              { key: 'dt_first_ping', label: 'GPS from', render: r => fmtDateTime(r.dt_first_ping) },
              { key: 'i_rows_parsed', label: 'Fixes', align: 'right', render: r => fmtInt(r.i_rows_parsed) },
              { key: 'd_distance_km', label: 'Distance', align: 'right', render: r => fmtKm(r.d_distance_km) },
              { key: 'i_places', label: 'Places', align: 'right', render: r => fmtInt(r.i_places) },
              { key: 'i_visits', label: 'Fence visits', align: 'right', render: r => fmtInt(r.i_visits) },
              { key: 's_quality', label: 'GPS', render: r => <QualityBadge quality={r.s_quality} /> },
              { key: 'd_seconds', label: 'Analysed in', align: 'right',
                render: r => r.d_seconds ? <span className="text-gray-500 text-xs tabular">
                  <Clock className="inline w-3 h-3 mr-0.5" />{r.d_seconds.toFixed(1)}s</span> : '—' },
              { key: 'act', label: '', render: r => (
                <div className="flex items-center gap-2 justify-end">
                  {r.s_status === 'ready' && <EntityLink to={`/geo/upload/${r.i_upload_id}`}>open</EntityLink>}
                  <button onClick={e => { e.stopPropagation(); remove(r.i_upload_id); }}
                    title="Delete this upload and everything derived from it"
                    className="text-gray-600 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              ) },
            ]} />
        )}
      </Card>

      <p className="text-xs text-gray-600 mt-4">
        Uploads are analysed with the same engine, the same fence master and the same thresholds as the
        nightly run. Nothing uploaded here is written into the run ledger, so Day Summary, Geofences and
        Trips are unaffected — and equally, an upload is not counted in any fleet total.
      </p>
    </div>
  );
}
