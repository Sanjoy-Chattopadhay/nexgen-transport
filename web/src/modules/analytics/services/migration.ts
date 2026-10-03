import { backendApi } from './api';

export const createSchema = () => backendApi.post('/migrate/schema');
export const startTripMigration = () => backendApi.post('/migrate/trips');
export const getMigrationProgress = () => backendApi.get('/migrate/progress');
export const migrateWaypoints = () => backendApi.post('/migrate/waypoints');
export const refreshSummaries = () => backendApi.post('/migrate/refresh-summaries');
export const getMigrationStatus = () => backendApi.get('/migrate/status');
