import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Factory, Building2, ChevronRight } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import Spinner from '../components/ui/Spinner';
import ChartCard from '../components/ui/ChartCard';
import PartnerProfile from '../components/partners/PartnerProfile';
import { useApi } from '../hooks/useApi';
import { getConsignorDetail } from '../services/partners';
import { formatDate, formatPercent, formatDistance } from '../lib/formatters';

const otdClass = (v: number | null) =>
  v == null ? 'text-gray-500' : v >= 95 ? 'text-emerald-400' : v >= 80 ? 'text-amber-400' : 'text-red-400';

export default function ConsignorDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data, loading, error } = useApi(() => getConsignorDetail(id!), [id]);

  return (
    <PageContainer title="">
      <button onClick={() => navigate('/consignors')} className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm">
        <ArrowLeft className="w-4 h-4" /> Back to Consignors
      </button>

      {loading ? <Spinner /> : error || !data ? (
        <p className="text-gray-500">{error || 'Consignor not found'}</p>
      ) : (
        <>
          <div className="flex items-center gap-4 mb-6">
            <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-amber-500 to-orange-600 flex items-center justify-center">
              <Factory className="w-7 h-7 text-white" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500">Consignor #{data.id}</p>
              <h1 className="text-2xl font-bold text-white leading-tight">{data.name}</h1>
              <p className="text-xs text-gray-500 mt-0.5">
                {data.kpis.first_trip ? `${formatDate(data.kpis.first_trip)} → ${formatDate(data.kpis.last_trip)}` : 'No trips'}
              </p>
            </div>
          </div>

          <PartnerProfile detail={data} partner={{ kind: 'consignor', id: data.id }} />

          {/* Consignor-only: who they ship to */}
          <ChartCard title="Top Consignees"
        method={{
          formula: "This consignor's trips grouped by receiving customer.",
          plot: "table",
        }} icon={Building2} className="mt-6" explain="Click a consignee to open their profile.">
            {data.top_consignees.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-800">
                      {['Consignee', 'Trips', 'OTD', 'Distance'].map(h => (
                        <th key={h} className="px-3 py-2 text-left text-xs text-gray-500 uppercase tracking-wide">{h}</th>
                      ))}
                      <th className="w-6" />
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_consignees.map((c, i) => (
                      <tr key={i} onClick={() => navigate(`/consignees/${encodeURIComponent(c.consignee)}`)}
                        className="border-b border-gray-800/50 cursor-pointer hover:bg-gray-800/50 transition-colors group">
                        <td className="px-3 py-2"><span className="flex items-center gap-2 text-gray-200">
                          <Building2 className="w-4 h-4 text-blue-400 shrink-0" />{c.consignee}</span></td>
                        <td className="px-3 py-2 text-gray-300">{c.trips}</td>
                        <td className="px-3 py-2"><span className={otdClass(c.otd_pct)}>{formatPercent(c.otd_pct)}</span></td>
                        <td className="px-3 py-2 text-gray-300">{formatDistance(c.total_km)}</td>
                        <td className="px-1 text-gray-600"><ChevronRight className="w-3.5 h-3.5 opacity-0 group-hover:opacity-100" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="text-sm text-gray-500 py-6 text-center">No consignees</p>}
          </ChartCard>
        </>
      )}
    </PageContainer>
  );
}
