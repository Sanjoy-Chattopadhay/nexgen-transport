-- nx_fleet: the fleet's master data, trips and GPS, normalised.
--
-- What changed from Smart-Truck's smart_truck schema, and why:
--
-- * Entities are extracted. A transporter, consignee, place, vehicle, device
--   and driver are each a row in their own table, referenced by id. Each is
--   keyed on the exact values the source sent, so a legacy-shaped view can
--   return every original string unchanged (the source spells one consignor
--   16 ways; merging spellings is a separate, later mapping).
-- * Attributes that genuinely vary per trip (the consignor name printed on
--   that trip, the declared asset type, document numbers) stay on the trip:
--   they are facts about the trip as received, not about the entity.
-- * GPS is stored once per physical fix, keyed by vehicle, not once per
--   consignment. Measured 2026-10-03: 12% of tta_trip_gps rows repeated a fix
--   stored for another consignment on the same truck; every fix belonged to
--   its trip's own truck. A trip reads its truck's fixes over its own window
--   (trip_gps_window), which reproduces each trip's legacy copy.
-- * GPS text is dictionary-encoded (4,549 waypoint name+state pairs, 75
--   status strings, 310 entities were repeated on 6.6M rows).
-- * Nothing is dropped: a second, different fix in the same second gets
--   i_seq 1, 2, ...; positions from a source that filters (map-matches) its
--   GPS land here too, marked c_source='F'.

