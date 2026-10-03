-- What analytics reads from fleet and geofence, under the names Smart-Truck's
-- analysis code uses. Read-throughs to their published views; the tables
-- analytics writes itself are real tables (0001_analysis_tables.sql).

CREATE OR REPLACE VIEW consignors AS
SELECT i_cnr_id, s_cnr_name, dt_created, dt_modified FROM {{schema:fleet}}.v1_consignors;

-- Smart-Truck's tta_trips, including the origin-geofence exit its circle
-- geofencing stamped on it and the three minutes it derived from it.
CREATE OR REPLACE VIEW tta_trips AS
SELECT t.i_trip_no, t.i_cnr_id, t.s_trip_class, t.s_cnr_name, t.s_asset_id, t.s_device_id, t.s_asset_type,
       t.c_trip_type, t.s_trip_type_desc, t.i_org_node_no, t.s_org_node_name, t.i_dest_node_no,
       t.s_dest_node_name, t.s_final_dest, t.s_load_plant, t.dt_booking, t.dt_trip_start, t.dt_trip_eta,
       t.dt_trip_ata, t.dt_trip_end, t.i_trans_id, t.s_trans_name, t.s_cne_name, t.c_trip_status,
       t.s_close_reason, t.s_invoice, t.s_card_id, t.s_shipment_id, t.s_event_code, t.s_gate_entry_no,
       t.s_driver_name, t.s_driver_mobile_no, t.i_route_id, t.s_created_by, t.dt_created, t.s_modified_by,
       t.dt_modified, t.i_gps_ping_count,
       g.dt_geofence_out, g.i_geofence_out_gap_min, g.s_geofence_out_status,
       TIMESTAMPDIFF(MINUTE, t.dt_booking, t.dt_trip_start)       AS i_works_detention_min,
       TIMESTAMPDIFF(MINUTE, t.dt_trip_start, g.dt_geofence_out)  AS i_geofence_tail_min,
       TIMESTAMPDIFF(MINUTE, t.dt_booking, g.dt_geofence_out)     AS i_origin_total_min
FROM {{schema:fleet}}.v1_tta_trips t
LEFT JOIN {{schema:geofence}}.v1_trip_geofence_out g ON g.i_tenant_id = t.i_tenant_id AND g.i_trip_no = t.i_trip_no;

CREATE OR REPLACE VIEW tta_trip_metrics AS
SELECT i_trip_no, i_sl_no, s_tag, s_store_entry_no, dt_ata_out, dt_delivery, s_delivery_status, s_delivery_dur,
       i_delivery_delta_min, s_transit_time, i_transit_time_min, s_detention, i_detention_min,
       s_total_moving_time, i_moving_time_min, s_total_stoppage_time, i_stoppage_time_min, s_plant_vivo,
       i_plant_vivo_min, d_distance_travelled_km, i_speed_violation, d_uptime_pct, s_service_provider,
       s_supplier_name, s_cne_contact_no, i_cne_pin, s_ship_to_address, i_geo_id, d_inv_qty, s_material_desc,
       s_asset_make, s_asset_model, s_close_remarks, s_fo_no, i_trip_seq, s_tta_ex_nd, s_det_ex_nd, raw_json,
       dt_created
FROM {{schema:fleet}}.v1_tta_trip_metrics;

CREATE OR REPLACE VIEW tta_trip_gps AS
SELECT id, i_trip_no, s_asset_id, s_device_id, i_entity_id, s_entity_name, dt_message, d_lat, d_long, i_speed,
       s_wpnt1, i_wpnt1_mt, s_wpnt1_st_abbr, s_wpnt2, i_wpnt2_mt, s_wpnt2_st_abbr, s_uom, i_dist, i_cdist,
       s_status, is_moving, i_status_speed_kmph
FROM {{schema:fleet}}.v1_tta_trip_gps;

CREATE OR REPLACE VIEW tta_trip_gps_cdist AS
SELECT id, i_trip_no, s_asset_id, s_device_id, i_entity_id, s_entity_name, dt_message, d_lat, d_long, i_speed,
       s_wpnt1, i_wpnt1_mt, s_wpnt1_st_abbr, s_wpnt2, i_wpnt2_mt, s_wpnt2_st_abbr, s_uom, i_dist, i_cdist,
       s_status, is_moving, i_status_speed_kmph
FROM {{schema:fleet}}.v1_tta_trip_gps_cdist;

-- Smart-Truck's circle geofencing (the geofence service owns it)
CREATE OR REPLACE VIEW geofences AS SELECT * FROM {{schema:geofence}}.v1_geofences;
CREATE OR REPLACE VIEW tta_trip_geofence_events AS SELECT * FROM {{schema:geofence}}.v1_trip_geofence_events;
CREATE OR REPLACE VIEW gps_facility_anchors AS SELECT * FROM {{schema:geofence}}.v1_gps_facility_anchors;
