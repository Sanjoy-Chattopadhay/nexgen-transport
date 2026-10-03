import { useState } from 'react';
import { Clock, HelpCircle, BookOpen, Cpu, Calendar, Zap } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import LocationSelect from '../../components/ml/LocationSelect';
import RouteStatsPreview from '../../components/ml/RouteStatsPreview';
import FeaturePills from '../../components/ml/FeaturePills';
import ResultCard from '../../components/ml/ResultCard';
import { predictEta } from '../../services/ml';

const ETA_FEATURES = [
  { name: 'hour', description: 'Hour of departure (0-23)', importance: 'medium' as const },
  { name: 'day_of_week', description: 'Day of week (Mon=0 to Sun=6)', importance: 'medium' as const },
  { name: 'is_weekend', description: 'Whether trip starts on weekend', importance: 'low' as const },
  { name: 'month', description: 'Month of year (1-12)', importance: 'low' as const },
  { name: 'time_bucket', description: 'Morning / Afternoon / Evening / Night', importance: 'medium' as const },
  { name: 'route_avg_duration', description: 'Historical average duration for this route', importance: 'high' as const },
  { name: 'route_avg_distance', description: 'Average distance for this route', importance: 'high' as const },
  { name: 'route_trip_count', description: 'How many trips completed on this route', importance: 'medium' as const },
  { name: 'route_eta_success', description: 'ETA success rate for this route', importance: 'medium' as const },
  { name: 'driver_avg_duration', description: "Driver's average trip duration", importance: 'high' as const },
  { name: 'driver_avg_speed', description: "Driver's average speed", importance: 'medium' as const },
  { name: 'driver_eta_success', description: "Driver's ETA success rate", importance: 'medium' as const },
  { name: 'driver_total_trips', description: "Driver's total trip count (experience)", importance: 'medium' as const },
  { name: 'driver_vehicles_used', description: 'Number of different vehicles driver has used', importance: 'low' as const },
  { name: 'vehicle_avg_speed', description: "Vehicle's average speed", importance: 'medium' as const },
  { name: 'vehicle_total_trips', description: "Vehicle's total trips", importance: 'low' as const },
  { name: 'vehicle_eta_success', description: "Vehicle's ETA success rate", importance: 'low' as const },
  { name: 'time_pattern_avg_duration', description: 'Avg duration at this hour+day on this route (top feature ~43%)', importance: 'high' as const },
  { name: 'time_pattern_trip_count', description: 'Trips at this specific hour+day', importance: 'medium' as const },
  { name: 'time_pattern_eta_success', description: 'ETA success at this specific time', importance: 'low' as const },
  { name: 'trip_km', description: 'Planned trip distance in km', importance: 'medium' as const },
  { name: 'is_5am_default', description: 'Flag: suspicious default 5:00 AM timestamp', importance: 'low' as const },
];

function fmtDuration(minutes: number): string {
  if (minutes == null) return '-';
  const d = Math.floor(minutes / 1440);
  const h = Math.floor((minutes % 1440) / 60);
  const m = Math.round(minutes % 60);
  const parts: string[] = [];
  if (d > 0) parts.push(`${d}d`);
  if (h > 0) parts.push(`${h}h`);
  if (m > 0 || parts.length === 0) parts.push(`${m}m`);
  return parts.join(' ');
}

