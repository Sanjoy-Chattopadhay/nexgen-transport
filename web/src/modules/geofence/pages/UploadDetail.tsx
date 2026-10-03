/**
 * One uploaded trip, from the file to the KPIs, with every step in between.
 *
 * The page is ordered the way the pipeline runs, because the point is that a
 * reader can follow it: the file is read, rows are refused, the receiver's
 * scatter is corrected, the index narrows 4,500 fences to a handful, geometry
 * turns a point and a polygon into one signed number, a state machine decides
 * which changes of state are real, and only then does anything call itself a
 * visit. The answer comes last, and the check on the answer comes after it.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import L from 'leaflet';
import {
  AlertTriangle, CheckCircle2, Clock, Download, Factory, FileSpreadsheet, Hexagon,
  ListTree, MapPinned, Package, PackageOpen, Route, SatelliteDish, ShieldCheck, Truck, XCircle,
} from 'lucide-react';
import { api, uploadUrls } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, ErrorBox, KPI, Note, PageHeader, QualityBadge, ScaleBadge,
  Spinner, Toggle, Toolbar,
} from '../components/ui';
import Step, { StatGrid, type StepDoc } from '../components/upload/Step';
import { KPIGrid } from '../components/drill';
import IndexMap from '../components/upload/IndexMap';
import GeoMap from '../components/map/GeoMap';
import { dot, escapeHtml, fencePolygon } from '../components/map/layers';
import { PALETTE } from '../lib/theme';
import { fmtDateTime, fmtDuration, fmtInt, fmtKm, fmtMetres } from '../lib/format';

/** The stages the steps group into, in pipeline order. */
const STAGES: { title: string; blurb: string; keys: string[] }[] = [
  {
    title: 'Reading the file',
    blurb: 'Which column is which, and which rows carried nothing usable.',
    keys: ['read'],
  },
  {
    title: 'Deciding which fixes may vote',
    blurb: 'A raw device feed into a clean, ordered, physically plausible trail — and an honest '
         + 'statement of what was thrown away and how complete what remains is.',
    keys: ['admit', 'teleport', 'admitted'],
  },
  {
    title: 'Fitting: where was the truck really',
    blurb: 'Not "is this fix valid" but "given everything around it, what is the best estimate of '
         + 'where the truck was at that instant". Raw fixes are never overwritten.',
    keys: ['standstill', 'median', 'spikes', 'snap', 'stops', 'gaps', 'fitted'],
  },
  {
    title: 'Finding the candidate geofences',
    blurb: 'Four and a half thousand polygons, five million potential tests, and the structures that '
         + 'turn that into a few thousand.',
    keys: ['index', 'hilbert', 'candidates', 'prune', 'geometry'],
  },
  {
    title: 'Deciding what actually happened',
    blurb: 'A signed distance per fix into confirmed crossings, and crossings into stays.',
    keys: ['band', 'detect', 'visits', 'nesting', 'places', 'events', 'violations'],
  },
  {
    title: 'The answer, and the check on it',
    blurb: 'Time in each geofence, loading and unloading — then the same facts re-derived three other '
         + 'ways to see whether they hold.',
    keys: ['kpis', 'verify'],
  },
];

