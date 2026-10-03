-- Routing's own data version (its answers are cached on it, beside the
-- geofence version they also depend on).
CREATE TABLE IF NOT EXISTS route_state (
    s_key       VARCHAR(64)  NOT NULL,
    s_value     VARCHAR(255) NULL,
    dt_updated  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (s_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Smart-Truck's per-trip weather endpoint is served by analytics (with its
-- cost model and its own weather cache), so routing does not keep one.
DROP TABLE IF EXISTS tta_weather_cache;
