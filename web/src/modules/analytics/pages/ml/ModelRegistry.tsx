import { useState } from 'react';
import { Settings, BookOpen, Cpu, RefreshCw, Trash2, Play, Layers, CheckCircle2, XCircle, Clock, Zap } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import ModelPageHeader from '../../components/ml/ModelPageHeader';
import InfoCard from '../../components/ml/InfoCard';
import { useApi } from '../../hooks/useApi';
import { listModels, trainModel, trainAllModels, trainTier, clearModelCache, checkTrainingReadiness } from '../../services/ml';

const TIER_INFO: Record<string, { label: string; models: string[]; color: string }> = {
  daily: { label: 'Daily', models: ['driver_scorer', 'driver_recommender', 'fatigue_predictor'], color: 'text-emerald-400' },
  weekly: { label: 'Weekly', models: ['eta_predictor', 'anomaly_detector', 'demand_forecaster', 'sla_predictor', 'client_demand_forecaster'], color: 'text-blue-400' },
  monthly: { label: 'Monthly', models: ['All 9 models'], color: 'text-purple-400' },
};

export default function ModelRegistry() {
  const { data: modelsData, loading, refetch } = useApi<any>(() => listModels().then(r => r.data), []);
  const [trainLoading, setTrainLoading] = useState<string | null>(null);
  const [tierLoading, setTierLoading] = useState<string | null>(null);
  const [allLoading, setAllLoading] = useState(false);
  const [cacheLoading, setCacheLoading] = useState(false);
  const [readiness, setReadiness] = useState<any>(null);

  const models = modelsData?.models || (Array.isArray(modelsData) ? modelsData : []);

  const handleTrain = async (name: string) => {
    setTrainLoading(name);
    try { await trainModel(name); await new Promise(r => setTimeout(r, 2000)); refetch(); } catch { /* */ }
    setTrainLoading(null);
  };

  const handleTrainAll = async () => {
    setAllLoading(true);
    try { await trainAllModels(); await new Promise(r => setTimeout(r, 3000)); refetch(); } catch { /* */ }
    setAllLoading(false);
  };

  const handleTrainTier = async (tier: string) => {
    setTierLoading(tier);
    try { await trainTier(tier); await new Promise(r => setTimeout(r, 2000)); refetch(); } catch { /* */ }
    setTierLoading(null);
  };

  const handleClearCache = async () => {
    setCacheLoading(true);
    try { await clearModelCache(); } catch { /* */ }
    setCacheLoading(false);
  };

  const handleCheckReadiness = async () => {
    try { const r = await checkTrainingReadiness(); setReadiness(r.data); } catch { /* */ }
  };

  const fmtDate = (d: string) => {
    if (!d) return '-';
    try { return new Date(d).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
    catch { return d; }
  };

  return (
    <PageContainer title="">
      <ModelPageHeader
        title="Model Registry"
        subtitle="View all trained ML models, trigger retraining, manage cache, and monitor training readiness. The control centre for every model in NexGen Transport."
        icon={Settings} iconColor="text-gray-300"
        gradientFrom="from-gray-700/20" gradientTo="to-gray-600/20" accentBorder="border-gray-600/30"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-5">
          {/* Actions */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-1">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Zap className="w-4 h-4 text-amber-400" /> Training Controls
            </h2>
            <div className="flex flex-wrap gap-3">
              <button onClick={handleTrainAll} disabled={allLoading}
                className="px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition-all flex items-center gap-2 disabled:opacity-50 shadow-lg shadow-blue-600/20">
                {allLoading ? <><div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Training All...</>
                  : <><Play className="w-3.5 h-3.5" /> Train All Models</>}
              </button>

              {Object.entries(TIER_INFO).map(([tier, info]) => (
                <button key={tier} onClick={() => handleTrainTier(tier)} disabled={tierLoading !== null}
                  className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-white rounded-lg text-sm font-medium transition-all flex items-center gap-2 disabled:opacity-50 border border-gray-700">
                  {tierLoading === tier ? <div className="w-3.5 h-3.5 border-2 border-gray-400/30 border-t-gray-400 rounded-full animate-spin" />
                    : <Layers className="w-3.5 h-3.5" />}
                  <span>{info.label} Tier</span>
                </button>
              ))}

              <button onClick={handleClearCache} disabled={cacheLoading}
                className="px-4 py-2.5 bg-red-900/40 hover:bg-red-900/60 text-red-400 rounded-lg text-sm font-medium transition-all flex items-center gap-2 disabled:opacity-50 border border-red-800/50">
                {cacheLoading ? <div className="w-3.5 h-3.5 border-2 border-red-400/30 border-t-red-400 rounded-full animate-spin" />
                  : <Trash2 className="w-3.5 h-3.5" />}
                Clear Cache
              </button>

              <button onClick={handleCheckReadiness}
                className="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg text-sm font-medium transition-all flex items-center gap-2 border border-gray-700">
                <CheckCircle2 className="w-3.5 h-3.5" /> Check Readiness
              </button>
            </div>

            {readiness && (
              <div className="mt-4 bg-gray-800/50 rounded-lg p-3 text-xs text-gray-400 animate-scale-in">
                <pre className="whitespace-pre-wrap">{JSON.stringify(readiness, null, 2)}</pre>
              </div>
            )}
          </div>

          {/* Training Tiers */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 animate-fade-in-up stagger-2">
            <h2 className="text-base font-semibold text-white mb-4 flex items-center gap-2">
              <Layers className="w-4 h-4 text-purple-400" /> Training Tiers
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {Object.entries(TIER_INFO).map(([tier, info]) => (
                <div key={tier} className="bg-gray-800/50 rounded-lg p-4 border border-gray-700/50">
                  <h3 className={`text-sm font-semibold ${info.color} mb-2`}>{info.label}</h3>
                  <ul className="space-y-1">
                    {info.models.map(m => (
                      <li key={m} className="text-xs text-gray-400 flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-gray-600" />{m.replace(/_/g, ' ')}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>

          {/* Model List */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden animate-fade-in-up stagger-3">
            <div className="px-5 py-4 border-b border-gray-800">
              <h2 className="text-base font-semibold text-white flex items-center gap-2">
                <Cpu className="w-4 h-4 text-blue-400" /> Trained Models ({models.length})
              </h2>
            </div>
            {loading ? (
              <div className="p-8 text-center"><div className="w-6 h-6 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin mx-auto" /></div>
            ) : (
              <div className="divide-y divide-gray-800/50">
                {models.map((m: any, i: number) => (
                  <div key={m.id || m.model_name} className={`px-5 py-4 animate-fade-in stagger-${Math.min(i + 1, 9)}`}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-3">
                        <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${m.is_active ? 'bg-emerald-500/20 border border-emerald-500/30' : 'bg-gray-800 border border-gray-700'}`}>
                          {m.is_active ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <XCircle className="w-4 h-4 text-gray-500" />}
                        </div>
                        <div>
                          <p className="text-sm font-medium text-white">{m.model_name?.replace(/_/g, ' ')}</p>
                          <p className="text-xs text-gray-500">v{m.version} | {m.model_type}</p>
                        </div>
                      </div>
                      <button onClick={() => handleTrain(m.model_name)} disabled={trainLoading !== null}
                        className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 disabled:opacity-50 border border-gray-700">
                        {trainLoading === m.model_name ? <div className="w-3 h-3 border-2 border-gray-400/30 border-t-gray-400 rounded-full animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                        Retrain
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 ml-11">
                      {m.trained_at && <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {fmtDate(m.trained_at)}</span>}
                      {m.training_data_count > 0 && <span>{m.training_data_count.toLocaleString()} rows</span>}
                      {m.target_variable && <span>Target: {m.target_variable}</span>}
                    </div>
                    {m.metrics && Object.keys(m.metrics).length > 0 && (
                      <div className="flex flex-wrap gap-2 mt-2 ml-11">
                        {Object.entries(m.metrics).slice(0, 5).map(([k, v]) => (
                          <span key={k} className="text-xs px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">
                            {k}: {typeof v === 'number' ? (v as number).toFixed(4) : String(v)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {models.length === 0 && (
                  <div className="p-8 text-center text-gray-500 text-sm">No models trained yet. Click "Train All Models" to get started.</div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-5">
          <div className="animate-fade-in-up stagger-3">
            <InfoCard title="How Training Works" icon={BookOpen} iconColor="text-blue-400" defaultOpen={true}>
              <p className="mt-2">Models are organized into <strong className="text-gray-300">training tiers</strong> based on how often they need updating:</p>
              <ul className="list-disc list-inside space-y-1 mt-2 text-xs">
                <li><strong className="text-emerald-400">Daily</strong> — Driver scoring, recommendations, fatigue</li>
                <li><strong className="text-blue-400">Weekly</strong> — ETA, SLA, anomaly detection, demand</li>
                <li><strong className="text-purple-400">Monthly</strong> — Full retraining of all models</li>
              </ul>
              <p className="mt-3 text-xs text-gray-500">Cache is auto-cleared after training. Use "Clear Cache" to force model reload without retraining.</p>
            </InfoCard>
          </div>

          <div className="animate-fade-in-up stagger-4 bg-gradient-to-br from-blue-900/20 to-indigo-900/20 rounded-xl border border-blue-800/30 p-5">
            <h3 className="text-sm font-semibold text-blue-300 mb-2">API Reference</h3>
            <div className="space-y-2 text-xs text-gray-500">
              <div className="flex justify-between"><span>List Models</span><span className="text-gray-300 font-mono">GET /ml/models</span></div>
              <div className="flex justify-between"><span>Train One</span><span className="text-gray-300 font-mono">POST /ml/train/{'{name}'}</span></div>
              <div className="flex justify-between"><span>Train All</span><span className="text-gray-300 font-mono">POST /ml/train-all</span></div>
              <div className="flex justify-between"><span>Train Tier</span><span className="text-gray-300 font-mono">POST /ml/train-tier/{'{tier}'}</span></div>
              <div className="flex justify-between"><span>Clear Cache</span><span className="text-gray-300 font-mono">POST /ml/cache/clear</span></div>
              <div className="flex justify-between"><span>Readiness</span><span className="text-gray-300 font-mono">GET /ml/training/readiness</span></div>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
