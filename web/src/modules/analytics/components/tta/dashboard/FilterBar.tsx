import { RotateCcw } from 'lucide-react';
import MultiSelect from '../../ui/MultiSelect';
import { useTTAFilters } from './FilterContext';

/** Global analytics filter bar — affects every KPI, chart, table and map. */
export default function FilterBar() {
  const { filters, setFilters, reset, meta } = useTTAFilters();

  const active =
    filters.transporters.length + filters.destinations.length +
    filters.vehicleCategories.length + filters.ownMarket.length +
    filters.consignors.length + (filters.dateFrom ? 1 : 0) + (filters.dateTo ? 1 : 0);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 mb-6 relative z-[1100]">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5">
          <input type="date" value={filters.dateFrom} min={meta?.date_min ?? undefined} max={meta?.date_max ?? undefined}
            onChange={e => setFilters({ ...filters, dateFrom: e.target.value })}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-300 outline-none focus:border-blue-600 [color-scheme:dark]" />
          <span className="text-gray-600 text-xs">→</span>
          <input type="date" value={filters.dateTo} min={meta?.date_min ?? undefined} max={meta?.date_max ?? undefined}
            onChange={e => setFilters({ ...filters, dateTo: e.target.value })}
            className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-300 outline-none focus:border-blue-600 [color-scheme:dark]" />
        </div>
        <MultiSelect label="Transporter" options={meta?.transporters ?? []}
          selected={filters.transporters}
          onChange={v => setFilters({ ...filters, transporters: v })} />
        <MultiSelect label="Destination" options={meta?.destinations ?? []}
          selected={filters.destinations}
          onChange={v => setFilters({ ...filters, destinations: v })} />
        <MultiSelect label="Vehicle" options={meta?.vehicle_categories ?? []}
          selected={filters.vehicleCategories}
          onChange={v => setFilters({ ...filters, vehicleCategories: v })} />
        <MultiSelect label="Own/Market" options={meta?.own_market ?? []}
          selected={filters.ownMarket}
          onChange={v => setFilters({ ...filters, ownMarket: v })} />
        <MultiSelect label="Consignor" options={meta?.consignors ?? []}
          selected={filters.consignors}
          onChange={v => setFilters({ ...filters, consignors: v })} />
        {active > 0 && (
          <button onClick={reset}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-gray-400 hover:text-red-400 border border-gray-700 bg-gray-800">
            <RotateCcw className="w-3 h-3" /> Reset
          </button>
        )}
        <span className="ml-auto text-xs text-gray-500">
          {meta ? `${meta.rows.toLocaleString('en-IN')} trips · ${meta.date_min ?? '—'} → ${meta.date_max ?? '—'}` : 'loading…'}
        </span>
      </div>
    </div>
  );
}
