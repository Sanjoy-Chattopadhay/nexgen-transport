import { useNavigate } from 'react-router-dom';
import { Clock, ShieldCheck, AlertTriangle, Users, Brain, TrendingUp, Route, Building2, Gauge, Settings, Sparkles } from 'lucide-react';
import PageContainer from '../../components/layout/PageContainer';
import { useApi } from '../../hooks/useApi';
import { listModels } from '../../services/ml';

interface ModelCardDef {
  key: string;
  title: string;
  desc: string;
  icon: any;
  color: string;
  gradient: string;
  border: string;
  path: string;
  category: 'prediction' | 'monitoring' | 'optimization';
}

const MODELS: ModelCardDef[] = [
  {
    key: 'eta_predictor', title: 'ETA Predictor', path: '/ml/eta',
    desc: 'Predict trip duration using 22 features — route history, driver performance, time patterns, and vehicle stats.',
    icon: Clock, color: 'text-blue-400', gradient: 'from-blue-600/15 to-indigo-600/15', border: 'border-blue-500/20',
    category: 'prediction',
  },
  {
    key: 'sla_predictor', title: 'SLA Predictor', path: '/ml/sla',
    desc: 'Assess on-time delivery probability and risk level before dispatch. Helps operations prioritize critical shipments.',
    icon: ShieldCheck, color: 'text-emerald-400', gradient: 'from-emerald-600/15 to-teal-600/15', border: 'border-emerald-500/20',
    category: 'prediction',
  },
  {
    key: 'anomaly_detector', title: 'Anomaly Scanner', path: '/ml/anomaly',
    desc: 'Batch-scan recent trips to detect unusual duration, speed, or route deviations. Auto-creates alerts for review.',
    icon: AlertTriangle, color: 'text-amber-400', gradient: 'from-amber-600/15 to-orange-600/15', border: 'border-amber-500/20',
    category: 'monitoring',
  },
  {
    key: 'driver_scorer', title: 'Driver Scorer', path: '/ml/driver-scorer',
    desc: 'Composite scoring system for driver performance — punctuality, speed consistency, safety, and trip completion.',
    icon: Gauge, color: 'text-purple-400', gradient: 'from-purple-600/15 to-fuchsia-600/15', border: 'border-purple-500/20',
    category: 'monitoring',
  },
  {
    key: 'fatigue_predictor', title: 'Fatigue Monitor', path: '/ml/fatigue',
    desc: 'Real-time driver fatigue risk assessment based on driving hours, rest periods, consecutive days, and night trips.',
    icon: Brain, color: 'text-red-400', gradient: 'from-red-600/15 to-rose-600/15', border: 'border-red-500/20',
    category: 'monitoring',
  },
  {
    key: 'driver_recommender', title: 'Driver Recommender', path: '/ml/recommender',
    desc: 'Find the best driver for any route — Bayesian-weighted scoring of route experience, ETA success, and consistency.',
    icon: Users, color: 'text-cyan-400', gradient: 'from-cyan-600/15 to-sky-600/15', border: 'border-cyan-500/20',
    category: 'optimization',
  },
  {
    key: 'demand_forecaster', title: 'Demand Forecaster', path: '/ml/demand',
    desc: '7-day trip volume forecasting per route. Helps plan fleet capacity and driver allocation ahead of demand.',
    icon: TrendingUp, color: 'text-indigo-400', gradient: 'from-indigo-600/15 to-violet-600/15', border: 'border-indigo-500/20',
    category: 'prediction',
  },
  {
    key: 'route_optimizer', title: 'Route Optimizer', path: '/ml/route-optimizer',
    desc: 'Network analysis to identify optimal routes, hub locations, and efficiency scores for origin-destination pairs.',
    icon: Route, color: 'text-amber-400', gradient: 'from-amber-600/15 to-yellow-600/15', border: 'border-amber-500/20',
    category: 'optimization',
  },
  {
    key: 'client_demand_forecaster', title: 'Client Forecast', path: '/ml/client-forecast',
    desc: 'Per-client demand trends and 7-day forecasts. Understand seasonal patterns and growth for key accounts.',
    icon: Building2, color: 'text-teal-400', gradient: 'from-teal-600/15 to-emerald-600/15', border: 'border-teal-500/20',
    category: 'prediction',
  },
];

const CATEGORIES = [
  { key: 'prediction', label: 'Prediction Models', icon: Sparkles, desc: 'Forecast future outcomes' },
  { key: 'monitoring', label: 'Monitoring Models', icon: AlertTriangle, desc: 'Detect issues in real-time' },
  { key: 'optimization', label: 'Optimization Models', icon: Route, desc: 'Improve decisions' },
];

