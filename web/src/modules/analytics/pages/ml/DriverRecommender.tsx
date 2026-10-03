import { useState } from 'react';
import { Users, BookOpen, HelpCircle, Cpu, Star, Award, Route } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import LocationSelect from '../../components/ml/LocationSelect';
import RouteStatsPreview from '../../components/ml/RouteStatsPreview';
import FeaturePills from '../../components/ml/FeaturePills';
import ResultCard from '../../components/ml/ResultCard';
import Badge from '../../components/ui/Badge';
import { recommendDrivers } from '../../services/ml';
import { formatDuration, formatSpeed, formatPercent } from '../../lib/formatters';

const FEATURES = [
  { name: 'Route Experience', description: 'Trips completed on this exact route (log-scaled)', importance: 'high' as const },
  { name: 'ETA Success (Bayesian)', description: 'On-time rate with Bayesian smoothing for low-trip drivers', importance: 'high' as const },
  { name: 'Overall Experience', description: 'Total trips across all routes', importance: 'medium' as const },
  { name: 'Average Speed', description: 'Driver speed on this route', importance: 'medium' as const },
  { name: 'Consistency', description: 'Standard deviation of trip durations', importance: 'medium' as const },
  { name: 'Average Duration', description: 'Mean trip time on this route', importance: 'medium' as const },
];

