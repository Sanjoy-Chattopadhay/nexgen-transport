-- What the routing service reads from geofence and fleet, under the names
-- Geo-Fencing's routing code uses. Read-throughs to their published views.
CREATE OR REPLACE VIEW geo_run AS SELECT * FROM {{schema:geofence}}.v1_geo_run;
CREATE OR REPLACE VIEW geo_state AS SELECT * FROM {{schema:geofence}}.v1_geo_state;
CREATE OR REPLACE VIEW geo_trip_summary AS SELECT * FROM {{schema:geofence}}.v1_geo_trip_summary;
CREATE OR REPLACE VIEW geo_fit_trail AS SELECT * FROM {{schema:geofence}}.v1_geo_fit_trail;
CREATE OR REPLACE VIEW geo_stop AS SELECT * FROM {{schema:geofence}}.v1_geo_stop;
CREATE OR REPLACE VIEW geo_gap AS SELECT * FROM {{schema:geofence}}.v1_geo_gap;
CREATE OR REPLACE VIEW geo_trip_phase AS SELECT * FROM {{schema:geofence}}.v1_geo_trip_phase;
CREATE OR REPLACE VIEW geo_fence AS SELECT * FROM {{schema:geofence}}.v1_geo_fence;
CREATE OR REPLACE VIEW geo_site AS SELECT * FROM {{schema:geofence}}.v1_geo_site;
CREATE OR REPLACE VIEW geo_visit AS SELECT * FROM {{schema:geofence}}.v1_geo_visit;
CREATE OR REPLACE VIEW geo_trip_meta AS
SELECT i_trip_no, s_asset_id, s_asset_type, s_trip_class, s_trip_type_desc AS s_trip_type, s_driver_name,
       s_driver_mobile_no AS s_driver_mobile, i_trans_id AS s_trans_id, s_trans_name, s_cnr_name, s_cne_name,
       s_org_node_name AS s_origin, s_dest_node_name AS s_destination, s_final_dest, s_load_plant,
       c_trip_status AS s_status, s_close_reason, s_invoice, dt_booking, dt_trip_start, dt_trip_eta,
       dt_trip_ata, dt_trip_end, dt_modified AS dt_synced
FROM {{schema:fleet}}.v1_tta_trips;
