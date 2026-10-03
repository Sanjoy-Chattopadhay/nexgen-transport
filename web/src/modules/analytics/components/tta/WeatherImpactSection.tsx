import { useState } from 'react';
import { CloudRain, CloudSun, Loader2 } from 'lucide-react';
import {
  ResponsiveContainer, ComposedChart, Bar, Line, Cell, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts';
import { getTTATripWeather } from '../../services/tta';
import { formatDuration } from '../../lib/formatters';
import { CHART_COLORS } from '../../lib/colors';
import { tc } from '../../../../core/theme';

const TOOLTIP_STYLE = { backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') };

const BUCKET_COLORS: Record<string, string> = {
  clear: tc('#3b82f6'),
  rain: tc('#06b6d4'),
  heavy_rain: tc('#ef4444'),
  storm: tc('#a855f7'),
  fog: tc('#9ca3af'),
  snow: tc('#e5e7eb'),
  unknown: tc('#4b5563'),
};

const VERDICT_UI: Record<string, { label: string; cls: string }> = {
  weather_was_a_factor: { label: 'Weather WAS a factor on this trip', cls: 'bg-red-900/30 text-red-300 border-red-800' },
  weather_present_but_minor: { label: 'Adverse weather present, but impact minor', cls: 'bg-amber-900/30 text-amber-300 border-amber-800' },
  weather_was_clear: { label: 'Clear weather throughout — slowdowns were not weather-driven', cls: 'bg-emerald-900/30 text-emerald-300 border-emerald-800' },
  no_data: { label: 'No weather data available', cls: 'bg-gray-800 text-gray-400 border-gray-700' },
};

export default function WeatherImpactSection({ tripNo }: { tripNo: number | string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWeather = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getTTATripWeather(tripNo);
      setData(res.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e.message || 'Weather fetch failed');
    } finally {
      setLoading(false);
    }
  };

  const verdict = data ? (VERDICT_UI[data.verdict] || VERDICT_UI.no_data) : null;
  const chartData = data ? (data.windows as any[]).map(w => ({
    window: w.window.split(' ').slice(-1)[0],
    full: w.window,
    speed: w.avg_speed_kmph,
    rain: w.weather?.rain_mm ?? 0,
    temp: w.weather?.temperature_c,
    bucket: w.weather_bucket,
    caused: w.weather_caused,
  })) : [];

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        <div>
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <CloudRain className="w-5 h-5 text-blue-400" /> Weather Impact on Speed
          </h2>
          <p className="text-xs text-gray-500 mt-1">
            Historical hourly weather fetched at the trip's own GPS coordinates and timestamps (Open-Meteo, cached) — was a slowdown weather, traffic, or driver?
          </p>
        </div>
        {!data && (
          <button onClick={fetchWeather} disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition-colors">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CloudSun className="w-4 h-4" />}
            {loading ? 'Fetching weather along route…' : 'Fetch Weather for This Trip'}
          </button>
        )}
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}

      {data && verdict && (
        <>
          <div className={`border rounded-lg px-4 py-3 text-sm font-medium mb-4 ${verdict.cls}`}>
            {verdict.label}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-5">
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Windows Analysed</p>
              <p className="text-xl font-bold text-gray-100">{data.summary.windows_total}</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Adverse Weather</p>
              <p className="text-xl font-bold text-cyan-400">{data.summary.windows_adverse_weather}</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Slow Windows</p>
              <p className="text-xl font-bold text-amber-400">{data.summary.windows_slow}</p>
              <p className="text-xs text-gray-500">below {data.summary.slow_threshold_kmph} km/h</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Slow + Adverse</p>
              <p className="text-xl font-bold text-red-400">{data.summary.windows_slow_and_adverse}</p>
              <p className="text-xs text-gray-500">weather-caused slowdowns</p>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">Time Lost to Weather</p>
              <p className="text-xl font-bold text-red-400">{formatDuration(data.summary.minutes_lost_to_weather)}</p>
            </div>
          </div>

          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
              <XAxis dataKey="window" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} />
              <YAxis yAxisId="spd" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} label={{ value: 'km/h', angle: -90, fill: tc('#6b7280'), fontSize: 12 }} />
              <YAxis yAxisId="rain" orientation="right" tick={{ fill: tc('#9ca3af'), fontSize: 12 }} axisLine={false} tickLine={false} label={{ value: 'mm/h rain', angle: 90, fill: tc('#6b7280'), fontSize: 12 }} />
              <Tooltip contentStyle={TOOLTIP_STYLE}
                formatter={(v: any, name: any) => [v, name]}
                labelFormatter={(_, payload: any) => payload?.[0]?.payload?.full ?? ''} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar yAxisId="spd" dataKey="speed" name="Avg speed (bar colour = weather)">
                {chartData.map((d, i) => (
                  <Cell key={i} fill={BUCKET_COLORS[d.bucket] || tc('#3b82f6')} opacity={d.caused ? 1 : 0.75} />
                ))}
              </Bar>
              <Line yAxisId="rain" type="monotone" dataKey="rain" name="Rain mm/h" stroke={tc(tc('#ef4444'))} strokeWidth={2} dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="flex items-center gap-4 text-xs text-gray-500 mt-1 flex-wrap">
            {Object.entries(BUCKET_COLORS).filter(([b]) => b !== 'unknown').map(([b, c]) => (
              <span key={b} className="flex items-center gap-1.5">
                <span className="w-3 h-3 rounded-sm inline-block" style={{ background: c }} /> {b.replace('_', ' ')}
              </span>
            ))}
          </div>

          {(data.windows as any[]).some(w => w.weather_caused) && (
            <div className="mt-4">
              <h3 className="text-sm font-semibold text-gray-300 mb-2">Weather-caused slow windows</h3>
              <div className="space-y-1.5">
                {(data.windows as any[]).filter(w => w.weather_caused).map((w, i) => (
                  <p key={i} className="text-xs text-gray-400 bg-red-900/20 border border-red-900/40 rounded-lg px-3 py-2">
                    <span className="text-gray-200 font-medium">{w.window}</span> — {w.note}
                  </p>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
