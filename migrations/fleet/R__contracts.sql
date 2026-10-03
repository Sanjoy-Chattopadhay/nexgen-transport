-- The fleet service's published contract: v1_* views. Other services read
-- fleet data only through these (or through their own alias views over
-- them). Change a column here only by adding v2_* beside v1_*.
--
-- Two families:
--   * normalised views (v1_trip, v1_gps_fix, ...) for new code
--   * legacy-shaped views (v1_tta_trips, v1_tta_trip_gps, ...) that return
--     exactly the columns, names and values Smart-Truck's and Geo-Fencing's
--     tables held, so their analysis code runs unchanged on the new storage.

-- ---------------------------------------------------------------------------
-- Legacy shapes (Smart-Truck)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v1_consignors AS
SELECT c.i_cnr_id, c.s_cnr_name, c.dt_created, c.dt_modified, c.i_tenant_id
FROM consignor c;

CREATE OR REPLACE VIEW v1_tta_trips AS
SELECT t.i_trip_no,
       t.i_cnr_id,
       t.s_trip_class,
       t.s_cnr_name,
       v.s_asset_no                 AS s_asset_id,
       dv.s_device_no               AS s_device_id,
       t.s_asset_type,
       t.c_trip_type,
       t.s_trip_type_desc,
       NULLIF(o.i_node_no, 0)       AS i_org_node_no,
       NULLIF(o.s_name, '')         AS s_org_node_name,
       NULLIF(de.i_node_no, 0)      AS i_dest_node_no,
       NULLIF(de.s_name, '')        AS s_dest_node_name,
       NULLIF(fd.s_name, '')        AS s_final_dest,
       NULLIF(lp.s_name, '')        AS s_load_plant,
       t.dt_booking,
       t.dt_trip_start,
       t.dt_trip_eta,
       t.dt_trip_ata,
       t.dt_trip_end,
       NULLIF(tr.s_code, '')        AS i_trans_id,
       NULLIF(tr.s_name, '')        AS s_trans_name,
       ce.s_name                    AS s_cne_name,
       t.c_trip_status,
       t.s_close_reason,
       t.s_invoice,
       t.s_card_id,
       t.s_shipment_id,
       t.s_event_code,
       t.s_gate_entry_no,
       NULLIF(dr.s_name, '')        AS s_driver_name,
       NULLIF(dr.s_mobile, '')      AS s_driver_mobile_no,
       t.i_route_id,
       t.s_created_by,
       t.dt_created,
       t.s_modified_by,
       t.dt_modified,
       t.i_gps_ping_count,
       t.i_tenant_id
FROM trip t
LEFT JOIN vehicle     v  ON v.i_vehicle_id = t.i_vehicle_id
LEFT JOIN device      dv ON dv.i_device_id = t.i_device_id
LEFT JOIN location    o  ON o.i_location_id = t.i_origin_id
LEFT JOIN location    de ON de.i_location_id = t.i_dest_id
LEFT JOIN location    fd ON fd.i_location_id = t.i_final_dest_id
LEFT JOIN location    lp ON lp.i_location_id = t.i_load_plant_id
LEFT JOIN transporter tr ON tr.i_transporter_id = t.i_transporter_id
LEFT JOIN consignee   ce ON ce.i_consignee_id = t.i_consignee_id
LEFT JOIN driver      dr ON dr.i_driver_id = t.i_driver_id;

CREATE OR REPLACE VIEW v1_tta_trip_metrics AS
SELECT m.i_trip_no, m.i_sl_no, m.s_tag, m.s_store_entry_no, m.dt_ata_out, m.dt_delivery,
       m.s_delivery_status, m.s_delivery_dur, m.i_delivery_delta_min, m.s_transit_time,
       m.i_transit_time_min, m.s_detention, m.i_detention_min, m.s_total_moving_time,
       m.i_moving_time_min, m.s_total_stoppage_time, m.i_stoppage_time_min, m.s_plant_vivo,
       m.i_plant_vivo_min, m.d_distance_travelled_km, m.i_speed_violation, m.d_uptime_pct,
       m.s_service_provider, m.s_supplier_name, m.s_cne_contact_no, m.i_cne_pin,
       m.s_ship_to_address, m.i_geo_id, m.d_inv_qty, m.s_material_desc, m.s_asset_make,
       m.s_asset_model, m.s_close_remarks, m.s_fo_no, m.i_trip_seq, m.s_tta_ex_nd,
       m.s_det_ex_nd, r.j_record AS raw_json, m.dt_created, m.i_tenant_id
