-- The origin-geofence exit Smart-Truck's circle geofencing stamps on each
-- trip. Smart-Truck kept it on tta_trips (UPDATE tta_trips SET
-- dt_geofence_out ...); in NexGen the trip row belongs to the fleet service,
-- so the geofence service keeps its own finding here, and the legacy-shaped
-- tta_trips view joins it back in, with the three derived minutes Smart-Truck
-- computed as generated columns.
CREATE TABLE IF NOT EXISTS trip_geofence_out (
    i_tenant_id             SMALLINT UNSIGNED NOT NULL,
    i_trip_no               BIGINT            NOT NULL,
    dt_geofence_out         DATETIME          NULL,     -- sustained exit from the origin geofence
    i_geofence_out_gap_min  INT               NULL,     -- ping gap after the exit = uncertainty
    s_geofence_out_status   VARCHAR(24)       NULL,     -- ok | never_inside | never_exited | no_gps | no_fence
    dt_computed             DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id, i_trip_no),
    KEY idx_tgo_out (i_tenant_id, dt_geofence_out)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
