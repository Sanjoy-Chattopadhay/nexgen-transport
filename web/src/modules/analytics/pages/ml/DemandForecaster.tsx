import { useState, useEffect } from 'react';
import { TrendingUp, BookOpen, HelpCircle, Zap, Cpu, BarChart3, ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import FeaturePills from '../../components/ml/FeaturePills';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { forecastTrips, getDemandForecast } from '../../services/ml';
import { tc } from '../../../../core/theme';

const FEATURES = [
  { name: 'Historical Volume', description: 'Past trip counts by day/week/month', importance: 'high' as const },
  { name: 'Day of Week', description: 'Weekly seasonality pattern', importance: 'high' as const },
  { name: 'Monthly Trend', description: 'Long-term growth or decline', importance: 'medium' as const },
  { name: 'Route Popularity', description: 'Route-specific demand levels', importance: 'medium' as const },
  { name: 'Moving Average', description: '7-day and 30-day moving averages', importance: 'medium' as const },
  { name: 'Growth Rate', description: 'Week-over-week growth percentage', importance: 'low' as const },
];

const TREND_ICONS: Record<string, any> = {
  increasing: { icon: ArrowUpRight, color: 'text-emerald-400', bg: 'bg-emerald-500/20' },
  stable: { icon: Minus, color: 'text-gray-400', bg: 'bg-gray-500/20' },
  decreasing: { icon: ArrowDownRight, color: 'text-red-400', bg: 'bg-red-500/20' },
};

export default function DemandForecaster() {
  const [loading, setLoading] = useState(true);
  const [fleetData, setFleetData] = useState<any>(null);
  const [demandData, setDemandData] = useState<any>(null);
  const [error] = useState('');

  useEffect(() => {
    Promise.all([
      forecastTrips().then(r => setFleetData(r.data)).catch(() => {}),
      getDemandForecast().then(r => setDemandData(r.data)).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);

  const fleet = fleetData?.fleet_forecast;
  const daily = fleet?.daily_breakdown || [];
  const topRoutes = Object.entries(fleetData?.top_routes || demandData?.forecasts || {}).slice(0, 10);

  const dayLabels = ['Day 1', 'Day 2', 'Day 3', 'Day 4', 'Day 5', 'Day 6', 'Day 7'];
  const chartData = daily.map((v: number, i: number) => ({ name: dayLabels[i], trips: v }));

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Demand Forecaster"
        subtitle="7-day trip volume forecasting for the fleet and per route. Plan fleet capacity, driver allocation, and logistics operations ahead of demand peaks."
        icon={TrendingUp} iconColor="text-indigo-400"
        gradientFrom="from-indigo-600/20" gradientTo="to-violet-600/20" accentBorder="border-indigo-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-indigo-400" defaultOpen={true}>
              <p className="mt-2">The demand forecaster uses historical trip patterns to predict future volume:</p>
              <ul className="list-disc list-inside space-y-1 mt-2">
                <li>Analyzes <strong className="text-gray-300">weekly seasonality</strong> (Mon-Sun patterns)</li>
                <li>Tracks <strong className="text-gray-300">growth trends</strong> (increasing, stable, declining)</li>
                <li>Forecasts <strong className="text-gray-300">7 days ahead</strong> per route and fleet-wide</li>
                <li>Uses moving averages to smooth out noise</li>
              </ul>
            </InfoCard>
            <InfoCard title="Use Cases" icon={HelpCircle} iconColor="text-emerald-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Fleet Planning</strong> — Know how many trucks you'll need next week</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Driver Scheduling</strong> — Allocate drivers to expected high-demand routes</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Capacity Alerts</strong> — Get warned when demand exceeds capacity</span></li>
              </ul>
            </InfoCard>
          </div>

          {loading ? (
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-8 text-center"><div className="w-6 h-6 border-2 border-indigo-400/30 border-t-indigo-400 rounded-full animate-spin mx-auto" /></div>
          ) : error ? (
            <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-4"><p className="text-sm text-red-400">{error}</p></div>
          ) : (
            <>
              {/* Fleet KPIs */}
              {fleet && (
                <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
                  <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
                    <BarChart3 className="w-4 h-4 text-indigo-400" /> Fleet Forecast — Next 7 Days
                  </h2>
                  <div className="grid grid-cols-3 gap-4 mb-4">
                    <div className="bg-gray-800/50 rounded-lg p-4 text-center">
                      <p className="text-3xl font-bold text-indigo-400 animate-count-up">{fleet.total_expected_trips_next_7d}</p>
                      <p className="text-xs text-gray-500 mt-1">Expected Trips</p>
                    </div>
                    <div className="bg-gray-800/50 rounded-lg p-4 text-center">
                      {(() => {
                        const t = TREND_ICONS[fleet.trend] || TREND_ICONS.stable;
                        const TIcon = t.icon;
                        return (<>
                          <div className={`w-10 h-10 rounded-full ${t.bg} flex items-center justify-center mx-auto`}>
                            <TIcon className={`w-5 h-5 ${t.color}`} />
                          </div>
                          <p className={`text-xs font-medium capitalize mt-2 ${t.color}`}>{fleet.trend}</p>
                        </>);
                      })()}
                    </div>
                    <div className="bg-gray-800/50 rounded-lg p-4 text-center">
                      <p className={`text-3xl font-bold ${(fleet.growth_rate || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {fleet.growth_rate != null ? `${(fleet.growth_rate * 100).toFixed(1)}%` : '-'}
                      </p>
                      <p className="text-xs text-gray-500 mt-1">Growth Rate</p>
                    </div>
                  </div>
                  {chartData.length > 0 && (
                    <div className="h-48">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" stroke={tc(tc('#374151'))} />
                          <XAxis dataKey="name" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                          <YAxis tick={{ fill: tc('#9ca3af'), fontSize: 12 }} />
                          <Tooltip contentStyle={{ backgroundColor: tc('#1f2937'), border: `1px solid ${tc('#374151')}`, borderRadius: 8 }} />
                          <Bar dataKey="trips" fill="#6366f1" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
              )}

              {/* Top Routes */}
              {topRoutes.length > 0 && (
                <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden animate-fade-in-up stagger-3">
                  <div className="px-5 py-4 border-b border-gray-800">
                    <h2 className="text-base font-semibold text-white flex items-center gap-2">
                      <TrendingUp className="w-4 h-4 text-indigo-400" /> Top Route Forecasts
                    </h2>
                  </div>
                  <div className="divide-y divide-gray-800/50 max-h-[400px] overflow-y-auto">
                    {topRoutes.map(([route, data]: [string, any], i) => {
                      const t = TREND_ICONS[data.trend] || TREND_ICONS.stable;
                      const TIcon = t.icon;
                      const rawForecast = data.daily_breakdown || data.next_7_days || [];
                      // Normalise: items may be plain numbers or {predicted_trips} objects
                      const dailyNums: number[] = rawForecast.map((v: any) =>
                        typeof v === 'number' ? v : (v?.predicted_trips ?? v?.trips ?? 0)
                      );
                      const weekTotal = dailyNums.reduce((a: number, b: number) => a + b, 0);
                      const maxVal = Math.max(...dailyNums, 1);
                      return (
                        <div key={route} className={`px-5 py-3 animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-sm text-white font-medium">{route}</span>
                            <div className="flex items-center gap-2">
                              <TIcon className={`w-3.5 h-3.5 ${t.color}`} />
                              <span className="text-xs text-gray-400">{weekTotal || '-'} trips/week</span>
                            </div>
                          </div>
                          {dailyNums.length > 0 && (
                            <div className="flex gap-1 mt-1">
                              {dailyNums.slice(0, 7).map((v: number, di: number) => (
                                <div key={di} className="flex-1 text-center">
                                  <div className="h-6 bg-gray-800 rounded-sm overflow-hidden flex items-end">
                                    <div className="w-full bg-indigo-500/50 rounded-sm transition-all animate-progress-fill"
                                      style={{ height: `${Math.min(100, (v / maxVal) * 100)}%` }} />
                                  </div>
                                  <span className="text-xs text-gray-600">{v}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
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
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">Time Series + Moving Avg</span></div>
              <div className="flex justify-between"><span>Horizon</span><span className="text-gray-300">7 days ahead</span></div>
              <div className="flex justify-between"><span>Granularity</span><span className="text-gray-300">Per route + fleet</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">GET /ml/forecast/trips</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
