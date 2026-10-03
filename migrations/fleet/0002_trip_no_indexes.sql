-- The legacy code (and everything reading the legacy-shaped views) joins
-- trips on i_trip_no alone. The primary keys lead with i_tenant_id, so
-- without these a lookup by trip number cannot use them and every join
-- becomes a scan (measured: the geofence trips list timed out at 60 s).
ALTER TABLE trip ADD KEY idx_trip_trip_no (i_trip_no);
ALTER TABLE trip_provider_metric ADD KEY idx_tpm_trip_no (i_trip_no);
ALTER TABLE trip_source_record ADD KEY idx_tsr_trip_no (i_trip_no);
ALTER TABLE trip_gps_window ADD KEY idx_tgw_trip_no (i_trip_no);
ALTER TABLE fix_batch_trip ADD KEY idx_fbt_trip_no (i_trip_no, i_batch_id);
