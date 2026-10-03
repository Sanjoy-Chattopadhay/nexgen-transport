import { backendApi } from './api';

export const listRoutes = (params: Record<string, any>) => backendApi.get('/routes', { params });
export const getRouteDetail = (origin: string, destination: string) => backendApi.get('/routes/detail', { params: { origin, destination } });