FROM trip_provider_metric m
LEFT JOIN trip_source_record r ON r.i_tenant_id = m.i_tenant_id AND r.i_trip_no = m.i_trip_no;

-- A trip's copy of its truck's fixes, reproduced from the one stored copy.
-- id: unique per (trip, fix) and in time order, like the old auto-increment.
-- i_cdist: the running sum of i_dist from the trip's first fix, which is
-- exactly what the source sent (verified on 40 random trips, 2026-10-03).
CREATE OR REPLACE VIEW v1_tta_trip_gps AS
SELECT w.i_trip_no * 100000
         + ROW_NUMBER() OVER (PARTITION BY w.i_tenant_id, w.i_trip_no ORDER BY f.dt_fix, f.i_seq) AS id,
       w.i_trip_no,
       v.s_asset_no                       AS s_asset_id,
       dv.s_device_no                     AS s_device_id,
       f.i_entity_id,
       en.s_entity_name,
       f.dt_fix                           AS dt_message,
       f.d_lat,
       f.d_lon                            AS d_long,
       f.i_speed,
       w1.s_name                          AS s_wpnt1,
       f.i_wp1_m                          AS i_wpnt1_mt,
       NULLIF(w1.s_state_abbr, '')        AS s_wpnt1_st_abbr,
       w2.s_name                          AS s_wpnt2,
       f.i_wp2_m                          AS i_wpnt2_mt,
       NULLIF(w2.s_state_abbr, '')        AS s_wpnt2_st_abbr,
       f.c_uom                            AS s_uom,
       COALESCE(o.i_dist_m, f.i_dist_m)   AS i_dist,
       SUM(COALESCE(o.i_dist_m, f.i_dist_m, 0))
         OVER (PARTITION BY w.i_tenant_id, w.i_trip_no ORDER BY f.dt_fix, f.i_seq
               ROWS UNBOUNDED PRECEDING)  AS i_cdist,
       st.s_status,
       st.b_moving                        AS is_moving,
       st.i_speed_kmph                    AS i_status_speed_kmph,
       f.c_source,
       f.i_seq,
       f.i_batch_id,
       w.i_tenant_id
FROM trip_gps_window w
JOIN gps_fix f            ON f.i_tenant_id = w.i_tenant_id AND f.i_vehicle_id = w.i_vehicle_id
                         AND f.dt_fix BETWEEN w.dt_from AND w.dt_to
JOIN vehicle v            ON v.i_vehicle_id = f.i_vehicle_id
LEFT JOIN device dv       ON dv.i_device_id = f.i_device_id
LEFT JOIN gps_entity en   ON en.i_tenant_id = f.i_tenant_id AND en.i_entity_id = f.i_entity_id
LEFT JOIN ref_waypoint w1 ON w1.i_waypoint_id = f.i_wp1_id
LEFT JOIN ref_waypoint w2 ON w2.i_waypoint_id = f.i_wp2_id
LEFT JOIN ref_gps_status st ON st.i_status_id = f.i_status_id
LEFT JOIN trip_fix_override o ON o.i_tenant_id = w.i_tenant_id AND o.i_trip_no = w.i_trip_no
                             AND o.dt_fix = f.dt_fix AND o.i_seq = f.i_seq;

