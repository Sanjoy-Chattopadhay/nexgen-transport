-- Ingestion's published contract. The fleet processor reads batches through
-- these; nobody reads nx_ingest tables directly.

CREATE OR REPLACE VIEW v1_batch AS
SELECT i_batch_id, i_tenant_id, s_source, s_trip_class, c_gps_kind, i_default_cnr_id,
       i_sync_run_id, s_trigger, dt_window_from, dt_window_to, i_trips, i_fixes, i_parts,
       s_status, dt_landed, dt_processed
FROM batch;

CREATE OR REPLACE VIEW v1_payload AS
SELECT i_batch_id, i_part, i_trip_no, s_vehicle, dt_gps_from, dt_gps_to, i_fixes,
       b_gps_failed, b_body
FROM payload;

CREATE OR REPLACE VIEW v1_sync_run AS
SELECT id, trigger_type, lane, status, window_start, window_end, blocks_fetched,
       trips_upserted, gps_inserted, gps_failed, error, started_at, finished_at,
       elapsed_seconds, i_batch_id
FROM tta_sync_runs;
