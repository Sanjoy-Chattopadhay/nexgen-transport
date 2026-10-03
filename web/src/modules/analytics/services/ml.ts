import { mlApi } from './api';

// ── Predictions ──
export const predictEta = (data: any) => mlApi.post('/ml/predict/eta', data);
export const predictSla = (data: any) => mlApi.post('/ml/predict/sla', data);
export const scanAnomalies = (days = 7) => mlApi.post(`/ml/scan/anomalies?days=${days}`);
export const recommendDrivers = (data: { origin: string; destination: string; top_n?: number }) =>
  mlApi.post('/ml/recommend/drivers', data);
export const forecastTrips = (route?: string) =>
  mlApi.get('/ml/forecast/trips', { params: route ? { route } : {} });

// ── Driver scoring & fatigue ──
export const getDriverScores = (limit = 100) => mlApi.get(`/ml/drivers/scores?limit=${limit}`);
export const getDriverScore = (id: number) => mlApi.get(`/ml/drivers/${id}/score`);
export const getFleetFatigue = () => mlApi.get('/ml/drivers/fatigue');
export const getDriverFatigue = (id: number) => mlApi.get(`/ml/drivers/${id}/fatigue`);

// ── Demand & route ──
export const getDemandForecast = (route?: string) =>
  mlApi.get('/ml/forecast/demand', { params: route ? { route } : {} });
export const optimizeRoute = (data: any) => mlApi.post('/ml/optimize/route', data);
export const getHubLocations = () => mlApi.get('/ml/optimize/hubs');

// ── Client demand forecasting ──
export const getClients = () => mlApi.get('/ml/clients');
export const getClientForecast = (client?: string) =>
  mlApi.get('/ml/clients/forecast', { params: client ? { client } : {} });
export const getClientProfile = (name: string) => mlApi.get(`/ml/clients/${encodeURIComponent(name)}/profile`);

// ── Model management ──
export const listModels = () => mlApi.get('/ml/models');
export const getModelComparison = () => mlApi.get('/ml/models/comparison');
export const trainModel = (name: string) => mlApi.post(`/ml/train/${name}`);
export const trainAllModels = () => mlApi.post('/ml/train-all');
export const trainTier = (tier: string) => mlApi.post(`/ml/train-tier/${tier}`);
export const checkTrainingReadiness = () => mlApi.get('/ml/training/readiness');
export const clearModelCache = () => mlApi.post('/ml/cache/clear');
