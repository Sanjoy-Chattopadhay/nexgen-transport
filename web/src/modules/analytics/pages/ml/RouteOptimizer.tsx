import { useState } from 'react';
import { Route, BookOpen, HelpCircle, Zap, Cpu, MapPin, Timer, Gauge, Star } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import LocationSelect from '../../components/ml/LocationSelect';
import RouteStatsPreview from '../../components/ml/RouteStatsPreview';
import FeaturePills from '../../components/ml/FeaturePills';
import ResultCard from '../../components/ml/ResultCard';
import { optimizeRoute, getHubLocations } from '../../services/ml';
import { formatDuration, formatDistance } from '../../lib/formatters';
import { useEffect } from 'react';

const FEATURES = [
  { name: 'Network Distance', description: 'Shortest weighted path in route graph', importance: 'high' as const },
  { name: 'Historical Speed', description: 'Average speed on this corridor', importance: 'high' as const },
  { name: 'Traffic Pattern', description: 'Time-of-day congestion factor', importance: 'medium' as const },
  { name: 'Hub Proximity', description: 'Closeness to primary hub locations', importance: 'medium' as const },
  { name: 'Route Frequency', description: 'How often this route is used', importance: 'low' as const },
];

export default function RouteOptimizer() {
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [hour, setHour] = useState('');
  const [tripKm, setTripKm] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const [hubs, setHubs] = useState<any>(null);

  useEffect(() => {
    getHubLocations().then(r => setHubs(r.data)).catch(() => {});
  }, []);

  const canOptimize = origin && destination;

  const handleOptimize = async () => {
    if (!canOptimize) return;
    setLoading(true); setResult(null); setError('');
    try {
      const payload: any = { origin, destination };
      if (hour) payload.hour = Number(hour);
      if (tripKm) payload.trip_km = Number(tripKm);
      const res = await optimizeRoute(payload);
      setResult(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Optimization failed');
    }
    setLoading(false);
  };

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Route Optimizer"
        subtitle="Network analysis engine that evaluates routes using graph-based algorithms. Identifies optimal paths, hub locations, and efficiency scores for origin-destination pairs."
        icon={Route} iconColor="text-amber-400"
        gradientFrom="from-amber-600/20" gradientTo="to-yellow-600/20" accentBorder="border-amber-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-amber-400" defaultOpen={true}>
              <p className="mt-2">The route optimizer builds a <strong className="text-gray-300">weighted graph</strong> from historical trip data:</p>
              <ul className="list-disc list-inside space-y-1 mt-2">
                <li>Nodes = locations, edges = routes with average duration as weight</li>
                <li>Finds optimal path using Dijkstra's algorithm</li>
                <li>Calculates <strong className="text-gray-300">efficiency score</strong> comparing direct vs historical performance</li>
                <li>Identifies <strong className="text-gray-300">hub locations</strong> using network centrality analysis</li>
              </ul>
            </InfoCard>
            <InfoCard title="Outputs" icon={HelpCircle} iconColor="text-emerald-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Estimated distance & time</strong> — Based on historical average</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Efficiency score</strong> — 0-1 rating for this route</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Traffic conditions</strong> — Based on time-of-day patterns</span></li>
              </ul>
            </InfoCard>
          </div>

          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Route className="w-4 h-4 text-amber-400" /> Optimize Route
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <LocationSelect label="Origin *" value={origin} onChange={setOrigin} />
              <LocationSelect label="Destination *" value={destination} onChange={setDestination} />
            </div>
            <div className="mb-4"><RouteStatsPreview origin={origin} destination={destination} /></div>
            <div className="grid grid-cols-2 gap-4 mb-5">
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Hour of Day <span className="text-gray-600">(0-23)</span></label>
                <input type="number" min={0} max={23} value={hour} onChange={e => setHour(e.target.value)} placeholder="any"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-amber-500/50 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Trip KM <span className="text-gray-600">(optional)</span></label>
                <input type="number" value={tripKm} onChange={e => setTripKm(e.target.value)} placeholder="auto"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-amber-500/50 transition-all" />
              </div>
            </div>
            <button onClick={handleOptimize} disabled={!canOptimize || loading}
              className={`w-full py-3 rounded-xl font-semibold text-sm transition-all flex items-center justify-center gap-2 ${
                canOptimize ? 'bg-amber-600 hover:bg-amber-500 text-white shadow-lg shadow-amber-600/20' : 'bg-gray-800 text-gray-500 cursor-not-allowed'
              }`}>
              {loading ? <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Optimizing...</>
                : <><Route className="w-4 h-4" /> Optimize</>}
            </button>
          </div>

          {error && <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-4 animate-scale-in"><p className="text-sm text-red-400">{error}</p></div>}

          {result && (
            <ResultCard title="Optimization Result" gradient="from-amber-900/30 to-yellow-900/30" border="border-amber-700/30">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <MapPin className="w-5 h-5 text-amber-400 mx-auto mb-1" />
                  <p className="text-lg font-bold text-white animate-count-up">{result.recommended_distance_km ? formatDistance(result.recommended_distance_km) : '-'}</p>
                  <p className="text-xs text-gray-500">Distance</p>
                </div>
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <Timer className="w-5 h-5 text-purple-400 mx-auto mb-1" />
                  <p className="text-lg font-bold text-white animate-count-up">{result.time_to_travel_minutes ? formatDuration(result.time_to_travel_minutes) : '-'}</p>
                  <p className="text-xs text-gray-500">Est. Time</p>
                </div>
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <Star className="w-5 h-5 text-yellow-400 mx-auto mb-1" />
                  <p className={`text-lg font-bold animate-count-up ${(result.efficiency_score || 0) >= 0.7 ? 'text-emerald-400' : 'text-amber-400'}`}>
                    {result.efficiency_score != null ? `${(result.efficiency_score * 100).toFixed(0)}%` : '-'}
                  </p>
                  <p className="text-xs text-gray-500">Efficiency</p>
                </div>
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <Gauge className="w-5 h-5 text-cyan-400 mx-auto mb-1" />
                  <p className="text-sm font-bold text-white capitalize animate-count-up">{result.conditions || '-'}</p>
                  <p className="text-xs text-gray-500">Conditions</p>
                </div>
              </div>
              {result.notes && (
                <div className="bg-gray-900/60 rounded-lg p-3 border border-gray-800 text-sm text-gray-400">{result.notes}</div>
              )}
            </ResultCard>
          )}

          {/* Hub Locations */}
          {hubs && hubs.hub_analysis && hubs.hub_analysis.length > 0 && (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-4">
              <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
                <MapPin className="w-4 h-4 text-amber-400" /> Network Hub Locations
              </h2>
              <p className="text-xs text-gray-500 mb-3">Key locations with highest connectivity in the route network.</p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {hubs.hub_analysis.slice(0, 12).map((h: any, i: number) => (
                  <div key={i} className={`bg-gray-800/50 rounded-lg px-3 py-2 border border-gray-700/50 animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                    <p className="text-sm text-white font-medium truncate">{h.hub_name}</p>
                    <div className="flex items-center gap-2 mt-1 text-xs text-gray-500">
                      <span>Score: <span className="text-amber-400 font-medium">{(h.importance_score * 100).toFixed(0)}%</span></span>
                      <span>Degree: {h.avg_degree?.toFixed(1)}</span>
                    </div>
                  </div>
                ))}
              </div>
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
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">NetworkX Graph</span></div>
              <div className="flex justify-between"><span>Path Finding</span><span className="text-gray-300">Dijkstra's Algorithm</span></div>
              <div className="flex justify-between"><span>Hub Detection</span><span className="text-gray-300">Degree Centrality</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">POST /ml/optimize/route</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
