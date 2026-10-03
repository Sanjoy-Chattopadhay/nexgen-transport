import { backendApi } from './api';
import type { TTAStatus, TTATripList, TTATripDetail, TTAGpsResponse, TTAUploadResult } from '../types/tta';

export const createTTASchema = () => backendApi.post('/tta/schema');

export const uploadTTAFile = (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return backendApi.post<TTAUploadResult>('/tta/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 300000,
  });
};

export const getTTAStatus = () => backendApi.get<TTAStatus>('/tta/status');
export const getTTAProgress = () => backendApi.get('/tta/progress');

export const getTTATrips = (page = 1, pageSize = 25, search = '') =>
  backendApi.get<TTATripList>('/tta/trips', { params: { page, page_size: pageSize, search } });

export const getTTATrip = (tripNo: number | string) =>
  backendApi.get<TTATripDetail>(`/tta/trips/${tripNo}`);

export const getTTATripGps = (tripNo: number | string, maxPoints = 2000) =>
  backendApi.get<TTAGpsResponse>(`/tta/trips/${tripNo}/gps`, { params: { max_points: maxPoints } });

export const getTTATripAnalysis = (tripNo: number | string) =>
  backendApi.get<any>(`/tta/trips/${tripNo}/analysis`, { timeout: 120000 });

export const getTTATripWeather = (tripNo: number | string) =>
  backendApi.get<any>(`/tta/trips/${tripNo}/weather`, { timeout: 180000 });

export const getTTAPlantDelay = (tripNo: number | string, insights = true) =>
  backendApi.get<any>(`/tta/trips/${tripNo}/plant-delay`, {
    params: { insights },
    timeout: 120000,
  });

export const compareTTATrips = (tripNos: (number | string)[]) =>
  backendApi.get<any>('/tta/compare', { params: { trips: tripNos.join(',') }, timeout: 120000 });

export const getTTANetworkWaypoints = (limit = 25) =>
  backendApi.get<any>('/tta/analytics/waypoints', { params: { limit } });

export const getTTAHeatmap = (mode: 'all' | 'stops') =>
  backendApi.get<any>('/tta/analytics/heatmap', { params: { mode }, timeout: 60000 });

export const getTTAWaypointHours = (topN = 12) =>
  backendApi.get<any>('/tta/analytics/waypoint-hours', { params: { top_n: topN } });

export const getTTAStates = () => backendApi.get<any>('/tta/analytics/states');

export const getTTAMaintenanceStatus = () => backendApi.get<any>('/tta/maintenance/status');

export interface CostConfig {
  fuel_price_per_liter: number;
  fuel_efficiency_kmpl: number;
  driver_wage_per_hour: number;
  idle_fuel_consumption_lph: number;
}
export const getCostConfig = () => backendApi.get<CostConfig>('/tta/config/cost');
export const saveCostConfig = (cfg: CostConfig) => backendApi.post<CostConfig>('/tta/config/cost', cfg);

/**
 * Every break the truck took on a trip, named and totalled.
 *
 * Replaces the raw ping dump on the trip page. `weather=false` skips the
 * Open-Meteo lookup; the halt taxonomy does not depend on it.
 */
export const getTTATripBreaks = (tripNo: number | string, weather = true) =>
  backendApi.get<any>(`/tta/trips/${tripNo}/breaks`, { params: { weather } });