export default function ETAPredictor() {
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [driverId, setDriverId] = useState('');
  const [vehicleId, setVehicleId] = useState('');
  const [tripKm, setTripKm] = useState('');
  const [tripStart, setTripStart] = useState(new Date().toISOString().slice(0, 16));
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');

  const canPredict = origin && destination;

  const handlePredict = async () => {
    if (!canPredict) return;
    setLoading(true);
    setResult(null);
    setError('');
    try {
      const payload: any = { origin, destination, trip_start: tripStart || new Date().toISOString() };
      if (driverId) payload.driver_id = Number(driverId);
      if (vehicleId) payload.vehicle_id = Number(vehicleId);
      if (tripKm) payload.trip_km = Number(tripKm);
      const res = await predictEta(payload);
      setResult(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Prediction failed');
    }
    setLoading(false);
  };

  const predicted = result?.predicted_duration_minutes;
  const startDate = tripStart ? new Date(tripStart) : new Date();
  const arrivalDate = predicted ? new Date(startDate.getTime() + predicted * 60 * 1000) : null;

  const fmtDate = (d: Date) => d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  const fmtTime = (d: Date) => d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="ETA Predictor"
        subtitle="Predict trip duration using XGBoost regression with 22 engineered features. Combines route history, driver behavior, vehicle stats, and temporal patterns for accurate arrival estimates."
        icon={Clock} iconColor="text-blue-400"
        gradientFrom="from-blue-600/20" gradientTo="to-indigo-600/20" accentBorder="border-blue-500/30"
        accuracy="R2: 0.92 | MAE: 446m"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column — Form + Route Stats */}
        <div className="lg:col-span-2 space-y-5">
          {/* Info Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-blue-400" defaultOpen={true}>
              <ol className="list-decimal list-inside space-y-2 mt-2">
                <li><strong className="text-gray-300">Select Route</strong> — Choose origin and destination. We'll show you historical data for this route.</li>
                <li><strong className="text-gray-300">Add Context</strong> — Optionally add driver, vehicle, distance, and departure time for better accuracy.</li>
                <li><strong className="text-gray-300">Get Prediction</strong> — Our XGBoost model analyzes 22 features to predict trip duration.</li>
                <li><strong className="text-gray-300">Compare</strong> — See how the prediction compares to route and driver averages.</li>
              </ol>
              <p className="mt-3 text-xs text-gray-500">The model was trained on {'>'}500K completed trips. Top features: time-pattern average duration (43%), route average duration (41%).</p>
            </InfoCard>
            <InfoCard title="Tips for Best Results" icon={HelpCircle} iconColor="text-emerald-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span>Provide <strong className="text-gray-300">driver ID</strong> if known — the model uses driver-specific performance history.</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span>Set the actual <strong className="text-gray-300">departure time</strong> — time-of-day and day-of-week strongly affect duration.</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span>Routes with more historical trips produce more accurate predictions.</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span>If trip distance differs from the standard route, enter it manually.</span></li>
              </ul>
            </InfoCard>
          </div>

          {/* Prediction Form */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Clock className="w-4 h-4 text-blue-400" /> Predict Trip Duration
            </h2>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <LocationSelect label="Origin *" value={origin} onChange={setOrigin} placeholder="Search origin..." />
              <LocationSelect label="Destination *" value={destination} onChange={setDestination} placeholder="Search destination..." />
            </div>

            {/* Route Stats Preview */}
            <div className="mb-4">
              <RouteStatsPreview origin={origin} destination={destination} />
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5">
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Departure Time</label>
                <input type="datetime-local" value={tripStart} onChange={e => setTripStart(e.target.value)}
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/30 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Driver ID <span className="text-gray-600">(optional)</span></label>
                <input type="number" value={driverId} onChange={e => setDriverId(e.target.value)} placeholder="e.g. 42"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/30 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Vehicle ID <span className="text-gray-600">(optional)</span></label>
                <input type="number" value={vehicleId} onChange={e => setVehicleId(e.target.value)} placeholder="e.g. 105"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/30 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Trip KM <span className="text-gray-600">(optional)</span></label>
                <input type="number" value={tripKm} onChange={e => setTripKm(e.target.value)} placeholder="auto from route"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/30 transition-all" />
              </div>
            </div>

            <button onClick={handlePredict} disabled={!canPredict || loading}
              className={`w-full py-3 rounded-xl font-semibold text-sm transition-all duration-200 flex items-center justify-center gap-2 ${
                canPredict
                  ? 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-600/20 hover:shadow-blue-500/30 animate-pulse-glow'
                  : 'bg-gray-800 text-gray-500 cursor-not-allowed'
              }`}>
              {loading ? (
                <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Predicting...</>
              ) : (
                <><Cpu className="w-4 h-4" /> Predict ETA</>
              )}
            </button>
          </div>

          {/* Error */}
          {error && (
            <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-4 animate-scale-in">
              <p className="text-sm text-red-400">{error}</p>
            </div>
          )}

          {/* Result */}
          {result && predicted != null && (
            <ResultCard title="Prediction Result" gradient="from-blue-900/40 to-indigo-900/40" border="border-blue-700/30">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                {/* Arrival */}
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800">
                  <div className="flex items-center gap-2 mb-2">
                    <Calendar className="w-4 h-4 text-blue-400" />
                    <span className="text-xs text-gray-500 uppercase tracking-wider">Estimated Arrival</span>
                  </div>
                  {arrivalDate && (
                    <>
                      <p className="text-lg font-bold text-white animate-count-up">{fmtDate(arrivalDate)}</p>
                      <p className="text-2xl font-bold text-blue-400 animate-count-up">{fmtTime(arrivalDate)}</p>
                    </>
                  )}
                </div>
                {/* Duration */}
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800">
                  <div className="flex items-center gap-2 mb-2">
                    <Clock className="w-4 h-4 text-purple-400" />
                    <span className="text-xs text-gray-500 uppercase tracking-wider">Trip Duration</span>
                  </div>
                  <p className="text-2xl font-bold text-white animate-count-up">{fmtDuration(predicted)}</p>
                  <p className="text-xs text-gray-500 mt-1">{predicted.toFixed(0)} minutes</p>
                </div>
              </div>

              {/* Comparison */}
              {(result.route_avg_duration != null || result.driver_avg_duration != null) && (
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-3">Comparison</p>
                  <div className="space-y-2">
                    {result.route_avg_duration != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-gray-400">Route Average</span>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-gray-300">{fmtDuration(result.route_avg_duration)}</span>
                          {(() => {
                            const diff = predicted - result.route_avg_duration;
                            const pct = result.route_avg_duration > 0 ? (diff / result.route_avg_duration * 100) : 0;
                            return (
                              <span className={`text-xs font-medium ${diff > 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                                {diff > 0 ? '+' : ''}{pct.toFixed(1)}%
                              </span>
                            );
                          })()}
                        </div>
                      </div>
                    )}
                    {result.driver_avg_duration != null && (
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-gray-400">Driver Average</span>
                        <span className="text-sm font-medium text-gray-300">{fmtDuration(result.driver_avg_duration)}</span>
                      </div>
                    )}
                    <div className="flex items-center justify-between pt-2 border-t border-gray-700/50">
                      <span className="text-sm font-medium text-blue-400">ML Predicted</span>
                      <span className="text-sm font-bold text-blue-400">{fmtDuration(predicted)}</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Features used (collapsible) */}
              {result.features_used && (
                <details className="mt-4 group">
                  <summary className="text-xs text-gray-500 cursor-pointer hover:text-gray-400 transition-colors flex items-center gap-1">
                    <Cpu className="w-3 h-3" /> View raw feature values used
                  </summary>
                  <div className="mt-2 grid grid-cols-2 md:grid-cols-3 gap-1 text-xs">
                    {Object.entries(result.features_used).map(([k, v]) => (
                      <div key={k} className="flex justify-between px-2 py-1 bg-gray-800/50 rounded">
                        <span className="text-gray-500 truncate mr-2">{k}</span>
                        <span className="text-gray-300 font-mono">{typeof v === 'number' ? (v as number).toFixed(2) : String(v)}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </ResultCard>
          )}
        </div>

        {/* Right Column — Features & Developer Info */}
        <div className="space-y-5">
          <div className="animate-fade-in-up stagger-3 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <FeaturePills features={ETA_FEATURES} />
          </div>

          <div className="animate-fade-in-up stagger-4 bg-gray-900 rounded-xl border border-gray-800 p-5">
            <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-purple-400" /> Developer Info
            </h3>
            <div className="space-y-2 text-xs text-gray-500">
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">XGBoost Regressor</span></div>
              <div className="flex justify-between"><span>Target</span><span className="text-gray-300">trip_duration_minutes</span></div>
              <div className="flex justify-between"><span>Training Data</span><span className="text-gray-300">&gt;500K trips</span></div>
              <div className="flex justify-between"><span>Top Feature</span><span className="text-gray-300">time_pattern_avg (43%)</span></div>
              <div className="flex justify-between"><span>2nd Feature</span><span className="text-gray-300">route_avg_duration (41%)</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">POST /ml/predict/eta</span></div>
              <div className="flex justify-between"><span>Fallbacks</span><span className="text-gray-300">Route avg for missing features</span></div>
            </div>
          </div>

          <div className="animate-fade-in-up stagger-5 bg-gradient-to-br from-blue-900/20 to-indigo-900/20 rounded-xl border border-blue-800/30 p-5">
            <h3 className="text-sm font-semibold text-blue-300 mb-2">API Quick Start</h3>
            <pre className="text-xs text-gray-400 bg-gray-900/60 rounded-lg p-3 overflow-x-auto leading-relaxed">
{`POST /ml/predict/eta
{
  "origin": "JHARSUGUDA",
  "destination": "CHENNAI",
  "trip_start": "2026-03-29T10:00",
  "driver_id": 42,
  "vehicle_id": 105,
  "trip_km": 1450
}`}
            </pre>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
