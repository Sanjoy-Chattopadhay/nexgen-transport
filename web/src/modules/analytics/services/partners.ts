import { backendApi } from './api';

export interface PartnerKpis {
  trips: number;
  total_km: number | null;
  avg_speed: number | null;
  avg_duration_min: number | null;
  otd_pct: number | null;
  avg_delay_min: number | null;
  destinations: number;
  vehicles: number;
  drivers: number;
  consignees: number;
  first_trip: string | null;
  last_trip: string | null;
}

export interface PartnerDetail {
  kpis: PartnerKpis;
  top_routes: { origin: string; destination: string; trips: number; avg_duration_min: number | null; avg_km: number | null; otd_pct: number | null }[];
  top_vehicles: { vehicle_id: number; asset_id: string; asset_type: string; trips: number; otd_pct: number | null; total_km: number | null }[];
  top_drivers: { driver_id: number; driver_name: string; trips: number; otd_pct: number | null; avg_speed: number | null }[];
  delivery: { status: string; trips: number }[];
  monthly: { month: string; trips: number; otd_pct: number | null; total_km: number | null; avg_speed: number | null }[];
  recent_trips: any[];
}

export interface ConsigneeRow {
  consignee: string; trips: number; total_km: number | null; avg_speed: number | null;
  avg_duration_min: number | null; otd_pct: number | null; avg_delay_min: number | null;
  destinations: number; vehicles: number; drivers: number; last_trip: string | null;
}
export interface ConsignorRow {
  id: number; name: string; trips: number; total_km: number | null; avg_speed: number | null;
  otd_pct: number | null; consignees: number; destinations: number; vehicles: number;
  drivers: number; last_trip: string | null;
}

// --- Consignees (receivers) ---
export const listConsignees = (search = '') =>
  backendApi.get<{ data: ConsigneeRow[]; total: number }>('/consignees', { params: { search } });
export const getConsignee = (name: string) =>
  backendApi.get<PartnerDetail & { consignee: string }>(`/consignees/${encodeURIComponent(name)}`);

// --- Consignors (shippers) ---
export const getConsignorsOverview = () =>
  backendApi.get<{ data: ConsignorRow[]; total: number }>('/consignors/overview');
export const getConsignorDetail = (id: number | string) =>
  backendApi.get<PartnerDetail & { id: number; name: string; top_consignees: ConsigneeRow[] }>(`/consignors/${id}/detail`);