-- ---------------------------------------------------------------------------
-- Entities
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consignor (
    i_tenant_id   SMALLINT UNSIGNED NOT NULL,
    i_cnr_id      INT               NOT NULL,      -- the upstream consignor id (stable, in URLs)
    s_cnr_name    VARCHAR(255)      NOT NULL,      -- latest name seen
    dt_created    TIMESTAMP         NULL DEFAULT CURRENT_TIMESTAMP,
    dt_modified   TIMESTAMP         NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id, i_cnr_id),
    KEY idx_cnr_name (i_tenant_id, s_cnr_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS transporter (
    i_transporter_id INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id      SMALLINT UNSIGNED NOT NULL,
    s_code           VARCHAR(50)       NOT NULL DEFAULT '',   -- '' = none sent
    s_name           VARCHAR(255)      NOT NULL DEFAULT '',
    dt_first_seen    DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_transporter_id),
    UNIQUE KEY idx_transporter_src (i_tenant_id, s_code, s_name),
    KEY idx_transporter_name (i_tenant_id, s_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS consignee (
    i_consignee_id INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id    SMALLINT UNSIGNED NOT NULL,
    s_name         VARCHAR(255)      NOT NULL,
    dt_first_seen  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_consignee_id),
    UNIQUE KEY idx_consignee_src (i_tenant_id, s_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Places: origins, destinations, final destinations, load plants.
CREATE TABLE IF NOT EXISTS location (
    i_location_id  INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id    SMALLINT UNSIGNED NOT NULL,
    i_node_no      INT               NOT NULL DEFAULT 0,      -- upstream node number, 0 = none sent
    s_name         VARCHAR(255)      NOT NULL,
    dt_first_seen  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_location_id),
    UNIQUE KEY idx_location_src (i_tenant_id, i_node_no, s_name),
    KEY idx_location_name (i_tenant_id, s_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS vehicle (
    i_vehicle_id   INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id    SMALLINT UNSIGNED NOT NULL,
    s_asset_no     VARCHAR(50)       NOT NULL,                -- registration / asset id
    dt_first_seen  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dt_first_fix   DATETIME          NULL,
    dt_last_fix    DATETIME          NULL,
    i_fixes        BIGINT UNSIGNED   NOT NULL DEFAULT 0,
    PRIMARY KEY (i_vehicle_id),
    UNIQUE KEY idx_vehicle_src (i_tenant_id, s_asset_no),
    KEY idx_vehicle_last (i_tenant_id, dt_last_fix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS device (
    i_device_id    MEDIUMINT UNSIGNED NOT NULL AUTO_INCREMENT,
    i_tenant_id    SMALLINT UNSIGNED  NOT NULL,
    s_device_no    VARCHAR(50)        NOT NULL,
    PRIMARY KEY (i_device_id),
    UNIQUE KEY idx_device_src (i_tenant_id, s_device_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS driver (
    i_driver_id    INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    i_tenant_id    SMALLINT UNSIGNED NOT NULL,
    s_name         VARCHAR(255)      NOT NULL DEFAULT '',
    s_mobile       VARCHAR(20)       NOT NULL DEFAULT '',
    dt_first_seen  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_driver_id),
    UNIQUE KEY idx_driver_src (i_tenant_id, s_name, s_mobile)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Upstream GPS "entity" (the account a device reports under): id -> name is
-- 1:1 in the data (310 of each).
CREATE TABLE IF NOT EXISTS gps_entity (
    i_tenant_id    SMALLINT UNSIGNED NOT NULL,
    i_entity_id    INT               NOT NULL,
    s_entity_name  VARCHAR(255)      NULL,
    PRIMARY KEY (i_tenant_id, i_entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Dictionaries for the GPS text. Global (provider vocabulary, not a client's).
CREATE TABLE IF NOT EXISTS ref_waypoint (
    i_waypoint_id  MEDIUMINT UNSIGNED NOT NULL AUTO_INCREMENT,
    s_name         VARCHAR(255)       NOT NULL,
    s_state_abbr   VARCHAR(10)        NOT NULL DEFAULT '',
    PRIMARY KEY (i_waypoint_id),
    UNIQUE KEY idx_waypoint_src (s_name, s_state_abbr)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ref_gps_status (
    i_status_id    SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    s_status       VARCHAR(50)       NOT NULL,
    b_moving       TINYINT(1)        NOT NULL DEFAULT 0,      -- parsed once from the text
    i_speed_kmph   SMALLINT          NULL,                     -- speed in the text, if any
    PRIMARY KEY (i_status_id),
    UNIQUE KEY idx_status_src (s_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Trips
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trip (
    i_tenant_id        SMALLINT UNSIGNED NOT NULL,
    i_trip_no          BIGINT            NOT NULL,             -- upstream trip number
    s_trip_class       VARCHAR(10)       NOT NULL DEFAULT 'zonal',  -- which lane: zonal | local
    i_cnr_id           INT               NULL,
    s_cnr_name         VARCHAR(255)      NULL,                 -- as printed on this trip
    i_vehicle_id       INT UNSIGNED      NULL,
    i_device_id        MEDIUMINT UNSIGNED NULL,
    s_asset_type       VARCHAR(100)      NULL,                 -- as declared on this trip
    c_trip_type        VARCHAR(20)       NULL,
    s_trip_type_desc   VARCHAR(100)      NULL,
    i_origin_id        INT UNSIGNED      NULL,
    i_dest_id          INT UNSIGNED      NULL,
    i_final_dest_id    INT UNSIGNED      NULL,
    i_load_plant_id    INT UNSIGNED      NULL,
    dt_booking         DATETIME          NULL,
    dt_trip_start      DATETIME          NULL,
    dt_trip_eta        DATETIME          NULL,
    dt_trip_ata        DATETIME          NULL,
    dt_trip_end        DATETIME          NULL,
    i_transporter_id   INT UNSIGNED      NULL,
    i_consignee_id     INT UNSIGNED      NULL,
    c_trip_status      VARCHAR(30)       NULL,
    s_close_reason     VARCHAR(255)      NULL,
    s_invoice          VARCHAR(100)      NULL,
    s_card_id          VARCHAR(50)       NULL,
    s_shipment_id      VARCHAR(100)      NULL,
    s_event_code       VARCHAR(50)       NULL,
    s_gate_entry_no    VARCHAR(100)      NULL,
    i_driver_id        INT UNSIGNED      NULL,
    i_route_id         INT               NULL,
    s_created_by       VARCHAR(100)      NULL,
    s_modified_by      VARCHAR(100)      NULL,
    i_gps_ping_count   INT               NOT NULL DEFAULT 0,   -- fixes in the trip's window
    i_batch_id         INT UNSIGNED      NULL,                 -- last batch that changed it
    dt_created         DATETIME          NULL DEFAULT CURRENT_TIMESTAMP,
    dt_modified        DATETIME          NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id, i_trip_no),
    KEY idx_trip_vehicle (i_tenant_id, i_vehicle_id, dt_trip_start),
    KEY idx_trip_start (i_tenant_id, dt_trip_start),
    KEY idx_trip_cnr (i_tenant_id, i_cnr_id),
    KEY idx_trip_class (i_tenant_id, s_trip_class),
    KEY idx_trip_status (i_tenant_id, c_trip_status),
    KEY idx_trip_lane (i_tenant_id, i_origin_id, i_dest_id),
    KEY idx_trip_transporter (i_tenant_id, i_transporter_id),
    KEY idx_trip_modified (i_tenant_id, dt_modified)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The upstream's own computed figures for a trip (transit, detention, ...),
-- kept apart from NexGen's so the source of every number is clear.
CREATE TABLE IF NOT EXISTS trip_provider_metric (
    i_tenant_id             SMALLINT UNSIGNED NOT NULL,
    i_trip_no               BIGINT            NOT NULL,
    i_sl_no                 INT               NULL,
    s_tag                   VARCHAR(20)       NULL,
    s_store_entry_no        VARCHAR(100)      NULL,
    dt_ata_out              DATETIME          NULL,
    dt_delivery             DATETIME          NULL,
    s_delivery_status       VARCHAR(50)       NULL,
    s_delivery_dur          VARCHAR(100)      NULL,
    i_delivery_delta_min    INT               NULL,
    s_transit_time          VARCHAR(50)       NULL,
    i_transit_time_min      INT               NULL,
    s_detention             VARCHAR(50)       NULL,
    i_detention_min         INT               NULL,
    s_total_moving_time     VARCHAR(50)       NULL,
    i_moving_time_min       INT               NULL,
    s_total_stoppage_time   VARCHAR(50)       NULL,
    i_stoppage_time_min     INT               NULL,
    s_plant_vivo            VARCHAR(50)       NULL,
    i_plant_vivo_min        INT               NULL,
    d_distance_travelled_km DECIMAL(10,2)     NULL,
    i_speed_violation       INT               NULL,
    d_uptime_pct            DECIMAL(6,2)      NULL,
    s_service_provider      VARCHAR(100)      NULL,
    s_supplier_name         VARCHAR(255)      NULL,
    s_cne_contact_no        VARCHAR(30)       NULL,
    i_cne_pin               INT               NULL,
    s_ship_to_address       VARCHAR(500)      NULL,
    i_geo_id                INT               NULL,
    d_inv_qty               DECIMAL(14,2)     NULL,
    s_material_desc         VARCHAR(500)      NULL,
    s_asset_make            VARCHAR(100)      NULL,
    s_asset_model           VARCHAR(100)      NULL,
    s_close_remarks         VARCHAR(500)      NULL,
    s_fo_no                 VARCHAR(100)      NULL,
    i_trip_seq              INT               NULL,
    s_tta_ex_nd             VARCHAR(100)      NULL,
    s_det_ex_nd             VARCHAR(100)      NULL,
    dt_created              TIMESTAMP         NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id, i_trip_no),
    KEY idx_tpm_delivery_status (i_tenant_id, s_delivery_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The full source record of each trip, as received. Smart-Truck kept it
-- (raw_json) so a feed field added later is never lost; it stays for the
-- life of the trip. Compressed: it is read rarely and is mostly text.
CREATE TABLE IF NOT EXISTS trip_source_record (
    i_tenant_id   SMALLINT UNSIGNED NOT NULL,
    i_trip_no     BIGINT            NOT NULL,
    j_record      JSON              NOT NULL,
    i_batch_id    INT UNSIGNED      NULL,
    dt_received   DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id, i_trip_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8;

-- Which of its truck's fixes belong to a trip: the window its GPS was fetched
-- for (or, for imported history, the span of the copy it had).
CREATE TABLE IF NOT EXISTS trip_gps_window (
    i_tenant_id   SMALLINT UNSIGNED NOT NULL,
    i_trip_no     BIGINT            NOT NULL,
    i_vehicle_id  INT UNSIGNED      NOT NULL,
    dt_from       DATETIME          NOT NULL,
    dt_to         DATETIME          NOT NULL,
    dt_first_fix  DATETIME          NULL,       -- the fixes actually inside the window
    dt_last_fix   DATETIME          NULL,
    PRIMARY KEY (i_tenant_id, i_trip_no),
    KEY idx_tgw_vehicle (i_tenant_id, i_vehicle_id, dt_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Per trip, the fixes whose source values differ from the truck's stored fix
-- (291 of 562,808 shared fixes carry a different per-fix distance in one
-- consignment's copy). Kept so a trip's own copy reproduces exactly.
CREATE TABLE IF NOT EXISTS trip_fix_override (
    i_tenant_id   SMALLINT UNSIGNED NOT NULL,
    i_trip_no     BIGINT            NOT NULL,
    dt_fix        DATETIME          NOT NULL,
    i_seq         TINYINT UNSIGNED  NOT NULL DEFAULT 0,
    i_dist_m      INT               NULL,
    PRIMARY KEY (i_tenant_id, i_trip_no, dt_fix, i_seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- GPS
-- ---------------------------------------------------------------------------
-- One row per physical fix. ~57 bytes of columns against Smart-Truck's 468
-- bytes per row; no secondary index: the primary key serves every read the
-- pipeline makes (a vehicle over a time window). Partitioned by month so
-- retention drops whole months instantly.
--
-- Coordinates keep the source's exact decimals: 3.4% of fixes arrive with
-- eight decimal places, which a 10^-7 integer grid would round. Exact costs
-- 3 bytes a row and keeps every legacy figure reproducible.
CREATE TABLE IF NOT EXISTS gps_fix (
    i_tenant_id   SMALLINT UNSIGNED  NOT NULL,
    i_vehicle_id  INT UNSIGNED       NOT NULL,
    dt_fix        DATETIME           NOT NULL,     -- source wall clock (Asia/Kolkata)
    i_seq         TINYINT UNSIGNED   NOT NULL DEFAULT 0,
    d_lat         DECIMAL(10,8)      NOT NULL,
    d_lon         DECIMAL(11,8)      NOT NULL,
    i_speed       SMALLINT           NULL,         -- device speed as received (r_speed)
    i_status_id   SMALLINT UNSIGNED  NULL,         -- ref_gps_status
    i_wp1_id      MEDIUMINT UNSIGNED NULL,         -- nearest waypoint behind -> ref_waypoint
    i_wp1_m       MEDIUMINT          NULL,
    i_wp2_id      MEDIUMINT UNSIGNED NULL,         -- nearest waypoint ahead
    i_wp2_m       MEDIUMINT          NULL,
    i_dist_m      MEDIUMINT          NULL,         -- source distance since the previous fix
    i_device_id   MEDIUMINT UNSIGNED NULL,
    i_entity_id   INT                NULL,
    c_uom         VARCHAR(2)         NULL,
    c_source      CHAR(1)            NOT NULL DEFAULT 'R',   -- R raw feed, F filtered by the source
    i_batch_id    INT UNSIGNED       NOT NULL,     -- lineage -> nx_ingest.batch
    PRIMARY KEY (i_tenant_id, i_vehicle_id, dt_fix, i_seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE COLUMNS (dt_fix) (
    PARTITION p_old    VALUES LESS THAN ('2026-01-01'),
    PARTITION p2026_01 VALUES LESS THAN ('2026-02-01'),
    PARTITION p2026_02 VALUES LESS THAN ('2026-03-01'),
    PARTITION p2026_03 VALUES LESS THAN ('2026-04-01'),
    PARTITION p2026_04 VALUES LESS THAN ('2026-05-01'),
    PARTITION p2026_05 VALUES LESS THAN ('2026-06-01'),
    PARTITION p2026_06 VALUES LESS THAN ('2026-07-01'),
    PARTITION p2026_07 VALUES LESS THAN ('2026-08-01'),
    PARTITION p2026_08 VALUES LESS THAN ('2026-09-01'),
    PARTITION p2026_09 VALUES LESS THAN ('2026-10-01'),
    PARTITION p2026_10 VALUES LESS THAN ('2026-11-01'),
    PARTITION p2026_11 VALUES LESS THAN ('2026-12-01'),
    PARTITION p2026_12 VALUES LESS THAN ('2027-01-01'),
    PARTITION p_future VALUES LESS THAN (MAXVALUE)
);

-- Which trips each batch added fixes to. Consumers that track "what is new"
-- (the geofence scheduler, the live detector) read this instead of scanning
-- gps_fix.
CREATE TABLE IF NOT EXISTS fix_batch_trip (
    i_batch_id    INT UNSIGNED      NOT NULL,
    i_tenant_id   SMALLINT UNSIGNED NOT NULL,
    i_trip_no     BIGINT            NOT NULL,
    i_vehicle_id  INT UNSIGNED      NOT NULL,
    i_new_fixes   INT               NOT NULL DEFAULT 0,
    dt_min        DATETIME          NULL,
    dt_max        DATETIME          NULL,
    dt_created    DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_batch_id, i_tenant_id, i_trip_no),
    KEY idx_fbt_trip (i_tenant_id, i_trip_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Every batch the processor has applied, and how it went.
CREATE TABLE IF NOT EXISTS processed_batch (
    i_batch_id    INT UNSIGNED      NOT NULL,
    i_tenant_id   SMALLINT UNSIGNED NOT NULL,
    s_status      VARCHAR(16)       NOT NULL,          -- ok | failed
    i_trips       INT               NOT NULL DEFAULT 0,
    i_fixes_in    INT               NOT NULL DEFAULT 0,
    i_fixes_new   INT               NOT NULL DEFAULT 0,
    i_fixes_dup   INT               NOT NULL DEFAULT 0,
    i_fixes_seq   INT               NOT NULL DEFAULT 0,  -- distinct fixes in an occupied second
    d_seconds     DOUBLE            NULL,
    s_error       TEXT              NULL,
    dt_processed  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