export default function DriverRecommender() {
  const navigate = useNavigate();
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [topN, setTopN] = useState('10');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  const canRecommend = origin && destination;

  const handleRecommend = async () => {
    if (!canRecommend) return;
    setLoading(true); setResult(null); setError('');
    try {
      const res = await recommendDrivers({ origin, destination, top_n: Number(topN) || 10 });
      setResult(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Recommendation failed');
    }
    setLoading(false);
  };

  const experienced = result?.experienced_on_route || [];
  const similar = result?.similar_route_experience || [];

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Driver Recommender"
        subtitle="Find the best driver for any route using Bayesian-weighted scoring. Ranks drivers by route experience, ETA success, speed consistency, and overall reliability."
        icon={Users} iconColor="text-cyan-400"
        gradientFrom="from-cyan-600/20" gradientTo="to-sky-600/20" accentBorder="border-cyan-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How Recommendations Work" icon={BookOpen} iconColor="text-cyan-400" defaultOpen={true}>
              <p className="mt-2">The recommender uses a <strong className="text-gray-300">multi-factor scoring system</strong>:</p>
              <div className="mt-2 space-y-1.5">
                <p className="text-gray-300 text-xs font-medium">Scoring Weights:</p>
                {[
                  ['Route Experience', '25%', 'Log-scaled trips on this exact route'],
                  ['ETA Success', '30%', 'Bayesian-smoothed on-time rate'],
                  ['Average Speed', '15%', 'Speed efficiency on this route'],
                  ['Overall Experience', '20%', 'Total trips across fleet'],
                  ['Consistency', '10%', 'Low duration variance'],
                ].map(([name, weight, desc]) => (
                  <div key={name as string} className="flex items-center gap-2">
                    <span className="text-cyan-400 font-mono text-xs w-12">{weight}</span>
                    <span className="text-gray-300 text-xs">{name}</span>
                    <span className="text-gray-500 text-xs">— {desc}</span>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-xs text-gray-500">Minimum 2 trips required. 1-trip drivers get neutral scores via Bayesian smoothing.</p>
            </InfoCard>
            <InfoCard title="Two-Tier Results" icon={HelpCircle} iconColor="text-emerald-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Star className="w-3.5 h-3.5 text-yellow-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Experienced on Route</strong> — Drivers who have driven this exact route before. Most reliable recommendations.</span></li>
                <li className="flex gap-2"><Route className="w-3.5 h-3.5 text-blue-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Similar Route Experience</strong> — Drivers with experience on similar-distance routes. Good alternatives when few route-specific drivers exist.</span></li>
              </ul>
            </InfoCard>
          </div>

          {/* Form */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Users className="w-4 h-4 text-cyan-400" /> Find Best Drivers
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <LocationSelect label="Origin *" value={origin} onChange={setOrigin} />
              <LocationSelect label="Destination *" value={destination} onChange={setDestination} />
            </div>
            <div className="mb-4"><RouteStatsPreview origin={origin} destination={destination} /></div>
            <div className="flex items-end gap-4 mb-0">
              <div className="w-32">
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Top N</label>
                <input type="number" value={topN} onChange={e => setTopN(e.target.value)} min={1} max={50}
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-cyan-500/50 transition-all" />
              </div>
              <button onClick={handleRecommend} disabled={!canRecommend || loading}
                className={`flex-1 py-3 rounded-xl font-semibold text-sm transition-all flex items-center justify-center gap-2 ${
                  canRecommend ? 'bg-cyan-600 hover:bg-cyan-500 text-white shadow-lg shadow-cyan-600/20' : 'bg-gray-800 text-gray-500 cursor-not-allowed'
                }`}>
                {loading ? <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Finding...</>
                  : <><Award className="w-4 h-4" /> Recommend Drivers</>}
              </button>
            </div>
          </div>

          {error && <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-4 animate-scale-in"><p className="text-sm text-red-400">{error}</p></div>}

          {/* Results */}
          {result && (
            <>
              {/* Experienced on Route */}
              {experienced.length > 0 && (
                <ResultCard title={`Experienced on Route (${experienced.length})`} gradient="from-cyan-900/30 to-sky-900/30" border="border-cyan-700/30">
                  <div className="space-y-3">
                    {experienced.map((d: any, i: number) => (
                      <div key={d.driver_id} onClick={() => navigate(`/drivers/${d.driver_id}`)}
                        className={`bg-gray-900/60 rounded-lg p-4 border border-gray-800 hover:border-cyan-700/40 cursor-pointer transition-all animate-fade-in-up stagger-${Math.min(i + 1, 9)}`}>
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold ${i < 3 ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30' : 'bg-gray-800 text-gray-400'}`}>
                              {i + 1}
                            </div>
                            <div>
                              <p className="text-sm font-medium text-white">{d.driver_name}</p>
                              <p className="text-xs text-gray-500">{d.driver_mobile || `ID: ${d.driver_id}`}</p>
                            </div>
                          </div>
                          {d.composite_score != null && (
                            <div className="text-right">
                              <p className="text-lg font-bold text-cyan-400">{d.composite_score.toFixed(1)}</p>
                              <p className="text-xs text-gray-600">SCORE</p>
                            </div>
                          )}
                        </div>
                        <div className="grid grid-cols-4 gap-2 text-xs">
                          <div><span className="text-gray-500 block">Route Trips</span><span className="text-gray-300 font-medium">{d.route_trips}</span></div>
                          <div><span className="text-gray-500 block">Avg Duration</span><span className="text-gray-300 font-medium">{formatDuration(d.avg_duration_min)}</span></div>
                          <div><span className="text-gray-500 block">Avg Speed</span><span className="text-gray-300 font-medium">{formatSpeed(d.avg_speed_kmph)}</span></div>
                          <div><span className="text-gray-500 block">ETA Rate</span>
                            <Badge label={formatPercent(d.eta_success_rate)} variant={d.eta_success_rate >= 60 ? 'success' : d.eta_success_rate >= 40 ? 'warning' : 'danger'} />
                          </div>
                        </div>
                        {d.consistency && <p className="text-xs text-gray-600 mt-1">Consistency: {d.consistency}</p>}
                      </div>
                    ))}
                  </div>
                </ResultCard>
              )}

              {/* Similar Route */}
              {similar.length > 0 && (
                <ResultCard title={`Similar Route Experience (${similar.length})`} gradient="from-indigo-900/30 to-violet-900/30" border="border-indigo-700/30">
                  <div className="space-y-2">
                    {similar.map((d: any, i: number) => (
                      <div key={d.driver_id} onClick={() => navigate(`/drivers/${d.driver_id}`)}
                        className={`flex items-center justify-between bg-gray-900/60 rounded-lg px-4 py-3 border border-gray-800 hover:border-indigo-700/40 cursor-pointer transition-all animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-gray-500 w-6">{i + 1}</span>
                          <div>
                            <p className="text-sm text-white">{d.driver_name}</p>
                            <p className="text-xs text-gray-500">{d.reason || `${d.route_trips} similar trips`}</p>
                          </div>
                        </div>
                        {d.composite_score != null && <span className="text-sm font-bold text-indigo-400">{d.composite_score.toFixed(1)}</span>}
                      </div>
                    ))}
                  </div>
                </ResultCard>
              )}

              {experienced.length === 0 && similar.length === 0 && (
                <div className="bg-gray-900 rounded-xl border border-gray-800 p-8 text-center animate-scale-in">
                  <Users className="w-12 h-12 text-gray-600 mx-auto mb-2" />
                  <p className="text-gray-400">No experienced drivers found for this route.</p>
                  <p className="text-xs text-gray-600 mt-1">This may be a new or rarely-used route.</p>
                </div>
              )}
            </>
          )}
        </div>

        <div className="space-y-5">
          <div className="animate-fade-in-up stagger-3 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <FeaturePills features={FEATURES} />
          </div>
          <div className="animate-fade-in-up stagger-4 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2"><Cpu className="w-4 h-4 text-purple-400" /> Developer Info</h3>
            <div className="space-y-2 text-xs text-gray-500">
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">Bayesian Composite Scoring</span></div>
              <div className="flex justify-between"><span>Min Trips</span><span className="text-gray-300">&ge; 2 (Bayesian smoothed)</span></div>
              <div className="flex justify-between"><span>Experience Scale</span><span className="text-gray-300">Logarithmic (log1p)</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">POST /ml/recommend/drivers</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
