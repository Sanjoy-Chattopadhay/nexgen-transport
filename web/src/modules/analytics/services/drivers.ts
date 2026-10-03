import { backendApi } from './api';

export const listDrivers = (params: Record<string, any>) => backendApi.get('/drivers', { params });
export const getDriverDetail = (id: number) => backendApi.get(`/drivers/${id}`);
export const getDriverTrips = (id: number, params: Record<string, any>) => backendApi.get(`/drivers/${id}/trips`, { params });
export const getDriverTrend = (id: number, params?: { date_from?: string; date_to?: string; group_by?: string }) =>
  backendApi.get(`/drivers/${id}/trend`, { params });
export const getDriverDrivingPattern = (id: number, window = 'all') =>
  backendApi.get(`/drivers/${id}/driving-pattern`, { params: { window } });
export const getDriverAlerts = (id: number, window = 'all') =>
  backendApi.get(`/drivers/${id}/alerts`, { params: { window } });
