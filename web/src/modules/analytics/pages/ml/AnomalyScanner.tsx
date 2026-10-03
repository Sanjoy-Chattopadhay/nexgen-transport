import { useState } from 'react';
import { AlertTriangle, BookOpen, HelpCircle, Zap, Cpu, Shield, Eye, Clock, Gauge } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import FeaturePills from '../../components/ml/FeaturePills';
import ResultCard from '../../components/ml/ResultCard';
import Badge from '../../components/ui/Badge';
import { scanAnomalies } from '../../services/ml';
import { formatDuration } from '../../lib/formatters';

const SEV_STYLES: Record<string, string> = {
  critical: 'bg-red-500/20 text-red-400 border-red-500/30',
  high: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  medium: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  low: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
};

const FEATURES = [
  { name: 'Duration Ratio', description: 'Trip duration vs route average ratio', importance: 'high' as const },
  { name: 'Speed Ratio', description: 'Average speed vs route average', importance: 'high' as const },
  { name: 'Distance Ratio', description: 'Trip distance vs route average', importance: 'medium' as const },
  { name: 'Time Pattern', description: 'Deviation from time-of-day norms', importance: 'medium' as const },
  { name: 'Isolation Score', description: 'IsolationForest anomaly score', importance: 'high' as const },
  { name: 'Route History', description: 'Comparison with historical trip data', importance: 'medium' as const },
];

