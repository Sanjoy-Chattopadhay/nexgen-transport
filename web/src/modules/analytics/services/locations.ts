import { backendApi } from './api';

export const getLocations = (search = '', limit = 200) =>
  backendApi.get('/locations', { params: { search, limit } });

export const getRouteStats = (origin: string, destination: string) =>
  backendApi.get('/locations/route-stats', { params: { origin, destination } });
