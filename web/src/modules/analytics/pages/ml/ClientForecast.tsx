import { useState, useEffect } from 'react';
import { Building2, BookOpen, HelpCircle, Zap, Cpu, Search, ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import FeaturePills from '../../components/ml/FeaturePills';
import { getClients, getClientProfile } from '../../services/ml';

const FEATURES = [
  { name: 'Historical Volume', description: 'Weekly/monthly trip history per client', importance: 'high' as const },
  { name: 'Seasonality', description: 'Day-of-week and monthly patterns', importance: 'high' as const },
  { name: 'Growth Trend', description: '30-day growth rate', importance: 'medium' as const },
  { name: 'Route Mix', description: 'Distribution across routes', importance: 'medium' as const },
  { name: 'Active Weeks', description: 'How consistently the client ships', importance: 'low' as const },
];

const TREND_ICONS: Record<string, { icon: any; color: string }> = {
  increasing: { icon: ArrowUpRight, color: 'text-emerald-400' },
  growing: { icon: ArrowUpRight, color: 'text-emerald-400' },
  stable: { icon: Minus, color: 'text-gray-400' },
  declining: { icon: ArrowDownRight, color: 'text-red-400' },
  decreasing: { icon: ArrowDownRight, color: 'text-red-400' },
};

export default function ClientForecast() {
  const [loading, setLoading] = useState(true);
  const [clients, setClients] = useState<any[]>([]);
  const [search, setSearch] = useState('');
  const [selectedClient, setSelectedClient] = useState<any>(null);
  const [profileLoading, setProfileLoading] = useState(false);

  useEffect(() => {
    getClients()
      .then(res => setClients(res.data?.clients || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const loadProfile = async (name: string) => {
    setProfileLoading(true);
    try {
      const res = await getClientProfile(name);
      setSelectedClient(res.data);
    } catch { setSelectedClient(null); }
    setProfileLoading(false);
  };

  const filtered = clients.filter(c =>
    c.client?.toLowerCase().includes(search.toLowerCase()) ||
    c.client_name?.toLowerCase().includes(search.toLowerCase())
  );

  const profile = selectedClient?.profile;
  const forecast = selectedClient?.forecast;

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Client Demand Forecast"
        subtitle="Per-client demand trends and 7-day forecasts. Understand seasonal patterns, growth trajectories, and shipping volume for key accounts."
        icon={Building2} iconColor="text-teal-400"
        gradientFrom="from-teal-600/20" gradientTo="to-emerald-600/20" accentBorder="border-teal-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-teal-400" defaultOpen={true}>
              <p className="mt-2">The client forecaster analyzes per-customer shipping patterns:</p>
              <ul className="list-disc list-inside space-y-1 mt-2">
                <li>Builds <strong className="text-gray-300">demand profile</strong> per client from historical trips</li>
                <li>Detects <strong className="text-gray-300">weekly seasonality</strong> (which days of the week they ship)</li>
                <li>Tracks <strong className="text-gray-300">growth trends</strong> (30-day growth rate)</li>
                <li>Generates <strong className="text-gray-300">7-day forecast</strong> with daily breakdowns</li>
              </ul>
            </InfoCard>
            <InfoCard title="Use Cases" icon={HelpCircle} iconColor="text-emerald-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Account Management</strong> — Track client shipping trends</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Capacity Planning</strong> — Allocate vehicles for key clients</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Growth Detection</strong> — Identify growing or declining accounts</span></li>
              </ul>
            </InfoCard>
          </div>

          {/* Client List */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden animate-fade-in-up stagger-2">
            <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
              <h2 className="text-base font-semibold text-white flex items-center gap-2">
                <Building2 className="w-4 h-4 text-teal-400" /> Clients
              </h2>
              <div className="relative w-64">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input type="text" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search client..."
                  className="w-full pl-9 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-teal-500/50 transition-all" />
              </div>
            </div>
            {loading ? (
              <div className="p-8 text-center"><div className="w-6 h-6 border-2 border-teal-400/30 border-t-teal-400 rounded-full animate-spin mx-auto" /></div>
            ) : (
              <div className="divide-y divide-gray-800/50 max-h-[350px] overflow-y-auto">
                {filtered.slice(0, 50).map((c, i) => {
                  const name = c.client || c.client_name;
                  const trend = TREND_ICONS[c.trend] || TREND_ICONS.stable;
                  const TIcon = trend.icon;
                  const isSelected = selectedClient?.client === name;
                  return (
                    <button key={name} onClick={() => loadProfile(name)}
                      className={`w-full text-left flex items-center px-5 py-3 hover:bg-gray-800/50 transition-colors animate-fade-in stagger-${Math.min(i + 1, 9)} ${isSelected ? 'bg-teal-500/10 border-l-2 border-teal-500' : ''}`}>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-white truncate">{name}</p>
                        <p className="text-xs text-gray-500">{c.total_trips} trips | {c.active_weeks} active weeks</p>
                      </div>
                      <div className="flex items-center gap-2 ml-2">
                        <TIcon className={`w-3.5 h-3.5 ${trend.color}`} />
                        <span className="text-xs text-gray-400">{c.avg_trips_per_week?.toFixed(1)}/wk</span>
                      </div>
                    </button>
                  );
                })}
                {filtered.length === 0 && <div className="p-8 text-center text-gray-500 text-sm">No clients found</div>}
              </div>
            )}
          </div>

          {/* Client Profile */}
          {profileLoading && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-8 text-center">
              <div className="w-6 h-6 border-2 border-teal-400/30 border-t-teal-400 rounded-full animate-spin mx-auto" />
            </div>
          )}

          {selectedClient && !profileLoading && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-scale-in">
              <h2 className="text-base font-semibold text-white mb-4">{selectedClient.client}</h2>

              {profile && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                  <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-teal-400">{profile.total_trips}</p>
                    <p className="text-xs text-gray-500">Total Trips</p>
                  </div>
                  <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-blue-400">{profile.avg_trips_per_week?.toFixed(1)}</p>
                    <p className="text-xs text-gray-500">Trips/Week</p>
                  </div>
                  <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-purple-400">{profile.active_weeks}</p>
                    <p className="text-xs text-gray-500">Active Weeks</p>
                  </div>
                  <div className="bg-gray-800/50 rounded-lg p-3 text-center">
                    <p className="text-xl font-bold text-amber-400">{profile.top_routes?.length || 0}</p>
                    <p className="text-xs text-gray-500">Routes Used</p>
                  </div>
                </div>
              )}

              {/* Top Routes */}
              {profile?.top_routes && profile.top_routes.length > 0 && (
                <div className="mb-4">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Top Routes</p>
                  <div className="space-y-1">
                    {profile.top_routes.slice(0, 5).map((r: any, i: number) => (
                      <div key={i} className="flex items-center justify-between bg-gray-800/30 rounded-lg px-3 py-2 text-xs">
                        <span className="text-gray-300">{r.origin} → {r.destination}</span>
                        <span className="text-gray-400">{r.trips} trips</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 7-Day Forecast */}
              {forecast && forecast.next_7_days && (
                <div>
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">7-Day Forecast</p>
                  <div className="grid grid-cols-7 gap-1">
                    {forecast.next_7_days.map((d: any, i: number) => (
                      <div key={i} className="bg-gray-800/50 rounded-lg p-2 text-center">
                        <p className="text-xs text-gray-500">{d.day_of_week?.slice(0, 3)}</p>
                        <p className="text-lg font-bold text-teal-400">{d.predicted_trips}</p>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-between mt-3 text-xs text-gray-500">
                    <span>Total: <span className="text-white font-medium">{forecast.total_predicted_week} trips</span></span>
                    <span>Trend: <span className={`font-medium capitalize ${(TREND_ICONS[forecast.trend] || TREND_ICONS.stable).color}`}>{forecast.trend}</span></span>
                    <span>Growth: <span className={`font-medium ${(forecast.growth_pct_30d || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{forecast.growth_pct_30d?.toFixed(1)}%</span></span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="space-y-5">
          <div className="animate-fade-in-up stagger-3 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <FeaturePills features={FEATURES} />
          </div>
          <div className="animate-fade-in-up stagger-4 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2"><Cpu className="w-4 h-4 text-purple-400" /> Developer Info</h3>
            <div className="space-y-2 text-xs text-gray-500">
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">Seasonal Decomposition</span></div>
              <div className="flex justify-between"><span>Horizon</span><span className="text-gray-300">7 days ahead</span></div>
              <div className="flex justify-between"><span>Per-Client</span><span className="text-gray-300">Individual forecasts</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">GET /ml/clients/{'{name}'}/profile</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