export default function AnomalyScanner() {
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  const handleScan = async () => {
    setLoading(true); setResult(null); setError('');
    try {
      const res = await scanAnomalies(days);
      setResult(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Scan failed');
    }
    setLoading(false);
  };

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Anomaly Scanner"
        subtitle="Batch-scan recent trips to detect unusual patterns — extreme durations, impossible speeds, or route deviations. Auto-creates alerts for operations review."
        icon={AlertTriangle} iconColor="text-amber-400"
        gradientFrom="from-amber-600/20" gradientTo="to-orange-600/20" accentBorder="border-amber-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-amber-400" defaultOpen={true}>
              <p className="mt-2">The anomaly scanner uses <strong className="text-gray-300">Isolation Forest</strong> to identify trips that deviate significantly from normal patterns:</p>
              <ol className="list-decimal list-inside space-y-1 mt-2">
                <li>Scans all completed trips in the selected time window</li>
                <li>Compares each trip against route-specific averages</li>
                <li>Assigns an anomaly score (0-1, higher = more unusual)</li>
                <li>Creates alerts for anomalous trips, categorized by severity</li>
              </ol>
            </InfoCard>
            <InfoCard title="What Gets Flagged" icon={HelpCircle} iconColor="text-red-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" /><span>Duration <strong className="text-gray-300">3x+ route average</strong> — unusually long trip</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" /><span>Speed <strong className="text-gray-300">{'>'} 100 km/h</strong> or near-zero — data error or unsafe driving</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span>Distance <strong className="text-gray-300">{'>'} 2x route avg</strong> — possible detour or GPS error</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span>Trips arriving <strong className="text-gray-300">before they started</strong> — time-travel anomaly</span></li>
              </ul>
            </InfoCard>
          </div>

          {/* Scan Controls */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Eye className="w-4 h-4 text-amber-400" /> Run Anomaly Scan
            </h2>
            <div className="flex items-end gap-4 mb-2">
              <div className="flex-1">
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Scan Window</label>
                <div className="flex items-center gap-3">
                  <input type="range" min={1} max={90} value={days} onChange={e => setDays(Number(e.target.value))}
                    className="flex-1 h-2 bg-gray-800 rounded-lg appearance-none cursor-pointer accent-amber-500" />
                  <span className="text-sm font-bold text-amber-400 w-16 text-right">{days} days</span>
                </div>
              </div>
              <button onClick={handleScan} disabled={loading}
                className="px-6 py-2.5 bg-amber-600 hover:bg-amber-500 text-white rounded-xl font-semibold text-sm transition-all flex items-center gap-2 disabled:opacity-50 shadow-lg shadow-amber-600/20">
                {loading ? <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Scanning...</>
                  : <><Shield className="w-4 h-4" /> Scan Trips</>}
              </button>
            </div>
          </div>

          {error && <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-4 animate-scale-in"><p className="text-sm text-red-400">{error}</p></div>}

          {result && (
            <ResultCard title="Scan Results" gradient="from-amber-900/30 to-orange-900/30" border="border-amber-700/30">
              {/* Summary KPIs */}
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <p className="text-xs text-gray-500 mb-1">Trips Scanned</p>
                  <p className="text-2xl font-bold text-white animate-count-up">{result.trips_scanned || 0}</p>
                </div>
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <p className="text-xs text-gray-500 mb-1">Anomalies Found</p>
                  <p className={`text-2xl font-bold animate-count-up ${(result.anomalies_found || 0) > 0 ? 'text-amber-400' : 'text-emerald-400'}`}>
                    {result.anomalies_found || 0}
                  </p>
                </div>
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 text-center">
                  <p className="text-xs text-gray-500 mb-1">Alerts Created</p>
                  <p className="text-2xl font-bold text-red-400 animate-count-up">{result.alerts_created || 0}</p>
                </div>
              </div>

              {/* Severity Breakdown */}
              {result.severity_breakdown && Object.keys(result.severity_breakdown).length > 0 && (
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800 mb-4">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-3">Severity Breakdown</p>
                  <div className="flex gap-3">
                    {Object.entries(result.severity_breakdown).map(([sev, count]) => (
                      <div key={sev} className={`flex-1 text-center px-3 py-2 rounded-lg border ${SEV_STYLES[sev] || SEV_STYLES.medium}`}>
                        <p className="text-lg font-bold">{String(count)}</p>
                        <p className="text-xs uppercase tracking-wider capitalize">{sev}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Top Anomalies */}
              {result.top_anomalies && result.top_anomalies.length > 0 && (
                <div className="bg-gray-900/60 rounded-lg border border-gray-800 overflow-hidden">
                  <p className="text-xs text-gray-500 uppercase tracking-wider px-4 py-3 border-b border-gray-800">Top Anomalies</p>
                  <div className="divide-y divide-gray-800/50">
                    {result.top_anomalies.slice(0, 10).map((a: any, i: number) => (
                      <div key={i} className={`px-4 py-3 animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                        <div className="flex items-start justify-between mb-1">
                          <div>
                            <span className="text-sm font-medium text-white">{a.dispatch_entry_no || `Trip #${a.trip_id}`}</span>
                            <span className="text-xs text-gray-500 ml-2">{a.driver_name}</span>
                          </div>
                          <Badge label={`Score: ${(a.anomaly_score * 100).toFixed(0)}%`}
                            variant={a.anomaly_score >= 0.9 ? 'danger' : a.anomaly_score >= 0.7 ? 'warning' : 'info'} />
                        </div>
                        <p className="text-xs text-gray-400">{a.origin} → {a.destination}</p>
                        <p className="text-xs text-amber-400/80 mt-1">{a.anomaly_reason}</p>
                        <div className="flex gap-4 mt-1 text-xs text-gray-500">
                          <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{formatDuration(a.trip_duration_minutes)}</span>
                          <span className="flex items-center gap-1"><Gauge className="w-3 h-3" />Route avg: {formatDuration(a.route_avg_duration)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {result.anomalies_found === 0 && (
                <div className="text-center py-6">
                  <Shield className="w-12 h-12 text-emerald-400 mx-auto mb-2 animate-float" />
                  <p className="text-emerald-400 font-semibold">All Clear!</p>
                  <p className="text-xs text-gray-500 mt-1">No anomalies detected in the last {days} days</p>
                </div>
              )}
            </ResultCard>
          )}
        </div>

        <div className="space-y-5">
          <div className="animate-fade-in-up stagger-3 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <FeaturePills features={FEATURES} />
          </div>
          <div className="animate-fade-in-up stagger-4 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2"><Cpu className="w-4 h-4 text-purple-400" /> Developer Info</h3>
            <div className="space-y-2 text-xs text-gray-500">
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">Isolation Forest</span></div>
              <div className="flex justify-between"><span>Type</span><span className="text-gray-300">Unsupervised</span></div>
              <div className="flex justify-between"><span>Mode</span><span className="text-gray-300">Batch scan</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">POST /ml/scan/anomalies</span></div>
              <div className="flex justify-between"><span>Side Effect</span><span className="text-gray-300">Creates alerts in DB</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
