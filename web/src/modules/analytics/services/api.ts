import axios from 'axios';
import { parseConsignorPath, CONSIGNOR_PARAM } from '../lib/consignor';
import { TRIP_CLASS_PARAM, readStoredTripClass, type TripClassValue } from '../lib/tripClass';

// VITE_API_URL / VITE_ML_URL are injected by vite.config.ts, derived from the
// root .env ports (API_PORT / ML_SERVICE_PORT). The literals below are only a
// last-resort fallback and must match the default ports in those configs.
export const backendApi = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api/v1',
  timeout: 30000,
});

export const mlApi = axios.create({
  baseURL: import.meta.env.VITE_ML_URL || '/api/v1',
  timeout: 60000,
});

// --------------- Consignor scoping ---------------
// Every backend call is transparently scoped to the active consignor (from the
// URL), so individual services never have to pass it. Empty = all consignors.
let activeConsignorId: string | null = parseConsignorPath().consignorId;

export function setActiveConsignorId(id: string | null) {
  activeConsignorId = id;
}

// --------------- Trip-class scoping ---------------
// The zonal/local filter is applied to every backend call the same way the
// consignor scope is, so no individual service has to know about it. null =
// all classes, and the param is omitted entirely (the backend's default).
// Seeded from localStorage so a page refresh keeps the active filter.
let activeTripClass: TripClassValue = readStoredTripClass();

export function setActiveTripClass(value: TripClassValue) {
  activeTripClass = value;
}

export function getActiveTripClass(): TripClassValue {
  return activeTripClass;
}

backendApi.interceptors.request.use((config) => {
  if (activeConsignorId) {
    config.params = { ...(config.params ?? {}), [CONSIGNOR_PARAM]: activeConsignorId };
  }
  if (activeTripClass) {
    config.params = { ...(config.params ?? {}), [TRIP_CLASS_PARAM]: activeTripClass };
  }
  return config;
});

// --------------- Request / Response Logging ---------------

const TAG_STYLE = 'color:#6366f1;font-weight:bold';
const OK_STYLE = 'color:#22c55e;font-weight:bold';
const ERR_STYLE = 'color:#ef4444;font-weight:bold';
const DIM_STYLE = 'color:#9ca3af';

function attachLogging(instance: ReturnType<typeof axios.create>, label: string) {
  instance.interceptors.request.use((config) => {
    const method = (config.method ?? 'GET').toUpperCase();
    console.log(
      `%c[${label}] %c>>> ${method} %c${config.url}`,
      TAG_STYLE, DIM_STYLE, 'color:inherit',
      config.params ?? '',
    );
    (config as any).__startTime = performance.now();
    return config;
  });

  instance.interceptors.response.use(
    (response) => {
      const ms = Math.round(performance.now() - ((response.config as any).__startTime ?? 0));
      console.log(
        `%c[${label}] %c<<< ${response.status} %c${response.config.url} %c${ms}ms`,
        TAG_STYLE, OK_STYLE, 'color:inherit', DIM_STYLE,
      );
      return response;
    },
    (error) => {
      const ms = error.config
        ? Math.round(performance.now() - ((error.config as any).__startTime ?? 0))
        : 0;
      const status = error.response?.status ?? 'NETWORK_ERROR';
      console.error(
        `%c[${label}] %c!!! ${status} %c${error.config?.url ?? '?'} %c${ms}ms`,
        TAG_STYLE, ERR_STYLE, 'color:inherit', DIM_STYLE,
        error.response?.data ?? error.message,
      );
      return Promise.reject(error);
    },
  );
}

attachLogging(backendApi, 'API');
attachLogging(mlApi, 'ML');
