import { backendApi } from './api';

export const listVehicles = (params: Record<string, any>) => backendApi.get('/vehicles', { params });
export const getVehicleDetail = (id: number) => backendApi.get(`/vehicles/${id}`);
export const getVehicleTrips = (id: number, params: Record<string, any>) => backendApi.get(`/vehicles/${id}/trips`, { params });