export default function UploadDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const { data, loading, error, reload } = useApi(() => api.upload(id), [id]);
  const track = useApi(() => api.uploadTrack(id).catch(() => null), [id]);
  const [map, setMap] = useState<L.Map | null>(null);
  const [layers, setLayers] = useState({ raw: true, fitted: true, fences: true, stops: true, spikes: true });
  const set = (k: keyof typeof layers) => (v: boolean) => setLayers(s => ({ ...s, [k]: v }));

  const steps = useMemo(() => {
    const by: Record<string, StepDoc> = {};
    for (const s of (data?.steps ?? [])) by[s.key] = s;
    return by;
  }, [data]);

  const t = track.data;

  // The route, the fences it entered, its stops and the fixes the fit moved.
  useEffect(() => {
    if (!map || !t) return undefined;
    const group = L.layerGroup().addTo(map);
    const pts: any[][] = t.points;

    if (layers.fences) {
      for (const f of t.fences ?? []) {
        if (!f.ring?.length || f.scale === 'regional') continue;
        fencePolygon(f, { onClick: () => navigate(`/geo/geofences/${f.site_id}`) }).addTo(group);
      }
    }
    if (layers.raw) {
      L.polyline(pts.filter(p => p[1] != null).map(p => [p[1], p[2]] as [number, number]),
        { color: PALETTE.rawTrack, weight: 1, opacity: 0.55 }).addTo(group);
    }
    if (layers.fitted) {
      L.polyline(pts.filter(p => p[4] != null).map(p => [p[4], p[5]] as [number, number]),
        { color: PALETTE.blue, weight: 2.5, opacity: 0.9 }).addTo(group);
    }
    if (layers.spikes) {
      for (const p of pts) {
        if (p[6] === 'spike' && p[4] != null) {
          L.polyline([[p[1], p[2]], [p[4], p[5]]] as [number, number][],
            { color: PALETTE.amber, weight: 1, dashArray: '2 3' }).addTo(group);
          dot(p[1], p[2], PALETTE.amber, 4,
            `Corrected ${fmtMetres(p[7])} · ${escapeHtml(String(p[0]))}`).addTo(group);
        }
      }
    }
    if (layers.stops) {
      for (const s of t.stops ?? []) {
        L.circleMarker([s.lat, s.lon], {
          radius: Math.min(14, 4 + Math.sqrt(s.seconds / 600)),
          color: PALETTE.green, weight: 1.5, fillOpacity: 0.15,
        }).bindTooltip(`Stopped ${fmtDuration(s.seconds)} from ${fmtDateTime(s.from)}`
          + ` · GPS scatter ${fmtMetres(s.spread_m)}`, { className: 'geo-tip' }).addTo(group);
      }
    }
    return () => { group.remove(); };
  }, [map, t, layers, navigate]);

  useEffect(() => {
    if (!map || !t?.bbox) return;
    map.fitBounds([[t.bbox[1], t.bbox[0]], [t.bbox[3], t.bbox[2]]],
      { padding: [30, 30], maxZoom: 14 });
  }, [map, t?.bbox]); // eslint-disable-line react-hooks/exhaustive-deps

  const kpis = steps.kpis;
  const verify = steps.verify;
  const timeline = (kpis?.extra?.timeline ?? []) as any[];
  const st = kpis?.stats ?? {};
  const vs = verify?.stats ?? {};
  const allPassed = verify && vs['identity checks'] === vs['identity checks passed']
    && !vs['index false negatives'] && !vs['MySQL disagreed'];

  if (loading && !data) return <Spinner label="Loading the analysis" />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data) return null;
  const u = data.upload;

  if (u.s_status !== 'ready') {
    return (
      <div className="animate-fade-in">
        <PageHeader back={{ to: '/geo/upload', label: 'All uploads' }} title={u.s_label || u.s_name} />
        <Note tone="warn" title={`This upload is ${u.s_status}.`}>
          {u.s_error || 'It has not been analysed yet.'}
        </Note>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        back={{ to: '/geo/upload', label: 'All uploads' }}
        title={u.s_label || u.s_name}
        badges={<>
          <QualityBadge quality={u.s_quality} />
          {u.s_asset_id && <Badge variant="info">{u.s_asset_id}</Badge>}
          {u.i_source_trip && <Badge>trip {u.i_source_trip}</Badge>}
        </>}
        subtitle={<>
          {fmtInt(u.i_rows_parsed)} fixes read from <span className="text-gray-400">{u.s_name}</span>
          {' · '}GPS from {fmtDateTime(u.dt_first_ping)} to {fmtDateTime(u.dt_last_ping)}
          {' · '}analysed in {u.d_seconds?.toFixed(1)}s against {fmtInt(u.j_params?.fences_indexed)} geofences
        </>}
        actions={<Toolbar>
          <a href={uploadUrls.report(id)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600/15 text-blue-400 hover:bg-blue-600/25 text-sm font-medium">
            <FileSpreadsheet className="w-4 h-4" /> Download the whole analysis
          </a>
          <a href={uploadUrls.pings(id)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-gray-800 text-gray-300 hover:bg-gray-700 text-sm">
            <Download className="w-4 h-4" /> The fixes as parsed
          </a>
        </Toolbar>}
      />

      {/* ---------------- the answer, up front ---------------- */}
      <KPIGrid className="grid grid-cols-2 md:grid-cols-4 2xl:grid-cols-8 gap-3 mb-6">
        <KPI label="Geofences crossed" value={fmtInt(st['geofences touched'])} icon={Hexagon} color="blue"
          hint={`${fmtInt(st['fence visits'])} confirmed stays`} details={() => <FenceTable rows={kpis?.rows ?? []} />} />
        <KPI label="Places visited" value={fmtInt(st['places visited'])} icon={Factory} color="cyan"
          details={() => <PlaceTable rows={timeline} />} />
        <KPI label="Loading" value={fmtDuration(st['loading (s)'])} icon={Package} color="purple"
          hint={st['loading — at'] || undefined} details={() => <PlaceTable rows={timeline.filter(p => p.role === 'loading')} />} />
        <KPI label="Unloading" value={fmtDuration(st['unloading (s)'])} icon={PackageOpen} color="purple"
          hint={st['unloading — at'] || undefined} details={() => <PlaceTable rows={timeline.filter(p => p.role === 'unloading')} />} />
        <KPI label="Transit" value={fmtDuration(st['transit (s)'])} icon={Route} color="green"
          hint="first exit → last arrival" details={() => <PlaceTable rows={timeline.filter(p => p.role === 'intermediate')}
            empty="No intermediate places: the truck drove from loading to unloading without stopping at another fence." />} />
        <KPI label="Distance" value={fmtKm(st['distance travelled (km)'])} icon={Truck} color="green"
          hint={st['unobserved distance (km)'] ? `+${fmtKm(st['unobserved distance (km)'])} unobserved` : undefined}
          details={() => <StatGrid stats={Object.fromEntries(Object.entries(st).filter(([k]) => /km|distance|stops|quality/i.test(k)))} />} />
        <KPI label="Alerts" value={fmtInt(st.alerts)} icon={AlertTriangle} color={st.alerts ? 'red' : 'gray'}
          details={() => steps.violations ? <StepRows doc={steps.violations} /> : <p className="text-sm text-gray-500">No alerts.</p>} />
        <KPI label="GPS fixes used" value={fmtInt(st['GPS fixes used'])} icon={SatelliteDish} color="blue"
          hint={`of ${fmtInt(u.i_rows_parsed)} parsed`}
          details={() => <StatGrid stats={Object.fromEntries(Object.entries(st).filter(([k]) => /fix|GPS|rows/i.test(k)))} />} />
      </KPIGrid>

      {verify && (
        <div className="mb-6">
          <Note tone={allPassed ? 'good' : 'warn'}
            title={allPassed ? 'Every independent check passed.' : 'A check did not pass.'}>
            {fmtInt(vs['identity checks passed'])} of {fmtInt(vs['identity checks'])} arithmetic identities hold;
            a brute-force pass over {fmtInt(vs['brute-force polygon tests'])} fence × fix pairs with no index at all
            found {fmtInt(vs['index false negatives'])} fences the index failed to offer; and MySQL's own
            ST_Contains agreed with the engine on {fmtInt(vs['MySQL agreed'])} of {fmtInt(vs['MySQL probes'])} probes
            taken at fence boundaries. The full working is in the last step below.
          </Note>
        </div>
      )}

      {u.s_quality !== 'good' && (
        <div className="mb-6">
          <Note tone="warn" title={`GPS quality: ${u.s_quality}.`}>
            A geofence this trip appears not to have visited may simply have been passed while the tracker
            was silent. {st['unobserved distance (km)'] ? `${fmtKm(st['unobserved distance (km)'])} of this trip
            was travelled unobserved.` : ''}
          </Note>
        </div>
      )}

      {/* ---------------- time in each geofence ---------------- */}
      <Card title="How long the vehicle was inside each geofence" icon={Clock} className="mb-6"
        subtitle="Every fence the truck was confirmed inside, longest first. Nested fences are all kept —
          a truck at a mill is genuinely inside the mill, the works and the works' gate zone at once — so
          these rows overlap by design. 'Places' below folds them into destinations."
        actions={
          <a href={uploadUrls.step(id, 'kpis', 'xlsx')}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-gray-800 text-gray-300 hover:bg-gray-700">
            <Download className="w-3 h-3" /> Excel
          </a>}>
        <div className="max-h-[460px] overflow-y-auto">
          <DataTable dense rows={(kpis?.rows ?? []).map((r: any[]) => ({
            site: r[0], name: r[1], scale: r[2], category: r[3], visits: r[4], secs: r[5],
            hours: r[6], pings: r[7], first: r[8], last: r[9], speed: r[10], open: r[11],
          }))} rowKey={(r: any, i: number) => `${r.site}-${i}`}
            onRowClick={(r: any) => navigate(`/geo/geofences/${r.site}`)}
            empty="This trip entered no geofence"
            columns={[
              { key: 'name', label: 'Geofence', render: (r: any) => <span className="text-gray-100">{r.name}</span> },
              { key: 'scale', label: 'Scale', render: (r: any) => <ScaleBadge scale={r.scale} /> },
              { key: 'visits', label: 'Stays', align: 'right', render: (r: any) => fmtInt(r.visits) },
              { key: 'secs', label: 'Time inside', align: 'right',
                render: (r: any) => <span className="text-gray-100 font-medium">{fmtDuration(r.secs)}{r.open === 'yes' ? '+' : ''}</span> },
              { key: 'hours', label: 'Hours', align: 'right', render: (r: any) => r.hours?.toFixed(2) },
              { key: 'pings', label: 'Fixes inside', align: 'right', render: (r: any) => fmtInt(r.pings) },
              { key: 'first', label: 'First entered', render: (r: any) => fmtDateTime(r.first) },
              { key: 'last', label: 'Last left', render: (r: any) => fmtDateTime(r.last) },
              { key: 'speed', label: 'Max km/h', align: 'right', render: (r: any) => r.speed ?? '—' },
            ]} />
        </div>
      </Card>

      {/* ---------------- route and timeline ---------------- */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="The trip on the map" icon={MapPinned} className="xl:col-span-2"
          subtitle="Grey: the positions the receiver reported. Blue: the positions the geofences were
            decided on. Amber: fixes the fit moved, with a line to where it put them. Circles: stops.
            Outlines: the fences this trip was inside."
          actions={<Toolbar>
            <Toggle checked={layers.raw} onChange={set('raw')} label="Raw" />
            <Toggle checked={layers.fitted} onChange={set('fitted')} label="Fitted" />
            <Toggle checked={layers.fences} onChange={set('fences')} label="Fences" />
            <Toggle checked={layers.stops} onChange={set('stops')} label="Stops" />
            <Toggle checked={layers.spikes} onChange={set('spikes')} label="Corrections" />
          </Toolbar>}>
          {track.error ? <ErrorBox error={track.error} /> : <GeoMap height={520} onReady={setMap} />}
          {t && t.returned < t.total_points && (
            <p className="text-xs text-gray-500 mt-2">
              Drawing {fmtInt(t.returned)} of {fmtInt(t.total_points)} fixes; every corrected fix and every
              stop boundary is kept whatever the thinning.
            </p>
          )}
        </Card>

        <Card title="Where it went, in order" icon={ListTree}
          subtitle="Facility fences that overlap in time are one place, named by the outermost of them.">
          {timeline.length === 0 ? (
            <p className="text-sm text-gray-500 py-6 text-center">No facility fence was visited.</p>
          ) : (
            <div className="space-y-2 max-h-[520px] overflow-y-auto pr-1">
              {timeline.map((p: any, i: number) => (
                <div key={i} className="rounded-lg border border-gray-800 bg-gray-800/40 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <button onClick={() => navigate(`/geo/geofences/${p.site_id}`)}
                      className="text-sm text-gray-100 font-medium text-left hover:text-blue-400">{p.name}</button>
                    {p.role !== 'intermediate' && (
                      <Badge variant={p.role === 'loading' ? 'purple' : 'cyan'}>{p.role}</Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-gray-400 mt-1.5 flex-wrap">
                    <span>{fmtDateTime(p.enter)}</span>
                    <span>→ {p.exit ? fmtDateTime(p.exit) : <span className="text-amber-400">still inside</span>}</span>
                    <span className="text-gray-200 font-medium">{fmtDuration(p.dwell_s)}</span>
                    <ScaleBadge scale={p.scale} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* ---------------- the steps ---------------- */}
      <div className="mb-4">
        <h2 className="text-lg font-bold text-white">Every step, in the order it ran</h2>
        <p className="text-sm text-gray-500 mt-1 max-w-4xl">
          Each step opens to its reasoning, the rule in symbols, the numbers it produced on this trip, one
          case worked through by hand, and the whole table to download. Nothing below is a re-description
          of the engine — it is the engine's own intermediate state, recorded as it ran.
        </p>
      </div>

      {(() => { let n = 0; return STAGES.map(stage => {
        const docs = stage.keys.map(k => steps[k]).filter(Boolean);
        if (!docs.length) return null;
        return (
          <div key={stage.title} className="mb-8">
            <div className="flex items-baseline gap-3 flex-wrap mb-3">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">{stage.title}</h3>
              <p className="text-xs text-gray-500 flex-1 min-w-[260px]">{stage.blurb}</p>
            </div>
            <div className="space-y-3">
              {docs.map(doc => {
                n += 1;
                const isIndex = doc.key === 'index';
                const isVerify = doc.key === 'verify';
                return (
                  <Step key={doc.key} doc={doc} uploadId={id} index={n}
                    defaultOpen={doc.key === 'kpis' || doc.key === 'verify'}>
                    {isIndex && (
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
                          The index over India — every fence, the tree's node boxes, and this trip's query
                        </p>
                        <IndexMap uploadId={id} />
                      </div>
                    )}
                    {doc.key === 'candidates' && doc.extra?.sweep && (
                      <SweepTable sweep={doc.extra.sweep} />
                    )}
                    {doc.key === 'read' && doc.extra?.sample && (
                      <SubTable title="The first rows, as read" data={doc.extra.sample} />
                    )}
                    {doc.key === 'read' && (doc.extra?.refused?.rows?.length ?? 0) > 0 && (
                      <SubTable title="Rows that could not be read at all" data={doc.extra!.refused} warn />
                    )}
                    {isVerify && <VerifyPanels extra={doc.extra} />}
                  </Step>
                );
              })}
            </div>
          </div>
        );
      }); })()}

      <Card title="The settings this analysis ran under" icon={ShieldCheck} iconClass="text-emerald-400">
        <p className="text-xs text-gray-500 mb-4 max-w-4xl">
          A table of dwell times with no statement of the thresholds that produced them cannot be verified
          by anyone, so they travel with the result — in the downloads too. Change one and re-run the same
          fixes to see exactly what it was doing.
        </p>
        <StatGrid stats={u.j_params ?? {}} />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------

function SweepTable({ sweep }: { sweep: { columns: string[]; rows: any[][] } }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
        What every other chunk size would have cost, on this trail
      </p>
      <DataTable dense rows={sweep.rows.map(r => ({ a: r[0], b: r[1], c: r[2], d: r[3], e: r[4] }))}
        columns={[
          { key: 'a', label: 'Fixes per chunk', align: 'right', render: r => fmtInt(r.a) },
          { key: 'b', label: 'Chunks', align: 'right', render: r => fmtInt(r.b) },
          { key: 'c', label: 'Tree descents (node boxes opened)', align: 'right', render: r => fmtInt(r.c) },
          { key: 'd', label: 'Candidate fences returned', align: 'right', render: r => fmtInt(r.d) },
          { key: 'e', label: '', render: r => r.e ? <Badge variant={r.e === 'in use' ? 'info' : 'neutral'}>{r.e}</Badge> : null },
        ]} />
      <p className="text-xs text-gray-600 mt-2">
        Smaller chunks hug the road and hand fewer fences to the geometry, but pay more descents to do it.
        The final answer is identical in every row — only the work changes.
      </p>
    </div>
  );
}

function SubTable({ title, data, warn }: {
  title: string; data: { columns: string[]; rows: any[][] }; warn?: boolean;
}) {
  return (
    <div>
      <p className={`text-xs font-semibold uppercase tracking-wider mb-2 ${warn ? 'text-amber-400' : 'text-gray-500'}`}>
        {title}{data.rows.length > 20 ? ` — first 20 of ${fmtInt(data.rows.length)}` : ''}
      </p>
      <div className="max-h-64 overflow-y-auto rounded-lg border border-gray-800">
        <DataTable dense rows={data.rows.slice(0, 20).map(r => Object.fromEntries(r.map((v, i) => [String(i), v])))}
          columns={data.columns.map((c, i) => ({ key: String(i), label: c }))} />
      </div>
    </div>
  );
}

function VerifyPanels({ extra }: { extra: any }) {
  const idx = extra?.index;
  const oracle = extra?.oracle;
  if (!idx && !oracle) return null;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {idx && (
        <div className="rounded-lg border border-gray-800 bg-gray-800/30 p-4">
          <p className="text-xs font-medium text-gray-200 mb-2 flex items-center gap-1.5">
            {idx.missed_by_the_index === 0
              ? <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              : <XCircle className="w-4 h-4 text-red-400" />}
            Brute force, with no index at all
          </p>
          <div className="space-y-1.5 text-xs">
            {[
              ['Fences tested against every fix', fmtInt(idx.brute_force.fences_tested)],
              ['Polygon tests run', fmtInt(idx.brute_force.polygon_tests)],
              ['Took', `${idx.brute_force.seconds}s`],
              ['Fences that really contained a fix', fmtInt(idx.fences_actually_containing_a_fix)],
              ['Candidates the index offered', fmtInt(idx.candidates_offered)],
              ['Offered but not containing (removed by the polygon test)',
                fmtInt(idx.false_positives_the_polygon_test_removed)],
              ['Containing but NOT offered (false negatives)', fmtInt(idx.missed_by_the_index)],
            ].map(([k, v]) => (
              <div key={String(k)} className="flex items-baseline justify-between gap-3">
                <span className="text-gray-500">{k}</span>
                <span className="text-gray-200 tabular">{v}</span>
              </div>
            ))}
          </div>
          <p className={`text-xs mt-2.5 ${idx.missed_by_the_index === 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {idx.verdict}
          </p>
          <p className="text-xs text-gray-600 mt-1.5 leading-relaxed">
            False positives are expected and harmless: a bounding box can contain a point the polygon does
            not, and the geometry removes them. False negatives would be a real defect — the index would be
            hiding a fence the truck was in — and this is the pass that would show it.
          </p>
        </div>
      )}
      {oracle && (
        <div className="rounded-lg border border-gray-800 bg-gray-800/30 p-4">
          <p className="text-xs font-medium text-gray-200 mb-2 flex items-center gap-1.5">
            {!oracle.disagreed ? <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              : <XCircle className="w-4 h-4 text-red-400" />}
            MySQL ST_Contains, as an independent oracle
          </p>
          <div className="space-y-1.5 text-xs">
            {[
              ['Probes', fmtInt(oracle.checked)],
              ['Agreed', fmtInt(oracle.agreed)],
              ['Disagreed', fmtInt(oracle.disagreed)],
            ].map(([k, v]) => (
              <div key={String(k)} className="flex items-baseline justify-between gap-3">
                <span className="text-gray-500">{k}</span><span className="text-gray-200 tabular">{v}</span>
              </div>
            ))}
          </div>
          <p className={`text-xs mt-2.5 ${!oracle.disagreed ? 'text-emerald-400' : 'text-red-400'}`}>
            {oracle.verdict}
          </p>
          <p className="text-xs text-gray-600 mt-1.5 leading-relaxed">
            A different implementation, by different people, reading a separately stored copy of the
            geometry. The engine runs off the vertex table; the WKT column MySQL reads is derived from it.
            Probes are taken at the fixes closest to fence boundaries, where an off-by-one in edge handling
            would actually show — not on open road, where both trivially answer "outside".
          </p>
          {oracle.rows?.length > 0 && (
            <div className="mt-3">
              <DataTable dense rows={oracle.rows.map((r: any[]) => ({
                site: r[0], name: r[1], lat: r[2], lon: r[3], mine: r[4], theirs: r[5] }))}
                columns={[
                  { key: 'name', label: 'Fence' }, { key: 'lat', label: 'Lat', align: 'right' },
                  { key: 'lon', label: 'Lon', align: 'right' },
                  { key: 'mine', label: 'Engine' }, { key: 'theirs', label: 'MySQL' },
                ]} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** The places the trip was at, in order, with their role. */
function PlaceTable({ rows, empty = 'None' }: { rows: any[]; empty?: string }) {
  return (
    <DataTable dense rows={rows} empty={empty}
      columns={[
        { key: 'role', label: 'Role', render: (r: any) => <Badge variant={r.role === 'loading' ? 'purple' : r.role === 'unloading' ? 'cyan' : 'neutral'}>{r.role}</Badge> },
        { key: 'name', label: 'Place', render: (r: any) => <span className="text-gray-100 text-xs">{r.name}</span> },
        { key: 'scale', label: 'Scale', render: (r: any) => <ScaleBadge scale={r.scale} /> },
        { key: 'enter', label: 'Arrived', render: (r: any) => <span className="text-xs">{fmtDateTime(r.enter)}</span> },
        { key: 'exit', label: 'Left', render: (r: any) => r.exit ? <span className="text-xs">{fmtDateTime(r.exit)}</span> : <span className="text-xs text-amber-400">still there</span> },
        { key: 'dwell_s', label: 'Stay', align: 'right', render: (r: any) => fmtDuration(r.dwell_s) },
      ]} />
  );
}

/** Time inside each geofence (the answer step's table). */
function FenceTable({ rows }: { rows: any[][] }) {
  return (
    <div className="max-h-[360px] overflow-y-auto">
      <DataTable dense rows={rows.map(r => ({ site: r[0], name: r[1], scale: r[2], visits: r[4], dwell: r[5], first: r[8], last: r[9] }))}
        empty="No geofence was crossed"
        columns={[
          { key: 'name', label: 'Geofence', render: (r: any) => <span className="text-gray-100 text-xs">{r.name}</span> },
          { key: 'scale', label: 'Scale', render: (r: any) => <ScaleBadge scale={r.scale} /> },
          { key: 'visits', label: 'Stays', align: 'right', render: (r: any) => fmtInt(r.visits) },
          { key: 'dwell', label: 'Time inside', align: 'right', render: (r: any) => fmtDuration(r.dwell) },
          { key: 'first', label: 'First in', render: (r: any) => <span className="text-xs">{fmtDateTime(r.first)}</span> },
        ]} />
    </div>
  );
}

/** A step's own rows, as a small table. */
function StepRows({ doc }: { doc: StepDoc }) {
  const cols = (doc.columns || []).slice(0, 6);
  return (
    <DataTable dense rows={(doc.rows || []).slice(0, 50).map((r: any[]) => Object.fromEntries(cols.map((c, i) => [c, r[i]])))}
      columns={cols.map(c => ({ key: c, label: c, render: (r: any) => <span className="text-xs">{String(r[c] ?? '—')}</span> }))} />
  );
}
