/**
 * The API client. The UI is served by the same process as the API, so every
 * call is same-origin and there is no base URL to configure; in development
 * the Vite server proxies the same paths.
 */

export type Params = Record<string, string | number | boolean | null | undefined>;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function qs(params?: Params): string {
  if (!params) return '';
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : '';
}

export async function get<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${path}${qs(params)}`, { signal });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => get<any>('/api/v1/geo/status'),
  requestRefresh: () => post<any>('/api/v1/geo/refresh'),
  jobs: (limit = 30) => get<any>('/api/v1/geo/jobs', { limit }),
  drill: (dataset: string, p: Params) => get<any>(`/api/v1/geo/drill/${dataset}`, p),
  days: () => get<any>('/api/v1/geo/days'),
  daysHourly: () => get<any>('/api/v1/geo/days/hourly'),
  day: (d: string) => get<any>(`/api/v1/geo/days/${d}`),
  geofences: (p: Params) => get<any>('/api/v1/geo/geofences', p),
  geofenceFacets: () => get<any>('/api/v1/geo/geofences/facets'),
  stateSummary: () => get<any>('/api/v1/geo/states'),
  tolls: (p: Params) => get<any>('/api/v1/geo/tolls', p),
  geofence: (id: string | number) => get<any>(`/api/v1/geo/geofences/${id}`),
  geofenceActivity: (id: string | number, p?: Params) => get<any>(`/api/v1/geo/geofences/${id}/activity`, p),
  geofenceVisits: (id: string | number, p: Params) => get<any>(`/api/v1/geo/geofences/${id}/visits`, p),
  geofencesNear: (lat: number, lon: number, radius_m = 1500) =>
    get<any>('/api/v1/geo/geofences-near', { lat, lon, radius_m }),
  rings: (ids: number[]) => get<any>('/api/v1/geo/geofences-rings', { ids: ids.join(',') }),
  plants: (p?: Params) => get<any>('/api/v1/geo/plants', p),
  plantScenes: (p: Params) => get<any>('/api/v1/geo/plants/scenes', p),
  plant: (siteId: string | number, p?: Params) => get<any>(`/api/v1/geo/plants/${siteId}`, p),
  plantInside: (siteId: string | number, at?: string) => get<any>(`/api/v1/geo/plants/${siteId}/inside`, { at }),
  trips: (p: Params) => get<any>('/api/v1/geo/trips', p),
  trip: (no: string | number) => get<any>(`/api/v1/geo/trips/${no}`),
  tripTrack: (no: string | number) => get<any>(`/api/v1/geo/trips/${no}/track`),
  tripReport: (no: string | number) => get<any>(`/api/v1/geo/trips/${no}/report`),
  routes: (p: Params) => get<any>('/api/v1/geo/routes', p),
  routeLanes: (p: Params) => get<any>('/api/v1/geo/routes/lanes', p),
  routeTrip: (no: string | number) => get<any>(`/api/v1/geo/routes/trip/${no}`),
  routeSettings: () => get<any>('/api/v1/geo/routes/settings'),
  vehicles: (p: Params) => get<any>('/api/v1/geo/vehicles', p),
  vehicle: (id: string) => get<any>(`/api/v1/geo/vehicles/${encodeURIComponent(id)}`),
  transporters: (p: Params) => get<any>('/api/v1/geo/transporters', p),
  transporter: (name: string) => get<any>('/api/v1/geo/transporters/detail', { name }),
  lanes: (p: Params) => get<any>('/api/v1/geo/lanes', p),
  lane: (origin: string, destination: string) => get<any>('/api/v1/geo/lanes/detail', { origin, destination }),
  drivers: (p: Params) => get<any>('/api/v1/geo/drivers', p),
  driver: (name: string) => get<any>('/api/v1/geo/drivers/detail', { name }),
  alerts: (p: Params) => get<any>('/api/v1/geo/alerts', p),
  stops: (p: Params) => get<any>('/api/v1/geo/stops', p),
  quality: () => get<any>('/api/v1/geo/quality'),
  runs: () => get<any>('/api/v1/geo/runs'),
  compare: (a: number, b: number) => get<any>(`/api/v1/geo/runs/${a}/compare/${b}`),
  osrmHealth: () => get<any>('/api/v1/geo/osrm/health'),
  // `v` moves the base map off the URLs browsers cached for a day under the
  // old policy, when the files held geometry Leaflet could not read.
  states: () => get<any>('/api/v1/geo/map/states', { v: 2 }),
  districts: () => get<any>('/api/v1/geo/map/districts', { v: 2 }),
  fenceRingsInView: (bbox: number[], limit = 800) =>
    get<any>('/api/v1/geo/map/fences', { bbox: bbox.map(v => v.toFixed(5)).join(','), limit }),
  fenceSummary: () => get<any>('/api/v1/geo/map/fences/summary'),
  liveVehicles: () => get<any>('/api/v1/geo/live/vehicles'),
  liveStats: () => get<any>('/api/v1/geo/live/stats'),
  liveEvents: (afterId: number) => get<any>('/api/v1/geo/live/events', { after_id: afterId, limit: 150 }),
  liveTrail: (asset: string, minutes = 240) =>
    get<any>(`/api/v1/geo/live/vehicle/${encodeURIComponent(asset)}/trail`, { minutes }),

  // Uploaded trips. `upload` posts multipart and reports progress, so it does
  // not go through `get`.
  uploads: () => get<any>('/api/v1/geo/uploads'),
  upload: (id: number | string, rows = 200) => get<any>(`/api/v1/geo/uploads/${id}`, { rows }),
  uploadStep: (id: number | string, key: string, page: number, page_size = 200) =>
    get<any>(`/api/v1/geo/uploads/${id}/steps/${key}`, { page, page_size }),
  uploadTrack: (id: number | string) => get<any>(`/api/v1/geo/uploads/${id}/track`),
  uploadIndexMap: (id: number | string, level?: number) =>
    get<any>(`/api/v1/geo/uploads/${id}/index-map`, { level }),
  uploadSample: (km = 500) => get<any>('/api/v1/geo/uploads/sample', { km }),
  uploadReanalyse: (id: number | string, params: Params) =>
    post<any>(`/api/v1/geo/uploads/${id}/reanalyse${qs(params)}`),
  uploadDelete: (id: number | string) => del<any>(`/api/v1/geo/uploads/${id}`),
};

export const tripUrls = {
  report: (no: string | number, fmt: 'csv' | 'xlsx') => `/api/v1/geo/trips/${no}/report.${fmt}`,
};

export const uploadUrls = {
  sample: (km = 500) => `/api/v1/geo/uploads/sample.xlsx${qs({ km })}`,
  step: (id: number | string, key: string, fmt: 'csv' | 'json' | 'xlsx') =>
    `/api/v1/geo/uploads/${id}/download/${key}.${fmt}`,
  pings: (id: number | string) => `/api/v1/geo/uploads/${id}/download/pings.csv`,
  report: (id: number | string) => `/api/v1/geo/uploads/${id}/report.xlsx`,
};

async function send<T>(path: string, method: string, body?: BodyInit): Promise<T> {
  const res = await fetch(path, { method, body });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: BodyInit) => send<T>(path, 'POST', body);
const del = <T,>(path: string) => send<T>(path, 'DELETE');

/**
 * Upload a spreadsheet. XMLHttpRequest rather than fetch: a 4,500-fix workbook
 * takes a moment to send and the page has to be able to show how far it has
 * got, which fetch cannot report for a request body.
 */
export function uploadFile(file: File, label: string, onProgress?: (pct: number) => void) {
  const form = new FormData();
  form.append('file', file);
  if (label) form.append('label', label);
  return new Promise<any>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/v1/geo/uploads');
    xhr.upload.onprogress = e => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((100 * e.loaded) / e.total));
    };
    xhr.onload = () => {
      let body: any = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, body?.detail || xhr.statusText || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new ApiError(0, 'the upload did not reach the server'));
    xhr.send(form);
  });
}
