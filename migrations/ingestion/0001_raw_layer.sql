-- nx_ingest: the raw layer and the machinery that fills it.
--
-- Ingestion is the only part of NexGen that calls the TMS API or reads an
-- uploaded file. What it receives lands here exactly as received (compressed),
-- grouped into batches; the fleet processor turns a batch into clean rows and
-- the batch stays as the replay source for 90 days.

-- One unit of received data: one lane run, one upload, one import.
CREATE TABLE IF NOT EXISTS batch (
    i_batch_id      INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id     SMALLINT UNSIGNED NOT NULL,
    s_source        VARCHAR(32)       NOT NULL,     -- tms.zonal | tms.local | upload.tta | ...
    s_trip_class    VARCHAR(10)       NOT NULL DEFAULT 'zonal',
    c_gps_kind      CHAR(1)           NOT NULL DEFAULT 'R',   -- R raw device fixes, F filtered by the source
    i_default_cnr_id INT              NULL,         -- consignor for records that carry none
    i_sync_run_id   BIGINT            NULL,
    s_trigger       VARCHAR(20)       NULL,
    dt_window_from  DATETIME          NULL,
    dt_window_to    DATETIME          NULL,
    i_trips         INT               NOT NULL DEFAULT 0,
    i_fixes         INT               NOT NULL DEFAULT 0,
    i_parts         INT               NOT NULL DEFAULT 0,
    s_status        VARCHAR(16)       NOT NULL DEFAULT 'landed',  -- landed | processed | failed
    s_note          VARCHAR(500)      NULL,
    dt_landed       DATETIME(3)       NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    dt_processed    DATETIME(3)       NULL,
    PRIMARY KEY (i_batch_id),
    KEY idx_batch_tenant (i_tenant_id, i_batch_id),
    KEY idx_batch_landed (dt_landed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The payload of a batch: one row per trip record with its GPS, exactly as the
-- upstream returned it, as zlib-compressed JSON ({"trip": {...}, "gps": [...],
-- "gps_from": ..., "gps_to": ...}). About 40 KB per trip compressed.
CREATE TABLE IF NOT EXISTS payload (
    i_batch_id   INT UNSIGNED  NOT NULL,
    i_part       INT           NOT NULL,
    i_trip_no    BIGINT        NULL,
    s_vehicle    VARCHAR(50)   NULL,
    dt_gps_from  DATETIME      NULL,     -- the window this trip's GPS was fetched for
    dt_gps_to    DATETIME      NULL,
    i_fixes      INT           NOT NULL DEFAULT 0,
    b_gps_failed TINYINT(1)    NOT NULL DEFAULT 0,   -- GPS fetch failed; the lane will re-pull
    b_body       MEDIUMBLOB    NOT NULL,
    PRIMARY KEY (i_batch_id, i_part),
    KEY idx_payload_trip (i_trip_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Smart-Truck's per-lane settings and bookkeeping (sync config, watermarks,
-- boot catch-up marks), same keys and shape as its app_settings rows.
CREATE TABLE IF NOT EXISTS app_settings (
    s_key        VARCHAR(64) NOT NULL,
    s_value      JSON        NOT NULL,
    dt_modified  TIMESTAMP   NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (s_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- One row per scheduled/manual lane run (Smart-Truck's tta_sync_runs).
-- trips_upserted / gps_inserted count what was LANDED; the fleet processor's
-- own counts (new vs duplicate fixes) are on its processed_batch row.
CREATE TABLE IF NOT EXISTS tta_sync_runs (
    id                  BIGINT        NOT NULL AUTO_INCREMENT,
    trigger_type        VARCHAR(20)   NULL,
    lane                VARCHAR(16)   NOT NULL DEFAULT 'zonal',
    status              VARCHAR(20)   NULL,
    window_start        DATETIME      NULL,
    window_end          DATETIME      NULL,
    blocks_fetched      INT           NULL DEFAULT 0,
    trips_upserted      INT           NULL DEFAULT 0,
    gps_inserted        INT           NULL DEFAULT 0,
    gps_skipped         INT           NULL DEFAULT 0,
    gps_failed          INT           NULL DEFAULT 0,
    legacy_trips_synced INT           NULL DEFAULT 0,
    error               TEXT          NULL,
    started_at          DATETIME      NULL,
    finished_at         DATETIME      NULL,
    elapsed_seconds     DECIMAL(10,1) NULL,
    i_batch_id          INT UNSIGNED  NULL,
    created_at          TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_sync_runs_created (created_at),
    KEY idx_sync_runs_lane (lane, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Spans the lanes could not reach (Smart-Truck's durable gap register).
CREATE TABLE IF NOT EXISTS tta_data_gaps (
    id            BIGINT        NOT NULL AUTO_INCREMENT,
    lane          VARCHAR(16)   NOT NULL,
    gap_start     DATETIME      NOT NULL,
    gap_end       DATETIME      NOT NULL,
    hours         DECIMAL(10,1) NOT NULL,
    state         VARCHAR(16)   NOT NULL DEFAULT 'open',
    detected_at   DATETIME      NOT NULL,
    detected_by   VARCHAR(32)   NULL,
    decided_at    DATETIME      NULL,
    snooze_until  DATETIME      NULL,
    note          VARCHAR(255)  NULL,
    recovered_at  DATETIME      NULL,
    created_at    TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_gaps_lane (lane, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Files received (TTA exports, master extracts, CSV/Excel history).
CREATE TABLE IF NOT EXISTS upload (
    i_upload_id    INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id    SMALLINT UNSIGNED NOT NULL,
    s_kind         VARCHAR(32)       NOT NULL,   -- tta_export | masters | trip_csv | ...
    s_filename     VARCHAR(500)      NOT NULL,
    s_stored_path  VARCHAR(1000)     NOT NULL,
    i_bytes        BIGINT            NULL,
    s_sha256       CHAR(64)          NULL,
    i_batch_id     INT UNSIGNED      NULL,
    s_status       VARCHAR(16)       NOT NULL DEFAULT 'received',
    s_error        TEXT              NULL,
    dt_received    DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_upload_id),
    KEY idx_upload_tenant (i_tenant_id, dt_received)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
