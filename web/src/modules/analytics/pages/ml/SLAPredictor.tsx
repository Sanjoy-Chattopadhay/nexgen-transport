import { useState } from 'react';
import { ShieldCheck, BookOpen, HelpCircle, Zap, Cpu, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import LocationSelect from '../../components/ml/LocationSelect';
import RouteStatsPreview from '../../components/ml/RouteStatsPreview';
import FeaturePills from '../../components/ml/FeaturePills';
import ResultCard from '../../components/ml/ResultCard';
import { predictSla } from '../../services/ml';

const RISK_STYLES: Record<string, { bg: string; text: string; border: string; label: string }> = {
  low:      { bg: 'bg-emerald-500/20', text: 'text-emerald-400', border: 'border-emerald-500/30', label: 'Low Risk' },
  medium:   { bg: 'bg-amber-500/20', text: 'text-amber-400', border: 'border-amber-500/30', label: 'Medium Risk' },
  high:     { bg: 'bg-orange-500/20', text: 'text-orange-400', border: 'border-orange-500/30', label: 'High Risk' },
  critical: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/30', label: 'Critical Risk' },
};

const FEATURES = [
  { name: 'Route History', description: 'Avg duration, distance, ETA success for this route', importance: 'high' as const },
  { name: 'Driver Track Record', description: 'Driver ETA success rate and consistency', importance: 'high' as const },
  { name: 'Time Patterns', description: 'Time-of-day and day-of-week success rates', importance: 'medium' as const },
  { name: 'Vehicle Performance', description: 'Vehicle speed and reliability metrics', importance: 'medium' as const },
  { name: 'Predicted Duration', description: 'ETA model prediction as input feature', importance: 'high' as const },
  { name: 'Historical Delay', description: 'Average delay on this route', importance: 'medium' as const },
];

export default function SLAPredictor() {
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
    setLoading(true); setResult(null); setError('');
    try {
      const payload: any = { origin, destination, trip_start: tripStart || new Date().toISOString() };
      if (driverId) payload.driver_id = Number(driverId);
      if (vehicleId) payload.vehicle_id = Number(vehicleId);
      if (tripKm) payload.trip_km = Number(tripKm);
      const res = await predictSla(payload);
      setResult(res.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || 'Prediction failed');
    }
    setLoading(false);
  };

  const risk = result?.risk_level ? RISK_STYLES[result.risk_level] || RISK_STYLES.medium : null;
  const prob = result?.on_time_probability;

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="SLA Predictor"
        subtitle="Assess the probability of on-time delivery before dispatch. Get risk levels and contributing factors to make informed allocation decisions."
        icon={ShieldCheck} iconColor="text-emerald-400"
        gradientFrom="from-emerald-600/20" gradientTo="to-teal-600/20" accentBorder="border-emerald-500/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-fade-in-up stagger-1">
            <InfoCard title="How It Works" icon={BookOpen} iconColor="text-emerald-400" defaultOpen={true}>
              <p className="mt-2">The SLA Predictor combines ETA prediction with historical reliability data to assess delivery risk:</p>
              <ul className="list-disc list-inside space-y-1 mt-2">
                <li><strong className="text-gray-300">On-Time Probability</strong> — Likelihood of meeting the ETA deadline (0-100%)</li>
                <li><strong className="text-gray-300">Risk Level</strong> — Low / Medium / High / Critical classification</li>
                <li><strong className="text-gray-300">Contributing Factors</strong> — Which factors increase or decrease risk</li>
              </ul>
              <p className="mt-3 text-xs text-gray-500">Uses the ETA model internally, plus additional SLA-specific features.</p>
            </InfoCard>
            <InfoCard title="When to Use" icon={HelpCircle} iconColor="text-amber-400">
              <ul className="space-y-2 mt-2">
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Before dispatch</strong> — Check if the trip is likely to be on-time.</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Driver selection</strong> — Compare risk levels with different drivers.</span></li>
                <li className="flex gap-2"><Zap className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" /><span><strong className="text-gray-300">Priority handling</strong> — Identify critical shipments needing fast drivers.</span></li>
              </ul>
            </InfoCard>
          </div>

          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-400" /> Assess Delivery Risk
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
              <LocationSelect label="Origin *" value={origin} onChange={setOrigin} />
              <LocationSelect label="Destination *" value={destination} onChange={setDestination} />
            </div>
            <div className="mb-4"><RouteStatsPreview origin={origin} destination={destination} /></div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5">
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Departure</label>
                <input type="datetime-local" value={tripStart} onChange={e => setTripStart(e.target.value)}
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-emerald-500/50 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Driver ID</label>
                <input type="number" value={driverId} onChange={e => setDriverId(e.target.value)} placeholder="optional"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Vehicle ID</label>
                <input type="number" value={vehicleId} onChange={e => setVehicleId(e.target.value)} placeholder="optional"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 transition-all" />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Trip KM</label>
                <input type="number" value={tripKm} onChange={e => setTripKm(e.target.value)} placeholder="auto"
                  className="w-full px-3 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-emerald-500/50 transition-all" />
              </div>
            </div>
            <button onClick={handlePredict} disabled={!canPredict || loading}
              className={`w-full py-3 rounded-xl font-semibold text-sm transition-all flex items-center justify-center gap-2 ${
                canPredict ? 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20' : 'bg-gray-800 text-gray-500 cursor-not-allowed'
              }`}>
              {loading ? <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Assessing...</>
                : <><ShieldCheck className="w-4 h-4" /> Assess SLA Risk</>}
            </button>
          </div>

          {error && <div className="bg-red-900/30 border border-red-800/50 rounded-xl p-4 animate-scale-in"><p className="text-sm text-red-400">{error}</p></div>}

          {result && risk && (
            <ResultCard title="SLA Assessment" gradient="from-emerald-900/30 to-teal-900/30" border="border-emerald-700/30">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                {/* Probability Gauge */}
                <div className="md:col-span-2 bg-gray-900/60 rounded-lg p-5 border border-gray-800 text-center">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">On-Time Probability</p>
                  <p className={`text-5xl font-bold animate-count-up ${prob >= 70 ? 'text-emerald-400' : prob >= 40 ? 'text-amber-400' : 'text-red-400'}`}>
                    {prob != null ? `${(prob * 100).toFixed(1)}%` : '-'}
                  </p>
                  <div className="w-full bg-gray-800 rounded-full h-3 mt-3 overflow-hidden">
                    <div className={`h-full rounded-full animate-progress-fill transition-all ${prob >= 0.7 ? 'bg-emerald-500' : prob >= 0.4 ? 'bg-amber-500' : 'bg-red-500'}`}
                      style={{ width: `${(prob || 0) * 100}%` }} />
                  </div>
                </div>
                {/* Risk Level */}
                <div className={`${risk.bg} rounded-lg p-5 border ${risk.border} flex flex-col items-center justify-center`}>
                  {result.prediction === 'yes' ? <CheckCircle2 className={`w-10 h-10 ${risk.text} mb-2`} /> : <XCircle className={`w-10 h-10 ${risk.text} mb-2`} />}
                  <p className={`text-lg font-bold ${risk.text}`}>{risk.label}</p>
                  <p className="text-xs text-gray-500 mt-1">{result.prediction === 'yes' ? 'Likely on time' : 'May be delayed'}</p>
                </div>
              </div>
              {/* Contributing Factors */}
              {result.contributing_factors && Object.keys(result.contributing_factors).length > 0 && (
                <div className="bg-gray-900/60 rounded-lg p-4 border border-gray-800">
                  <p className="text-xs text-gray-500 uppercase tracking-wider mb-3">Contributing Factors</p>
                  <div className="space-y-2">
                    {Object.entries(result.contributing_factors).map(([k, v]) => (
                      <div key={k} className="flex items-start gap-2 text-sm">
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
                        <div>
                          <span className="text-gray-400 capitalize">{k.replace(/_/g, ' ')}: </span>
                          <span className="text-gray-300">{String(v)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
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
              <div className="flex justify-between"><span>Algorithm</span><span className="text-gray-300">XGBoost Classifier</span></div>
              <div className="flex justify-between"><span>Target</span><span className="text-gray-300">eta_met (binary)</span></div>
              <div className="flex justify-between"><span>Output</span><span className="text-gray-300">Probability + Risk Level</span></div>
              <div className="flex justify-between"><span>Endpoint</span><span className="text-gray-300 font-mono">POST /ml/predict/sla</span></div>
            </div>
          </div>
          <div className="animate-fade-in-up stagger-5 bg-gradient-to-br from-emerald-900/20 to-teal-900/20 rounded-xl border border-emerald-800/30 p-5">
            <h3 className="text-sm font-semibold text-emerald-300 mb-2">Risk Level Guide</h3>
            <div className="space-y-2 text-xs">
              {Object.entries(RISK_STYLES).map(([k, v]) => (
                <div key={k} className={`flex items-center gap-2 px-2 py-1.5 rounded-lg ${v.bg} border ${v.border}`}>
                  <span className={`w-2 h-2 rounded-full ${v.text.replace('text-', 'bg-')}`} />
                  <span className={v.text}>{v.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
