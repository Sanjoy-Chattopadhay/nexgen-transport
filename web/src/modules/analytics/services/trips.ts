import { backendApi } from './api';

export const listTrips = (params: Record<string, any>) => backendApi.get('/trips', { params });
export const getTripStats = () => backendApi.get('/trips/stats');
export const getTripDetail = (id: number) => backendApi.get(`/trips/${id}`);
