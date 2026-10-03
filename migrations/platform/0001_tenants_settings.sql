-- nx_platform: tenants (clients) and their settings.
--
-- A tenant's settings come from config/tenants/_default.yaml, then its own
-- file, then the overrides below (made on the Admin page). Every override is
-- versioned and audited, so "who changed the overspeed limit, and when" has an
-- answer, and a KPI run records the version it was computed with.

CREATE TABLE IF NOT EXISTS tenant (
    i_tenant_id  SMALLINT UNSIGNED NOT NULL,
    s_code       VARCHAR(64)       NOT NULL,
    s_name       VARCHAR(255)      NOT NULL,
    s_timezone   VARCHAR(64)       NOT NULL DEFAULT 'Asia/Kolkata',
    b_active     TINYINT(1)        NOT NULL DEFAULT 1,
    dt_created   DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dt_modified  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id),
    UNIQUE KEY idx_tenant_code (s_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tenant_setting (
    i_tenant_id  SMALLINT UNSIGNED NOT NULL,
    s_key        VARCHAR(128)      NOT NULL,      -- dotted path, e.g. thresholds.overspeed_kmph
    j_value      JSON              NOT NULL,
    i_version    INT UNSIGNED      NOT NULL DEFAULT 1,
    s_changed_by VARCHAR(100)      NULL,
    dt_changed   DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id, s_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS setting_audit (
    i_audit_id   BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT,
    i_tenant_id  SMALLINT UNSIGNED NOT NULL,
    s_key        VARCHAR(128)      NOT NULL,
    j_old        JSON              NULL,
    j_new        JSON              NULL,            -- NULL = override removed
    s_changed_by VARCHAR(100)      NULL,
    dt_changed   DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (i_audit_id),
    KEY idx_audit_tenant (i_tenant_id, dt_changed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The tenant's config version: bumped on every override change, recorded by
-- every KPI and detection run.
CREATE TABLE IF NOT EXISTS tenant_config_version (
    i_tenant_id  SMALLINT UNSIGNED NOT NULL,
    i_version    INT UNSIGNED      NOT NULL DEFAULT 1,
    dt_changed   DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
