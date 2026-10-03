import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Building2 } from 'lucide-react';
import PageContainer from '../components/layout/PageContainer';
import Spinner from '../components/ui/Spinner';
import PartnerProfile from '../components/partners/PartnerProfile';
import { useApi } from '../hooks/useApi';
import { getConsignee } from '../services/partners';
import { formatDate } from '../lib/formatters';

export default function ConsigneeDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const decoded = decodeURIComponent(name || '');
  const { data, loading, error } = useApi(() => getConsignee(decoded), [decoded]);

  return (
    <PageContainer title="">
      <button onClick={() => navigate('/consignees')} className="flex items-center gap-2 text-gray-400 hover:text-white mb-4 text-sm">
        <ArrowLeft className="w-4 h-4" /> Back to Consignees
      </button>

      {loading ? <Spinner /> : error || !data ? (
        <p className="text-gray-500">{error || 'Consignee not found'}</p>
      ) : (
        <>
          <div className="flex items-center gap-4 mb-6">
            <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-blue-600 to-purple-600 flex items-center justify-center">
              <Building2 className="w-7 h-7 text-white" />
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500">Consignee</p>
              <h1 className="text-2xl font-bold text-white leading-tight">{data.consignee}</h1>
              <p className="text-xs text-gray-500 mt-0.5">
                {data.kpis.first_trip ? `${formatDate(data.kpis.first_trip)} → ${formatDate(data.kpis.last_trip)}` : 'No trips'}
              </p>
            </div>
          </div>
          <PartnerProfile detail={data} />
        </>
      )}
    </PageContainer>
  );
}
