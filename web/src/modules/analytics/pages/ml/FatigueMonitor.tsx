import { useState, useEffect } from 'react';
import { Brain, BookOpen, HelpCircle, Zap, Cpu, AlertTriangle, Moon, Timer, CalendarDays, Activity } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import FeaturePills from '../../components/ml/FeaturePills';
import Badge from '../../components/ui/Badge';
import { getFleetFatigue } from '../../services/ml';

const FEATURES = [
  { name: 'Hours Driving (24h)', description: 'Total driving hours in last 24 hours', importance: 'high' as const },
  { name: 'Hours Driving (7d)', description: 'Total driving hours in last 7 days', importance: 'high' as const },
  { name: 'Consecutive Days', description: 'Days of continuous driving without rest', importance: 'high' as const },
  { name: 'Night Trip Ratio', description: 'Fraction of trips driven at night', importance: 'medium' as const },
  { name: 'Hours Since Rest', description: 'Time since last completed trip', importance: 'medium' as const },
  { name: 'Trip Count (24h)', description: 'Number of trips in last 24 hours', importance: 'medium' as const },
];

const RISK_ORDER = ['critical', 'high', 'medium', 'low'];
const RISK_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  critical: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/30' },
  high: { bg: 'bg-orange-500/20', text: 'text-orange-400', border: 'border-orange-500/30' },
  medium: { bg: 'bg-amber-500/20', text: 'text-amber-400', border: 'border-amber-500/30' },
  low: { bg: 'bg-emerald-500/20', text: 'text-emerald-400', border: 'border-emerald-500/30' },
};