-- ---------------------------------------------------------------------------
-- Normalised contract
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v1_trip AS
SELECT t.i_tenant_id, t.i_trip_no, t.s_trip_class, t.i_cnr_id, t.s_cnr_name,
       t.i_vehicle_id, v.s_asset_no, t.s_asset_type,
       t.i_origin_id, o.s_name AS s_origin, t.i_dest_id, de.s_name AS s_destination,
       t.i_transporter_id, NULLIF(tr.s_name, '') AS s_transporter, NULLIF(tr.s_code, '') AS s_transporter_code,
       t.i_consignee_id, ce.s_name AS s_consignee,
       t.i_driver_id, NULLIF(dr.s_name, '') AS s_driver, NULLIF(dr.s_mobile, '') AS s_driver_mobile,
       t.dt_booking, t.dt_trip_start, t.dt_trip_eta, t.dt_trip_ata, t.dt_trip_end,
       t.c_trip_status, t.s_close_reason, t.i_gps_ping_count,
       w.dt_from AS dt_gps_from, w.dt_to AS dt_gps_to, w.dt_first_fix, w.dt_last_fix,
       t.dt_created, t.dt_modified
FROM trip t
LEFT JOIN vehicle v        ON v.i_vehicle_id = t.i_vehicle_id
LEFT JOIN location o       ON o.i_location_id = t.i_origin_id
LEFT JOIN location de      ON de.i_location_id = t.i_dest_id
LEFT JOIN transporter tr   ON tr.i_transporter_id = t.i_transporter_id
LEFT JOIN consignee ce     ON ce.i_consignee_id = t.i_consignee_id
LEFT JOIN driver dr        ON dr.i_driver_id = t.i_driver_id
LEFT JOIN trip_gps_window w ON w.i_tenant_id = t.i_tenant_id AND w.i_trip_no = t.i_trip_no;

CREATE OR REPLACE VIEW v1_gps_fix AS
SELECT f.i_tenant_id, f.i_vehicle_id, v.s_asset_no, f.dt_fix, f.i_seq, f.d_lat, f.d_lon,
       f.i_speed, st.s_status, st.b_moving, st.i_speed_kmph AS i_status_speed_kmph,
       f.i_wp1_id, f.i_wp1_m, f.i_wp2_id, f.i_wp2_m, f.i_dist_m, f.c_source, f.i_batch_id
FROM gps_fix f
JOIN vehicle v ON v.i_vehicle_id = f.i_vehicle_id
LEFT JOIN ref_gps_status st ON st.i_status_id = f.i_status_id;

CREATE OR REPLACE VIEW v1_vehicle AS
SELECT i_tenant_id, i_vehicle_id, s_asset_no, dt_first_seen, dt_first_fix, dt_last_fix, i_fixes FROM vehicle;

CREATE OR REPLACE VIEW v1_transporter AS
SELECT i_tenant_id, i_transporter_id, NULLIF(s_code, '') AS s_code, NULLIF(s_name, '') AS s_name FROM transporter;

CREATE OR REPLACE VIEW v1_consignee AS SELECT i_tenant_id, i_consignee_id, s_name FROM consignee;

CREATE OR REPLACE VIEW v1_location AS
SELECT i_tenant_id, i_location_id, NULLIF(i_node_no, 0) AS i_node_no, s_name FROM location;

CREATE OR REPLACE VIEW v1_driver AS
SELECT i_tenant_id, i_driver_id, NULLIF(s_name, '') AS s_name, NULLIF(s_mobile, '') AS s_mobile FROM driver;

CREATE OR REPLACE VIEW v1_waypoint AS
SELECT i_waypoint_id, s_name, NULLIF(s_state_abbr, '') AS s_state_abbr FROM ref_waypoint;

CREATE OR REPLACE VIEW v1_trip_gps_window AS
SELECT i_tenant_id, i_trip_no, i_vehicle_id, dt_from, dt_to, dt_first_fix, dt_last_fix FROM trip_gps_window;

CREATE OR REPLACE VIEW v1_processed_batch AS
SELECT i_batch_id, i_tenant_id, s_status, i_trips, i_fixes_in, i_fixes_new, i_fixes_dup, i_fixes_seq,
       d_seconds, s_error, dt_processed
FROM processed_batch;

CREATE OR REPLACE VIEW v1_fix_batch_trip AS
SELECT i_batch_id, i_tenant_id, i_trip_no, i_vehicle_id, i_new_fixes, dt_min, dt_max, dt_created
FROM fix_batch_trip;
