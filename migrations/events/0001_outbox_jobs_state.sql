-- nx_events: the shared infrastructure every service writes to.
--   event_log        the outbox: one row per domain event, written in the same
--                    transaction as the change it announces
--   consumer_offset  how far each consumer has applied the log
--   job_run          every scheduled or manual job run
--   service_state    whether each service should be running (developer page)
--   role_state       whether each role inside a service should be running
--   data_version     per tenant and scope; caches key their answers on it

CREATE TABLE IF NOT EXISTS event_log (
    i_event_id   BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    s_type       VARCHAR(64)       NOT NULL,
    i_tenant_id  SMALLINT UNSIGNED NOT NULL,
    s_source     VARCHAR(32)       NOT NULL,
    j_payload    JSON              NOT NULL,
    s_trace_id   VARCHAR(32)       NULL,
    dt_created   DATETIME(3)       NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (i_event_id),
    KEY idx_ev_type (s_type, i_event_id),
    KEY idx_ev_created (dt_created)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS consumer_offset (
    s_consumer       VARCHAR(64)     NOT NULL,
    i_last_event_id  BIGINT UNSIGNED NOT NULL DEFAULT 0,
    i_processed      BIGINT UNSIGNED NOT NULL DEFAULT 0,
    i_failed         BIGINT UNSIGNED NOT NULL DEFAULT 0,
    s_last_error     TEXT            NULL,
    dt_last_error    DATETIME(3)     NULL,
    dt_updated       DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (s_consumer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS job_run (
    i_run_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    s_service    VARCHAR(32)     NOT NULL,
    s_job        VARCHAR(64)     NOT NULL,
    s_trigger    VARCHAR(16)     NOT NULL,           -- schedule | manual | event | boot
    dt_started   DATETIME(3)     NOT NULL,
    dt_finished  DATETIME(3)     NULL,
    d_seconds    DOUBLE          NULL,
    s_status     VARCHAR(12)     NOT NULL,           -- running | ok | failed | skipped
    j_summary    JSON            NULL,
    s_error      TEXT            NULL,
    PRIMARY KEY (i_run_id),
    KEY idx_jr_job (s_service, s_job, i_run_id),
    KEY idx_jr_started (dt_started)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS service_state (
    s_service   VARCHAR(32) NOT NULL,
    s_desired   VARCHAR(12) NOT NULL,                -- running | stopped
    dt_changed  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (s_service)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS role_state (
    s_service   VARCHAR(32) NOT NULL,
    s_role      VARCHAR(32) NOT NULL,
    b_enabled   TINYINT(1)  NOT NULL,
    dt_changed  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (s_service, s_role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS data_version (
    i_tenant_id  SMALLINT UNSIGNED NOT NULL,
    s_scope      VARCHAR(32)       NOT NULL,         -- analytics | geofence | fleet ...
    i_version    BIGINT UNSIGNED   NOT NULL DEFAULT 0,
    dt_changed   DATETIME(3)       NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (i_tenant_id, s_scope)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
