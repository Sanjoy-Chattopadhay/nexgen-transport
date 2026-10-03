export interface TTAStatus {
  consignors: number;
  tta_trips: number;
  tta_trip_metrics: number;
  tta_trip_gps: number;
  [table: string]: number;
}

export interface TTATripRow {
  i_trip_no: number;
  i_cnr_id: number | null;
  s_cnr_name: string | null;
  s_asset_id: string | null;
  s_device_id: string | null;
  s_asset_type: string | null;
  s_org_node_name: string | null;
  s_dest_node_name: string | null;
  dt_trip_start: string | null;
  dt_trip_eta: string | null;
  dt_trip_ata: string | null;
  dt_trip_end: string | null;
  c_trip_status: string | null;
  s_close_reason: string | null;
  s_driver_name: string | null;
  s_driver_mobile_no: string | null;
  s_trans_name: string | null;
  d_distance_travelled_km: number | null;
  s_delivery_status: string | null;
  i_speed_violation: number | null;
  i_transit_time_min: number | null;
  i_moving_time_min: number | null;
  i_stoppage_time_min: number | null;
  gps_points: number;
}

export interface TTATripList {
  items: TTATripRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface TTATripMetrics {
  i_trip_no: number;
  i_sl_no: number | null;
  s_tag: string | null;
  s_store_entry_no: string | null;
  dt_ata_out: string | null;
  dt_delivery: string | null;
  s_delivery_status: string | null;
  s_delivery_dur: string | null;
  i_delivery_delta_min: number | null;
  s_transit_time: string | null;
  i_transit_time_min: number | null;
  s_detention: string | null;
  i_detention_min: number | null;
  s_total_moving_time: string | null;
  i_moving_time_min: number | null;
  s_total_stoppage_time: string | null;
  i_stoppage_time_min: number | null;
  s_plant_vivo: string | null;
  i_plant_vivo_min: number | null;
  d_distance_travelled_km: number | null;
  i_speed_violation: number | null;
  d_uptime_pct: number | null;
  s_service_provider: string | null;
  s_supplier_name: string | null;
  s_cne_contact_no: string | null;
  i_cne_pin: number | null;
  d_inv_qty: number | null;
  s_material_desc: string | null;
  s_asset_make: string | null;
  s_asset_model: string | null;
  s_close_remarks: string | null;
  s_fo_no: string | null;
  i_trip_seq: number | null;
  [key: string]: unknown;
}

export interface TTAGpsSummary {
  total_pings: number;
  moving_pings: number | null;
  first_ping: string | null;
  last_ping: string | null;
  max_cdist_m: number | null;
  max_speed_kmph: number | null;
  avg_moving_speed_kmph: number | null;
}

export interface TTATripDetail {
  trip: Record<string, unknown> & TTATripRow;
  metrics: TTATripMetrics | null;
  gps_summary: TTAGpsSummary | null;
}

export interface TTAGpsPoint {
  id: number;
  i_trip_no: number;
  s_asset_id: string | null;
  s_device_id: string | null;
  dt_message: string;
  d_lat: number;
  d_long: number;
  i_speed: number;
  s_wpnt1: string | null;
  i_wpnt1_mt: number | null;
  s_wpnt1_st_abbr: string | null;
  s_wpnt2: string | null;
  i_wpnt2_mt: number | null;
  s_wpnt2_st_abbr: string | null;
  s_uom: string | null;
  i_dist: number;
  i_cdist: number;
  s_status: string | null;
  is_moving: number;
  i_status_speed_kmph: number | null;
}

export interface TTAGpsResponse {
  trip_no: number;
  total: number;
  returned: number;
  points: TTAGpsPoint[];
}

export interface TTAUploadResult {
  status: string;
  trips_upserted: number;
  consignors_upserted: number;
  gps_inserted: number;
  gps_skipped: number;
  errors: string[];
  blocks_found: number;
  upload_id?: number;
}
