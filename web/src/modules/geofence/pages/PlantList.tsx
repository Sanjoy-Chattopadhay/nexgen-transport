/**
 * Plants & congestion: what the scan found, and every plant the tracked fleet uses.
 *
 * The scan reads the published run's physical ledger as a population over
 * time: how many vehicles each plant and each of its zones (gates,
 * weighbridges, parking, loading points, internal roads) held at every
 * moment, what each usually holds, and when it held more for long enough to
 * matter. Each scene it found is a card that reads as a sentence and opens to
 * the proof of its numbers; each plant opens to who was inside it at any
 * moment.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Factory, Radar, Siren, DoorOpen, ScanSearch } from 'lucide-react';
import KPICard from '../../analytics/components/ui/KPICard';
import { ProofGrid } from '../../../core/proof/ProofPanel';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import {
  Badge, Card, DataTable, Empty, ErrorBox, Note, PageHeader, Pagination, ScaleBadge, SearchInput, Segmented, Select,
  Spinner, Toolbar,
} from '../components/ui';
import { KIND_ICON, KIND_PLURAL, SceneCard, fmtDayName, fmtWhen, kindColor, plantProof } from '../components/plants';
import { fmtInt } from '../lib/format';

const KIND_FILTERS = [
  { value: '', label: 'Everything' }, { value: 'gate', label: 'Gates' }, { value: 'weighbridge', label: 'Weighbridges' },
  { value: 'parking', label: 'Parking' }, { value: 'loading', label: 'Loading' }, { value: 'road', label: 'Roads' },
  { value: 'area', label: 'Areas' }, { value: 'plant', label: 'Whole plant' },
] as const;

export default function PlantList() {
  const navigate = useNavigate();
  const [kind, setKind] = useState<string>('');
  const [severity, setSeverity] = useState<string>('');
  const [day, setDay] = useState('');
  const [sort, setSort] = useState<'score' | 'time'>('score');
  const [page, setPage] = useState(1);
  const [q, setQ] = useState('');

  const plants = useApi(() => api.plants(), []);
  const scenes = useApi(() => api.plantScenes({ kind, severity, from: day, to: day, sort, page, page_size: 8 }),
    [kind, severity, day, sort, page]);

  const rows = useMemo(() => {
    const all = plants.data?.plants ?? [];
    if (!q) return all;
    const needle = q.toLowerCase();
    return all.filter((p: any) => p.name.toLowerCase().includes(needle) || String(p.site_id) === q);
  }, [plants.data, q]);

  if (plants.loading && !plants.data) return <Spinner label="Scanning the plants" />;
  if (plants.error) return <ErrorBox error={plants.error} onRetry={plants.reload} />;
  const t = plants.data.totals;
  const gates = t.scenes_by_kind?.gate ?? 0;
  const reset = (f: () => void) => { f(); setPage(1); };

  return (
    <div className="animate-fade-in">
      <PageHeader title="Plants & congestion"
        subtitle="Which vehicles were inside each plant at any moment, and when a plant, a gate, a weighbridge, parking, a loading point or an internal road held more than it usually does. Counted from the published run's stays; only vehicles on trips with GPS can be seen." />

      <ProofGrid className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <KPICard label="Plants with tracked traffic" value={fmtInt(t.plants)} icon={Factory} color="blue"
          proof={plantProof('plants', {}, t.plants)} />
        <KPICard label="Overload scenes found" value={fmtInt(t.scenes)} icon={Radar} color="amber"
          proof={plantProof('scenes', {}, t.scenes)} />
        <KPICard label="High severity" value={fmtInt(t.scenes_high)} icon={Siren} color="red"
          proof={plantProof('scenes', { severity: 'high' }, t.scenes_high)} />
        <KPICard label="Gate overloads" value={fmtInt(gates)} icon={DoorOpen} color="purple"
          proof={plantProof('scenes', { kind: 'gate' }, gates)} />
      </ProofGrid>

      <Card title="What the scan found" icon={ScanSearch}
        subtitle="Every stretch of 15 minutes or more when a plant or one of its zones held more vehicles than it usually does (the count it stays at or below nine minutes in ten while in use, plus a margin). Overloads of the same place drawn twice in the fence master are one scene. Worst first: vehicles over the line, for how long, and the extra time the trucks that came through spent inside."
        >
        <div className="mb-3">
          <Toolbar>
            <Select label="Day" value={day} onChange={v => reset(() => setDay(v))}
              options={[{ value: '', label: 'Every day' }, ...(scenes.data?.days ?? []).map((d: string) => ({ value: d, label: fmtDayName(d) }))]} />
            <Select label="Severity" value={severity} onChange={v => reset(() => setSeverity(v))}
              options={[{ value: '', label: 'All' }, { value: 'high', label: 'High' }, { value: 'moderate', label: 'Moderate' }]} />
            <Segmented value={sort} onChange={v => reset(() => setSort(v))}
              options={[{ value: 'score', label: 'Worst first' }, { value: 'time', label: 'Newest first' }]} />
          </Toolbar>
        </div>
        <div className="flex flex-wrap gap-1.5 mb-4">
          {KIND_FILTERS.map(k => {
            const Icon = k.value ? KIND_ICON[k.value] : null;
            const n = k.value ? (t.scenes_by_kind?.[k.value] ?? 0) : t.scenes;
            return (
              <button key={k.value} onClick={() => reset(() => setKind(k.value))}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                  kind === k.value ? 'border-blue-500/50 bg-blue-600/15 text-blue-300' : 'border-gray-800 text-gray-400 hover:text-gray-200'}`}>
                {Icon && <Icon className="w-3.5 h-3.5" style={{ color: kindColor(k.value) }} />}
                {k.label} <span className="text-gray-500 tabular">{n}</span>
              </button>
            );
          })}
        </div>
        {scenes.error ? <ErrorBox error={scenes.error} onRetry={scenes.reload} /> : !scenes.data ? <Spinner /> : (
          scenes.data.items.length === 0 ? (
            <Empty>No overloads match. Every plant and zone stayed within its usual level for the filters chosen.</Empty>
          ) : (
            <>
              <div className="space-y-3">
                {scenes.data.items.map((s: any) => <SceneCard key={s.id} scene={s} />)}
              </div>
              <Pagination page={scenes.data.page} pages={scenes.data.pages} total={scenes.data.total} onPage={setPage} />
            </>
          )
        )}
      </Card>

      <Card title="Plants" icon={Factory} className="mt-6"
        subtitle="A plant is a facility fence that sits inside no larger one the fleet visits; the visited fences inside it are its zones, named by kind from their names. Busiest is the most vehicles inside at once over the whole run; usual is what it holds nine minutes in ten while in use."
        actions={<SearchInput value={q} onChange={setQ} placeholder="Plant name or site id" />}>
        {rows.length === 0 ? <Empty>No plant matches.</Empty> : (
          <DataTable rows={rows} rowKey={(r: any) => r.site_id} onRowClick={(r: any) => navigate(`/geo/plants/${r.site_id}`)}
            columns={[
              { key: 'name', label: 'Plant', render: (r: any) => (
                <div className="min-w-0">
                  <p className="text-gray-100">{r.name}</p>
                  <p className="text-xs text-gray-500">site {r.site_id}{r.type ? ` · ${r.type}` : ''}{r.copies.length > 1 ? ` · drawn ${r.copies.length}×` : ''}</p>
                </div>
              ) },
              { key: 'scale', label: 'Scale', render: (r: any) => <ScaleBadge scale={r.scale} /> },
              { key: 'zones', label: 'Zones', render: (r: any) => r.zones ? (
                <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                  {Object.entries(r.zone_kinds).map(([k, n]) => {
                    const Icon = KIND_ICON[k];
                    return <span key={k} className="inline-flex items-center gap-1 text-xs text-gray-400" title={KIND_PLURAL[k]}>
                      {Icon && <Icon className="w-3.5 h-3.5" style={{ color: kindColor(k) }} />}{String(n)}
                    </span>;
                  })}
                </div>
              ) : <span className="text-xs text-gray-600">none</span> },
              { key: 'vehicles', label: 'Vehicles', align: 'right', render: (r: any) => fmtInt(r.vehicles) },
              { key: 'peak', label: 'Busiest', align: 'right', render: (r: any) => (
                <span className="tabular">{r.peak} <span className="text-xs text-gray-500">{fmtWhen(r.peak_at)}</span></span>
              ) },
              { key: 'usual', label: 'Usual', align: 'right', render: (r: any) => (
                <span className="tabular">{r.usual ?? '—'}{r.baseline_thin && <span className="text-xs text-amber-400 ml-1" title="Under 6 hours of busy time">thin</span>}</span>
              ) },
              { key: 'scenes', label: 'Scenes', align: 'right', render: (r: any) => r.scenes ? (
                <span className="inline-flex items-center gap-1.5">
                  {r.scenes_high > 0 && <Badge variant="danger">{r.scenes_high} high</Badge>}
                  <span className="tabular">{r.scenes}</span>
                </span>
              ) : <span className="text-gray-600">0</span> },
              { key: 'last', label: 'Last scene', render: (r: any) => <span className="text-xs text-gray-400">{r.last_scene ? fmtWhen(r.last_scene) : '—'}</span> },
            ]} />
        )}
        {plants.data.unindexed_fences > 0 && (
          <div className="mt-3"><Note tone="warn" title={`${plants.data.unindexed_fences} visited fences are no longer in the active master`}>
            and cannot be placed in a plant; their stays are not counted here.</Note></div>
        )}
      </Card>
    </div>
  );
}
