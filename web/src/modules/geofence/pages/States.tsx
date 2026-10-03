import { useEffect, useMemo, useState } from 'react';
import { Anchor, Hexagon, Landmark, Split, Ticket } from 'lucide-react';
import { api } from '../lib/api';
import { useApi } from '../hooks/useApi';
import { Card, DataTable, EntityLink, ErrorBox, KPI, Note, PageHeader, Spinner, useSort } from '../components/ui';
import { KPIGrid } from '../components/drill';
import { BarList } from '../components/charts';
import { fmtDate, fmtInt } from '../lib/format';

export default function States() {
  const data = useApi(() => api.stateSummary(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const { sort, order, onSort } = useSort('active');

  const states: any[] = useMemo(() => {
    const items = (data.data?.states ?? []).filter((s: any) => s.state);
    return [...items].sort((a, b) => {
      const av = a[sort], bv = b[sort];
      const cmp = typeof av === 'string' ? String(av).localeCompare(String(bv)) : (av ?? 0) - (bv ?? 0);
      return order === 'asc' ? cmp : -cmp;
    });
  }, [data.data, sort, order]);

  // Open on the state with the most facility visits: where the fleet works.
  useEffect(() => {
    if (!selected && states.length) {
      setSelected([...states].sort((a, b) => b.facility_visits - a.facility_visits)[0].state);
    }
  }, [states, selected]);

  const tolls = useApi(() => (selected ? api.tolls({ state: selected, page_size: 500 }) : Promise.resolve(null)), [selected]);

  if (data.loading && !data.data) return <Spinner label="Loading states" />;
  if (data.error) return <ErrorBox error={data.error} onRetry={data.reload} />;
  const t = data.data?.totals;
  const tollRows = [...(data.data?.states ?? [])].filter((s: any) => s.toll_plazas).sort((a, b) => b.toll_plazas - a.toll_plazas);

  return (
    <div className="animate-fade-in">
      <PageHeader title="States & toll plazas"
        subtitle="Every geofence placed in its Indian state and district from the official boundaries the maps draw, with the fleet's activity in each state and the National Highway toll plazas there." />

      {t && (
        <KPIGrid className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-5 gap-3 mb-6">
          <KPI label="States with geofences" value={fmtInt(t.states_with_fences)} icon={Landmark} color="blue" hint="of 36 states and UTs" 
          drill={{ dataset: 'fences', params: { preset: 'placed' }, groups: ['state', 'scale'], sort: 'visits', headline: 'groups' }} />
          <KPI label="Geofences placed" value={fmtInt(t.fences)} icon={Hexagon} color="green"
            hint={t.unplaced ? `${fmtInt(t.unplaced)} unplaced` : 'every fence in the master'} 
          drill={{ dataset: 'fences', params: {}, groups: ['state', 'district', 'scale'], sort: 'visits' }} />
          <KPI label="Across a state border" value={fmtInt(t.cross_border)} icon={Split} color="amber" hint="points in more than one state" 
          drill={{ dataset: 'fences', params: { preset: 'cross_border' }, groups: ['state', 'scale'], sort: 'area' }} />
          <KPI label="Offshore" value={fmtInt(t.offshore)} icon={Anchor} color="cyan" hint="nearest state recorded" 
          drill={{ dataset: 'fences', params: { preset: 'offshore' }, groups: ['state', 'type'], sort: 'name' }} />
          <KPI label="NH toll plazas" value={fmtInt(t.toll_plazas)} icon={Ticket} color="purple"
            hint={`${fmtInt(t.toll_states)} states · IHMCL list ${fmtDate(t.toll_list_as_of)}`} 
          drill={{ dataset: 'tolls', params: {}, groups: ['state', 'nh'], sort: 'state', order: 'asc' }} />
        </KPIGrid>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <Card title="By state" icon={Landmark} className="xl:col-span-2"
          subtitle="Click a state for its toll plazas below. Visits and alerts count each real event once, however many consignment trips saw it.">
          <DataTable rows={states} sort={sort} order={order} onSort={onSort} dense
            rowKey={r => r.state} onRowClick={r => setSelected(r.state)}
            columns={[
              { key: 'state', label: 'State', sortable: true, render: r => (
                <span className={r.state === selected ? 'text-blue-300 font-medium' : 'text-gray-100'}>{r.state}</span>
              ) },
              { key: 'active', label: 'Active fences', sortable: true, align: 'right', render: r => (
                <EntityLink to={`/geo/geofences?state=${encodeURIComponent(r.state)}`}>{fmtInt(r.active)}</EntityLink>
              ) },
              { key: 'facility', label: 'Facility', sortable: true, align: 'right', title: 'Fences under 100 km² (micro, site, campus)',
                render: r => fmtInt(r.facility) },
              { key: 'restricted', label: 'Restricted', sortable: true, align: 'right', title: 'Restricted or high-risk zones',
                render: r => r.restricted ? <span className="text-red-400">{fmtInt(r.restricted)}</span> : '0' },
              { key: 'visited', label: 'Visited', sortable: true, align: 'right', title: 'Active fences with at least one visit in the published run',
                render: r => fmtInt(r.visited) },
              { key: 'facility_visits', label: 'Facility visits', sortable: true, align: 'right', title: 'Stays at the innermost facility fence',
                render: r => fmtInt(r.facility_visits) },
              { key: 'vehicles', label: 'Vehicles', sortable: true, align: 'right', render: r => fmtInt(r.vehicles) },
              { key: 'alerts', label: 'Alerts', sortable: true, align: 'right',
                render: r => r.alerts ? <span className="text-red-400">{fmtInt(r.alerts)}</span> : '0' },
              { key: 'toll_plazas', label: 'NH toll plazas', sortable: true, align: 'right', render: r => fmtInt(r.toll_plazas) },
            ]} />
        </Card>

        <Card title="NH toll plazas by state" icon={Ticket} subtitle="National Highway fee plazas as IHMCL lists them. Click a state.">
          <BarList rows={tollRows.map((s: any) => ({
            key: s.state, label: <span className={s.state === selected ? 'text-blue-300' : ''}>{s.state}</span>,
            value: s.toll_plazas, onClick: () => setSelected(s.state),
          }))} />
        </Card>
      </div>

      {selected && (
        <Card title={`NH toll plazas in ${selected} (${fmtInt(tolls.data?.total ?? 0)})`} icon={Ticket}
          subtitle="Name, highway and section exactly as published; state names normalised to the map's."
          actions={<EntityLink to={`/geo/geofences?state=${encodeURIComponent(selected)}`} className="text-xs">Geofences in {selected}</EntityLink>}>
          {tolls.error ? <ErrorBox error={tolls.error} onRetry={tolls.reload} /> : (
            <div className="max-h-[480px] overflow-y-auto">
              <DataTable dense rows={tolls.data?.items ?? []} loading={tolls.loading} rowKey={r => r.i_plaza_id}
                empty={`No National Highway toll plazas listed in ${selected}`}
                columns={[
                  { key: 's_name', label: 'Plaza', render: r => <span className="text-gray-100">{r.s_name}</span> },
                  { key: 's_nh', label: 'NH', render: r => <span className="text-xs">{r.s_nh || '—'}</span> },
                  { key: 's_district', label: 'District', render: r => <span className="text-xs">{r.s_district || '—'}</span> },
                  { key: 's_section', label: 'Section', render: r => <span className="text-xs text-gray-400">{r.s_section || '—'}</span> },
                  { key: 's_netc_code', label: 'NETC code', render: r => <span className="font-mono text-xs text-gray-500">{r.s_netc_code || '—'}</span> },
                ]} />
            </div>
          )}
        </Card>
      )}

      <div className="mt-6 grid grid-cols-1 xl:grid-cols-2 gap-6">
        <Note title="How a geofence's state is decided.">
          From the official state and district boundaries the maps draw, not an online lookup. The state is the one holding
          the fence's centre, or most of its corners when its centre falls outside it (an L-shaped yard). A fence reaching
          into more than one state is counted in the state of its centre and flagged as crossing a border; a fence entirely
          at sea takes the nearest state, with the distance recorded.
        </Note>
        <Note tone="warn" title="What the toll plaza list can and cannot answer.">
          It is the National Highway list (IHMCL, the NHAI company that runs FASTag), so state-highway and state-expressway
          plazas are not in it, and it carries no coordinates. It answers how many NH toll plazas each state has; counting the
          tolls a truck actually passed needs each plaza's location.
        </Note>
      </div>
    </div>
  );
}