export default function FatigueMonitor() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<string>('all');

  useEffect(() => {
    getFleetFatigue()
      .then(res => setData(res.data))
      .catch(err => setError(err?.response?.data?.detail || 'Failed to load fatigue data'))
      .finally(() => setLoading(false));
  }, []);

  const drivers = data?.at_risk_drivers || data?.drivers || [];
  const summary = data?.summary || {};
  const filtered = filter === 'all' ? drivers : drivers.filter((d: any) => d.risk_level === filter);

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Fatigue Monitor"
        subtitle="Real-time driver fatigue risk assessment. Monitors driving hours, rest periods, consecutive days, and night trips to protect driver safety and prevent accidents."
        icon={Brain} iconColor="text-red-400"
        gradientFrom="from-red-600/20" gradientTo="to-rose-600/20" accentBorder="border-red-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-red-400" defaultOpen={true}>
              <p className="mt-2">The fatigue predictor analyzes recent driving activity to assess risk:</p>
              <ul className="list-disc list-inside space-y-1 mt-2">
                <li><strong className="text-gray-300">24-hour window</strong> — Checks driving hours in the last day</li>
                <li><strong className="text-gray-300">7-day window</strong> — Weekly cumulative driving assessment</li>
                <li><strong className="text-gray-300">Consecutive days</strong> — Flags drivers without rest days</li>
                <li><strong className="text-gray-300">Night driving</strong> — Night trips increase fatigue probability</li>
              </ul>
              <p className="mt-3 text-xs text-red-400/80">Drivers classified as Critical or High should not be dispatched until rested.</p>
            </InfoCard>
            <InfoCard title="Safety Thresholds" icon={HelpCircle} iconColor="text-amber-400">
              <div className="space-y-2 mt-2">
                <div className="flex gap-2"><AlertTriangle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">&gt;11 hours/24h</strong> — Critical fatigue risk</span></div>
                <div className="flex gap-2"><AlertTriangle className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">&gt;8 consecutive days</strong> — Mandatory rest needed</span></div>
                <div className="flex gap-2"><AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">&gt;60 hours/week</strong> — Weekly limit exceeded</span></div>
                <div className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">&gt;50% night trips</strong> — Elevated fatigue factor</span></div>
              </div>
            </InfoCard>
          </div>

          {/* Summary KPIs */}
          {!loading && data && (
            <div className="grid grid-cols-4 gap-4 animate-fade-in-up stagger-2">
              {RISK_ORDER.map((level) => {
                const style = RISK_STYLES[level];
                const count = summary[level] || 0;
                return (
                  <button key={level} onClick={() => setFilter(filter === level ? 'all' : level)}
                    className={`${style.bg} rounded-xl border ${style.border} p-4 text-center transition-all hover:scale-105 ${filter === level ? 'ring-2 ring-offset-2 ring-offset-gray-950 ring-current' : ''}`}>
                    <p className={`text-2xl font-bold ${style.text} animate-count-up`}>{count}</p>
                    <p className="text-xs uppercase tracking-wider text-gray-500 capitalize">{level}</p>
                  </button>
                );
              })}
            </div>
          )}

          {/* Driver List */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden animate-fade-in-up stagger-3">
            <div className="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
              <h2 className="text-base font-semibold text-white flex items-center gap-2">
                <Activity className="w-4 h-4 text-red-400" /> At-Risk Drivers
              </h2>
              <span className="text-xs text-gray-500">{filtered.length} drivers</span>
            </div>
            {loading ? (
              <div className="p-8 text-center"><div className="w-6 h-6 border-2 border-red-400/30 border-t-red-400 rounded-full animate-spin mx-auto" /></div>
            ) : error ? (
              <div className="p-8 text-center text-red-400 text-sm">{error}</div>
            ) : filtered.length === 0 ? (
              <div className="p-8 text-center">
                <Brain className="w-12 h-12 text-emerald-400 mx-auto mb-2 animate-float" />
                <p className="text-emerald-400 font-semibold">All Clear</p>
                <p className="text-xs text-gray-500">No drivers at this risk level</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-800/50 max-h-[500px] overflow-y-auto">
                {filtered.map((d: any, i: number) => {
                  const rs = RISK_STYLES[d.risk_level] || RISK_STYLES.medium;
                  return (
                    <div key={d.driver_id} onClick={() => navigate(`/drivers/${d.driver_id}`)}
                      className={`px-5 py-4 hover:bg-gray-800/50 cursor-pointer transition-colors animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-3">
                          <div className={`w-8 h-8 rounded-lg ${rs.bg} border ${rs.border} flex items-center justify-center`}>
                            <Brain className={`w-4 h-4 ${rs.text}`} />
                          </div>
                          <div>
                            <p className="text-sm font-medium text-white">{d.driver_name}</p>
                            <p className="text-xs text-gray-500">ID: {d.driver_id}</p>
                          </div>
                        </div>
                        <Badge label={d.risk_level} variant={d.risk_level === 'low' ? 'success' : d.risk_level === 'medium' ? 'warning' : 'danger'} />
                      </div>
                      <div className="grid grid-cols-4 gap-3 text-xs">
                        <div className="flex items-center gap-1.5"><Timer className="w-3 h-3 text-gray-600" /><span className="text-gray-500">24h:</span><span className="text-gray-300 font-medium">{d.hours_driving_24h?.toFixed(1) ?? d.hours_driving_last_24h?.toFixed(1) ?? '-'}h</span></div>
                        <div className="flex items-center gap-1.5"><CalendarDays className="w-3 h-3 text-gray-600" /><span className="text-gray-500">7d:</span><span className="text-gray-300 font-medium">{d.hours_driving_7d?.toFixed(1) ?? '-'}h</span></div>
                        <div className="flex items-center gap-1.5"><Activity className="w-3 h-3 text-gray-600" /><span className="text-gray-500">Streak:</span><span className="text-gray-300 font-medium">{d.consecutive_days ?? d.consecutive_days_active ?? '-'}d</span></div>
                        <div className="flex items-center gap-1.5"><Moon className="w-3 h-3 text-gray-600" /><span className="text-gray-500">Night:</span><span className="text-gray-300 font-medium">{d.night_trips_ratio != null ? `${(d.night_trips_ratio * 100).toFixed(0)}%` : '-'}</span></div>
                      </div>
                      {d.contributing_factors && d.contributing_factors.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {d.contributing_factors.map((f: string, fi: number) => (
                            <span key={fi} className="text-xs px-2 py-0.5 rounded-full bg-red-500/10 text-red-400/80 border border-red-500/20">{f}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
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
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">Rule-based + ML hybrid</span></div>
              <div className="flex justify-between"><span>Output</span><span className="text-gray-300">Risk level + probability</span></div>
              <div className="flex justify-between"><span>Refresh</span><span className="text-gray-300">Daily (auto-tier)</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">GET /ml/drivers/fatigue</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
