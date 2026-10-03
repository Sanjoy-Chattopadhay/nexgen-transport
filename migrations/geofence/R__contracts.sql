-- The geofence service's published contract. Routing and analytics read
-- geofencing results only through these views.

-- Smart-Truck's circle geofencing
CREATE OR REPLACE VIEW v1_geofences AS
SELECT i_fence_id, s_key, s_name, s_role, d_lat, d_long, i_radius_m, i_trips_seen, i_spread_m, b_manual,
       b_active, s_source, dt_created, dt_modified
FROM geofences;

CREATE OR REPLACE VIEW v1_trip_geofence_events AS
SELECT id, i_trip_no, i_fence_id, s_fence_key, s_role, s_event, dt_event, i_gap_min, d_lat, d_long
FROM tta_trip_geofence_events;

CREATE OR REPLACE VIEW v1_gps_facility_anchors AS
SELECT s_node_name, s_role, d_lat, d_long, i_trips, i_spread_m, dt_refreshed FROM gps_facility_anchors;

CREATE OR REPLACE VIEW v1_trip_geofence_out AS
SELECT i_tenant_id, i_trip_no, dt_geofence_out, i_geofence_out_gap_min, s_geofence_out_status, dt_computed
FROM trip_geofence_out;

-- The polygon engine's results (Geo-Fencing's tables, unchanged)
CREATE OR REPLACE VIEW v1_geo_run AS SELECT * FROM geo_run;
CREATE OR REPLACE VIEW v1_geo_trip_summary AS SELECT * FROM geo_trip_summary;
CREATE OR REPLACE VIEW v1_geo_fit_trail AS SELECT * FROM geo_fit_trail;
CREATE OR REPLACE VIEW v1_geo_stop AS SELECT * FROM geo_stop;
CREATE OR REPLACE VIEW v1_geo_gap AS SELECT * FROM geo_gap;
CREATE OR REPLACE VIEW v1_geo_trip_phase AS SELECT * FROM geo_trip_phase;
CREATE OR REPLACE VIEW v1_geo_fence AS SELECT * FROM geo_fence;
CREATE OR REPLACE VIEW v1_geo_site AS SELECT * FROM geo_site;
CREATE OR REPLACE VIEW v1_geo_visit AS SELECT * FROM geo_visit;
CREATE OR REPLACE VIEW v1_geo_pvisit AS SELECT * FROM geo_pvisit;
CREATE OR REPLACE VIEW v1_geo_palert AS SELECT * FROM geo_palert;
CREATE OR REPLACE VIEW v1_geo_trip_share AS SELECT * FROM geo_trip_share;
CREATE OR REPLACE VIEW v1_geo_fence_day AS SELECT * FROM geo_fence_day;
CREATE OR REPLACE VIEW v1_geo_day_summary AS SELECT * FROM geo_day_summary;
CREATE OR REPLACE VIEW v1_geo_state AS SELECT * FROM geo_state;