export default function MLHub() {
  const navigate = useNavigate();
  const { data: modelsData } = useApi<any>(() => listModels().then(r => r.data), []);
  const models = modelsData?.models || (Array.isArray(modelsData) ? modelsData : []);

  const getModel = (key: string) => models.find((m: any) => m.model_name === key);

  const fmtDate = (d: string) => {
    try { return new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }); }
    catch { return '-'; }
  };

  return (
    <PageContainer title="">
      {/* Hero */}
      <div className="animate-fade-in-up mb-8">
        <div className="bg-gradient-to-r from-blue-600/15 via-purple-600/10 to-pink-600/10 rounded-2xl border border-blue-500/20 p-8 relative overflow-hidden">
          <Brain className="absolute right-8 top-1/2 -translate-y-1/2 w-40 h-40 text-blue-400 opacity-[0.05] animate-float" />
          <div className="relative z-10">
            <div className="flex items-center gap-3 mb-2">
              <div className="w-12 h-12 bg-blue-600/20 rounded-xl flex items-center justify-center border border-blue-500/30">
                <Brain className="w-6 h-6 text-blue-400" />
              </div>
              <div>
                <h1 className="text-3xl font-bold text-white">ML Intelligence Hub</h1>
                <p className="text-sm text-gray-400">9 machine learning models behind the fleet's planning and monitoring</p>
              </div>
            </div>
            <div className="flex items-center gap-6 mt-4">
              <div className="text-center">
                <p className="text-2xl font-bold text-blue-400">{models.length}</p>
                <p className="text-xs text-gray-500 uppercase tracking-wider">Models</p>
              </div>
              <div className="w-px h-8 bg-gray-700" />
              <div className="text-center">
                <p className="text-2xl font-bold text-emerald-400">{models.filter((m: any) => m.is_active).length}</p>
                <p className="text-xs text-gray-500 uppercase tracking-wider">Active</p>
              </div>
              <div className="w-px h-8 bg-gray-700" />
              <button onClick={() => navigate('/ml/models')}
                className="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-sm text-gray-300 transition-colors">
                <Settings className="w-4 h-4" /> Model Registry
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Model Categories */}
      {CATEGORIES.map((cat, ci) => {
        const catModels = MODELS.filter(m => m.category === cat.key);
        return (
          <div key={cat.key} className={`mb-8 animate-fade-in-up stagger-${ci + 2}`}>
            <div className="flex items-center gap-2 mb-4">
              <cat.icon className="w-4 h-4 text-gray-500" />
              <h2 className="text-lg font-semibold text-white">{cat.label}</h2>
              <span className="text-xs text-gray-600 ml-1">— {cat.desc}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {catModels.map((m, i) => {
                const trained = getModel(m.key);
                const topMetric = trained?.metrics ? Object.entries(trained.metrics)[0] : null;
                return (
                  <button key={m.key} onClick={() => navigate(m.path)}
                    className={`group text-left bg-gradient-to-br ${m.gradient} rounded-xl border ${m.border} p-5 hover:scale-[1.02] hover:shadow-lg hover:shadow-black/20 transition-all duration-200 animate-fade-in-up stagger-${i + 1}`}>
                    <div className="flex items-start justify-between mb-3">
                      <div className={`w-10 h-10 rounded-lg bg-gray-900/60 border border-gray-700/50 flex items-center justify-center group-hover:scale-110 transition-transform`}>
                        <m.icon className={`w-5 h-5 ${m.color}`} />
                      </div>
                      {trained ? (
                        <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/20">
                          v{trained.version}
                        </span>
                      ) : (
                        <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-gray-700/40 text-gray-500 border border-gray-700/30">
                          Not trained
                        </span>
                      )}
                    </div>
                    <h3 className="text-base font-semibold text-white mb-1 group-hover:text-blue-300 transition-colors">{m.title}</h3>
                    <p className="text-xs text-gray-400 leading-relaxed mb-3">{m.desc}</p>
                    {trained && (
                      <div className="flex items-center gap-3 text-xs text-gray-500 pt-2 border-t border-gray-700/30">
                        {topMetric && (
                          <span>{topMetric[0]}: <span className="text-gray-300 font-medium">{typeof topMetric[1] === 'number' ? topMetric[1].toFixed(2) : String(topMetric[1])}</span></span>
                        )}
                        {trained.trained_at && <span>Trained: {fmtDate(trained.trained_at)}</span>}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </PageContainer>
  );
}
