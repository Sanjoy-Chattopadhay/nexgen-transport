import { useState, useEffect } from 'react';
import { Gauge, BookOpen, HelpCircle, Cpu, Trophy, Medal, Search } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import FeaturePills from '../../components/ml/FeaturePills';
import Badge from '../../components/ui/Badge';
import { getDriverScores, getDriverScore } from '../../services/ml';

const FEATURES = [
  { name: 'ETA Punctuality', description: 'On-time delivery rate', importance: 'high' as const },
  { name: 'Speed Consistency', description: 'How consistent driving speed is', importance: 'high' as const },
  { name: 'Safety Rating', description: 'Based on speed violations and patterns', importance: 'high' as const },
  { name: 'Trip Completion', description: 'Percentage of trips completed successfully', importance: 'medium' as const },
  { name: 'Route Experience', description: 'Variety and count of routes driven', importance: 'medium' as const },
  { name: 'Night Drive Ratio', description: 'Proportion of night-time driving', importance: 'low' as const },
];

const RANK_COLORS = ['text-yellow-400', 'text-gray-300', 'text-amber-600'];

export default function DriverScorer() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [drivers, setDrivers] = useState<any[]>([]);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [, setSelectedDriver] = useState<any>(null);

  useEffect(() => {
    setLoading(true);
    getDriverScores(200)
      .then(res => setDrivers(res.data?.drivers || []))
      .catch(err => setError(err?.response?.data?.detail || 'Failed to load scores'))
      .finally(() => setLoading(false));
  }, []);

  const filtered = drivers.filter(d =>
    d.driver_name?.toLowerCase().includes(search.toLowerCase()) ||
    String(d.driver_id).includes(search)
  );

  const loadDriverDetail = async (id: number) => {
    try {
      const res = await getDriverScore(id);
      setSelectedDriver(res.data);
    } catch { /* ignore */ }
  };

  const scoreColor = (score: number) =>
    score >= 80 ? 'text-emerald-400' : score >= 60 ? 'text-amber-400' : score >= 40 ? 'text-orange-400' : 'text-red-400';

  const riskBadge = (level: string) => {
    const v = level === 'low' ? 'success' : level === 'medium' ? 'warning' : 'danger';
    return <Badge label={level} variant={v as any} />;
  };

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Driver Scorer"
        subtitle="Composite scoring system that evaluates driver performance across punctuality, speed consistency, safety, and trip completion. Used for driver rankings and performance reviews."
        icon={Gauge} iconColor="text-purple-400"
        gradientFrom="from-purple-600/20" gradientTo="to-fuchsia-600/20" accentBorder="border-purple-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How Scoring Works" icon={BookOpen} iconColor="text-purple-400" defaultOpen={true}>
              <p className="mt-2">Each driver gets a <strong className="text-gray-300">composite score (0-100)</strong> based on weighted components. ETA rate is <strong className="text-gray-300">Bayesian-smoothed</strong> — a driver with 4 trips at 100% won't outrank a veteran with 500 trips at 75%.</p>
              <div className="mt-2 space-y-1">
                {[
                  ['ETA (Bayesian)', '35%', 'On-time rate smoothed by experience'],
                  ['Experience', '20%', 'Total trips across fleet (log-scaled)'],
                  ['Consistency', '20%', 'Low variance in trip durations'],
                  ['Speed Safety', '15%', 'Penalizes unsafe or inefficient speeds'],
                  ['Efficiency', '10%', 'Distance per trip vs fleet median'],
                ].map(([name, weight, desc]) => (
                  <div key={name as string} className="flex items-center gap-2">
                    <span className="text-purple-400 font-mono text-xs w-12">{weight}</span>
                    <span className="text-gray-300 text-xs font-medium">{name}</span>
                    <span className="text-gray-500 text-xs">— {desc}</span>
                  </div>
                ))}
              </div>
            </InfoCard>
            <InfoCard title="Risk Levels" icon={HelpCircle} iconColor="text-amber-400">
              <div className="space-y-2 mt-2">
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-emerald-500" /><span><strong className="text-emerald-400">Low</strong> — Score 80+, reliable driver</span></div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-amber-500" /><span><strong className="text-amber-400">Medium</strong> — Score 60-79, needs monitoring</span></div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-orange-500" /><span><strong className="text-orange-400">High</strong> — Score 40-59, performance issues</span></div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-red-500" /><span><strong className="text-red-400">Critical</strong> — Score {'<'}40, requires intervention</span></div>
              </div>
            </InfoCard>
          </div>

          {/* Leaderboard */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden animate-fade-in-up stagger-2">
            <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
              <h2 className="text-base font-semibold text-white flex items-center gap-2">
                <Trophy className="w-4 h-4 text-yellow-400" /> Driver Leaderboard
              </h2>
              <div className="relative w-64">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input type="text" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search driver..."
                  className="w-full pl-9 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-purple-500/50 transition-all" />
              </div>
            </div>
            {loading ? (
              <div className="p-8 text-center"><div className="w-6 h-6 border-2 border-purple-400/30 border-t-purple-400 rounded-full animate-spin mx-auto" /></div>
            ) : error ? (
              <div className="p-8 text-center text-red-400 text-sm">{error}</div>
            ) : (
              <div className="divide-y divide-gray-800/50 max-h-[500px] overflow-y-auto">
                {filtered.map((d, i) => (
                  <div key={d.driver_id}
                    onClick={() => { loadDriverDetail(d.driver_id); navigate(`/drivers/${d.driver_id}`); }}
                    className={`flex items-center px-5 py-3 hover:bg-gray-800/50 cursor-pointer transition-colors animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                    <div className="w-8 text-center">
                      {i < 3 ? <Medal className={`w-5 h-5 ${RANK_COLORS[i]} mx-auto`} /> : <span className="text-xs text-gray-600">{i + 1}</span>}
                    </div>
                    <div className="flex-1 ml-3">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-white">{d.driver_name}</span>
                        {riskBadge(d.risk_level || 'medium')}
                      </div>
                      <span className="text-xs text-gray-500">ID: {d.driver_id}</span>
                    </div>
                    <div className="text-right">
                      <p className={`text-lg font-bold ${scoreColor(d.composite_score)}`}>{d.composite_score?.toFixed(1)}</p>
                      <p className="text-xs text-gray-600 uppercase">Score</p>
                    </div>
                    {/* Mini score bars */}
                    {d.scores && (
                      <div className="hidden md:flex flex-col gap-1 ml-4 w-32">
                        {Object.entries(d.scores).slice(0, 3).map(([k, v]) => (
                          <div key={k} className="flex items-center gap-1">
                            <span className="text-xs text-gray-600 w-16 truncate capitalize">{k.replace(/_/g, ' ')}</span>
                            <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                              <div className="h-full bg-purple-500/60 rounded-full animate-progress-fill" style={{ width: `${(v as number) * 100}%` }} />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {filtered.length === 0 && <div className="p-8 text-center text-gray-500 text-sm">No drivers found</div>}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-5">
          <div className="animate-fade-in-up stagger-3 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <FeaturePills features={FEATURES} />
          </div>
          <div className="animate-fade-in-up stagger-4 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2"><Cpu className="w-4 h-4 text-purple-400" /> Developer Info</h3>
            <div className="space-y-2 text-xs text-gray-500">
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">Weighted Composite</span></div>
              <div className="flex justify-between"><span>Output</span><span className="text-gray-300">Score 0-100 + Risk Level</span></div>
              <div className="flex justify-between"><span>Refresh</span><span className="text-gray-300">Daily (auto-tier)</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">GET /ml/drivers/scores</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
