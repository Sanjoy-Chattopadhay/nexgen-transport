-- What the geofence service reads from the fleet service, under the names
-- the ported engine and circle-geofence code use. Every view here is a
-- read-through to fleet's published v1 views; nothing in nx_geo copies the
-- feed any more (Geo-Fencing kept its own 5M-row copy in geo_gps_ping).

-- Smart-Truck shapes ------------------------------------------------------
CREATE OR REPLACE VIEW consignors AS
SELECT i_cnr_id, s_cnr_name, dt_created, dt_modified FROM {{schema:fleet}}.v1_consignors;

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
LEFT JOIN trip_geofence_out g ON g.i_tenant_id = t.i_tenant_id AND g.i_trip_no = t.i_trip_no;

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

-- Each trip's first and last fix, kept by the fleet processor as it stores
-- them. The same values as MIN/MAX(dt_message) ... GROUP BY i_trip_no over
-- tta_trip_gps, without materialising every fix of the fleet to get them.
CREATE OR REPLACE VIEW tta_trip_gps_span AS
SELECT i_trip_no, dt_first_fix AS first_ping, dt_last_fix AS last_ping
FROM {{schema:fleet}}.v1_trip_gps_window
WHERE dt_first_fix IS NOT NULL;

-- Geo-Fencing's feed names ------------------------------------------------
-- geo_gps_ping: id is stable per physical fix (vehicle, second, sequence).
CREATE OR REPLACE VIEW geo_gps_ping AS
SELECT id, i_trip_no, s_asset_id, s_device_id, dt_message, d_lat, d_long, i_speed, s_status,
       is_moving AS b_moving, i_vehicle_id, i_batch_id
FROM {{schema:fleet}}.v1_tta_trip_gps;

CREATE OR REPLACE VIEW geo_trip AS
SELECT i_trip_no, s_asset_no AS s_asset_id, dt_first_fix AS dt_first_ping, dt_last_fix AS dt_last_ping,
       i_gps_ping_count AS i_pings
FROM {{schema:fleet}}.v1_trip
WHERE dt_first_fix IS NOT NULL;

CREATE OR REPLACE VIEW geo_trip_meta AS
SELECT i_trip_no, s_asset_id, s_asset_type, s_trip_class, s_trip_type_desc AS s_trip_type, s_driver_name,
       s_driver_mobile_no AS s_driver_mobile, i_trans_id AS s_trans_id, s_trans_name, s_cnr_name, s_cne_name,
       s_org_node_name AS s_origin, s_dest_node_name AS s_destination, s_final_dest, s_load_plant,
       c_trip_status AS s_status, s_close_reason, s_invoice, dt_booking, dt_trip_start, dt_trip_eta,
       dt_trip_ata, dt_trip_end, dt_modified AS dt_synced
FROM {{schema:fleet}}.v1_tta_trips;

CREATE OR REPLACE VIEW geo_vehicle AS
SELECT s_asset_no AS s_asset_id, CAST(NULL AS CHAR(50)) AS s_device_id, CAST(NULL AS SIGNED) AS i_entity_id,
       CAST(NULL AS CHAR(255)) AS s_entity_name, i_fixes AS i_pings, dt_first_fix AS dt_first_seen,
       dt_last_fix AS dt_last_seen
FROM {{schema:fleet}}.v1_vehicle;

-- Which trips each batch added fixes to: the scheduler's and the live
-- detector's "what is new" (replacing geo_gps_ping.id watermarks).
CREATE OR REPLACE VIEW geo_fix_batch_trip AS
SELECT i_batch_id, i_trip_no, i_vehicle_id, i_new_fixes, dt_min, dt_max, dt_created
FROM {{schema:fleet}}.v1_fix_batch_trip;

-- Batches the fleet service has finished storing (the scheduler's watermark
-- only advances over complete batches) and each trip's last change.
CREATE OR REPLACE VIEW geo_processed_batch AS
SELECT i_batch_id, i_tenant_id, s_status, i_fixes_new, dt_processed
FROM {{schema:fleet}}.v1_processed_batch WHERE s_status = 'ok';

CREATE OR REPLACE VIEW geo_trip_sync AS
SELECT i_trip_no, dt_modified FROM {{schema:fleet}}.v1_trip_sync;
