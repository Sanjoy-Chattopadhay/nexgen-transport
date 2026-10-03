"""Smart-Truck's `settings` object, answered from NexGen's configuration.

The Smart-Truck code ported into NexGen reads settings as
`settings.TMS_API_BASE_URL`, `settings.GPS_RETENTION_DAYS` and so on. Rather
than edit hundreds of call sites (and risk changing behaviour), that code
imports this object instead, and every attribute it reads is mapped here to
config/services.yaml or the tenant files. The names are the same as in
Smart-Truck's config/settings.py, so its docstrings and runbooks still apply.

Nothing here is a second source of truth: each property reads the live
configuration on access.
"""

from __future__ import annotations

from pathlib import Path

from nexgen.core.config import ROOT, get_config


def _tms(key: str, default=None):
    return get_config().get(f"integrations.tms.{key}", default)


def _lane(lane: str, key: str, default=None):
    return get_config().get(f"integrations.tms.lanes.{lane}.{key}", default)


class _Settings:
    # -- product -----------------------------------------------------------
    @property
    def PROJECT_ROOT(self) -> Path:
        return ROOT

    @property
    def TMS_SOURCE_TIMEZONE(self) -> str:
        return str(get_config().product.get("timezone", "Asia/Kolkata"))

    # -- database (read by a few scripts; services use nexgen.core.db) -----
    @property
    def DB_HOST(self) -> str:
        return str(get_config().server().get("host", "localhost"))

    @property
    def DB_PORT(self) -> int:
        return int(get_config().server().get("port", 3306))

    @property
    def DB_USER(self) -> str:
        return str(get_config().credentials("default").get("user", "root"))

    @property
    def DB_PASSWORD(self) -> str:
        return str(get_config().credentials("default").get("password", ""))

    @property
    def DB_NAME(self) -> str:
        from nexgen.shared.legacy_db import current_schema_key
        return get_config().schema(current_schema_key())

    @property
    def AUTO_MIGRATE(self) -> bool:
        return False   # NexGen applies migrations itself (nexgen.core.migrate)

    # -- consignor scoping -------------------------------------------------
    @property
    def CONSIGNOR_QUERY_PARAM(self) -> str:
        return str(get_config().setting("analytics", "consignor_query_param", "consignor_id"))

    @property
    def CONSIGNOR_ROUTE_PREFIX(self) -> str:
        return str(get_config().setting("analytics", "consignor_route_prefix", "consignor"))

    @property
    def CONSIGNOR_ROLLUP_ID(self) -> int:
        return int(get_config().setting("analytics", "consignor_rollup_id", 0) or 0)

    @property
    def DEFAULT_CONSIGNOR_ID(self) -> str:
        return str(get_config().setting("analytics", "default_consignor_id", "") or "")

    # -- storage and files -------------------------------------------------
    @property
    def UPLOAD_DIR(self) -> str:
        cfg = get_config()
        p = cfg.path(cfg.setting("ingestion", "upload_dir", "uploads"))
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    @property
    def ML_MODELS_DIR(self) -> str:
        cfg = get_config()
        return str(cfg.path(cfg.setting("ml", "models_dir", "ml_models")))

    @property
    def DATA_DIR(self) -> str:
        cfg = get_config()
        return str(cfg.path(cfg.setting("ingestion", "data_dir", "data/import")))

    @property
    def TRIP_CSV_FILENAME(self) -> str:
        return str(get_config().setting("ingestion", "trip_csv_filename", "trip_data.csv"))

    @property
    def WAYPOINT_FILE_PATTERN(self) -> str:
        return str(get_config().setting("ingestion", "waypoint_file_pattern", "Waypoint_*.xls"))

    @property
    def TRIP_CSV_PATH(self) -> Path:
        return Path(self.DATA_DIR) / self.TRIP_CSV_FILENAME

    @property
    def WAYPOINT_DIR(self) -> Path:
        return Path(self.DATA_DIR)

    @property
    def GPS_RETENTION_DAYS(self) -> int:
        return int(get_config().setting("fleet", "gps.retention_days", 180) or 180)

    @property
    def BACKUP_DIR(self) -> str:
        cfg = get_config()
        return str(cfg.path(cfg.setting("platform", "backup_dir", "backups")))

    @property
    def BACKUP_KEEP(self) -> int:
        return int(get_config().setting("platform", "backup_keep", 14))

    @property
    def MYSQL_BIN_DIR(self) -> str:
        return str(get_config().setting("platform", "mysql_bin_dir", ""))

    # -- services ----------------------------------------------------------
    @property
    def API_HOST(self) -> str:
        return get_config().host

    @property
    def API_PORT(self) -> int:
        return get_config().gateway_port

    @property
    def ML_SERVICE_URL(self) -> str:
        cfg = get_config()
        return f"http://{cfg.host}:{cfg.service('ml').port}"

    @property
    def ML_SERVICE_PORT(self) -> int:
        return get_config().service("ml").port

    # -- TMS (eTrans) ------------------------------------------------------
    TMS_AUTH_URL = property(lambda self: str(_tms("auth_url", "") or ""))
    TMS_REFRESH_URL = property(lambda self: str(_tms("refresh_url", "") or ""))
    TMS_USERNAME = property(lambda self: str(_tms("username", "") or ""))
    TMS_AUTH_KEY = property(lambda self: str(_tms("auth_key", "") or ""))
    TMS_ACCESS_MODE = property(lambda self: str(_tms("access_mode", "EXT") or "EXT"))
    TMS_TOKEN_SKEW_SECONDS = property(lambda self: int(_tms("token_skew_s", 60)))
    TMS_TOKEN_TTL_SECONDS = property(lambda self: int(_tms("token_ttl_s", 600)))
    TMS_API_BASE_URL = property(lambda self: str(_tms("base_url", "") or ""))
    TMS_TRIP_ENDPOINT = property(lambda self: str(_tms("endpoints.trips_zonal", "/vehicle/track/ttaReport")))
    TMS_LOCAL_TRIP_ENDPOINT = property(
        lambda self: str(_tms("endpoints.trips_local", "/vehicle/track/tripDetailsTtaLocalReport")))
    TMS_GPS_ENDPOINT = property(lambda self: str(_tms("endpoints.gps", "/vehicle/trip-analysis/waypoints")))
    TMS_TRIP_SOURCE = property(lambda self: str(_tms("trip_source", "tta_report")))
    TMS_LOCAL_TRIP_STATUS = property(lambda self: str(_tms("local_trip_status", "C")))
    TMS_LOCAL_TRIP_DATETIME_FORMAT = property(lambda self: str(_tms("local_trip_datetime_format", "") or ""))
    TMS_ENTITY_IDS = property(lambda self: str(_tms("entity_ids", "") or ""))
    TMS_TRIP_STATUS = property(lambda self: str(_tms("trip_status", "Close")))
    TMS_CLOSED_REASON = property(lambda self: str(_tms("closed_reason", "All")))
    TMS_TRIP_DATE_FORMAT = property(lambda self: str(_tms("trip_date_format", "%Y/%m/%d")))
    TMS_GPS_DATETIME_FORMAT = property(lambda self: str(_tms("gps_datetime_format", "%d/%m/%Y %H:%M")))
    TMS_API_TIMEOUT = property(lambda self: int(_tms("timeout_s", 60)))
    TMS_GPS_WORKERS = property(lambda self: int(_tms("gps_workers", 8)))
    TMS_GPS_RETRIES = property(lambda self: int(_tms("gps_retries", 3)))
    TMS_GPS_RETRY_BACKOFF = property(lambda self: float(_tms("gps_retry_backoff", 1.5)))
    TMS_FETCH_RETRIES = property(lambda self: int(_tms("fetch_retries", 4)))
    TMS_FETCH_RETRY_BACKOFF = property(lambda self: float(_tms("fetch_retry_backoff", 3.0)))
    TMS_WAYPOINT_REFRESH_HOURS = property(lambda self: float(_tms("waypoint_refresh_hours", 6)))
    TMS_GPS_MAX_CONCURRENCY = property(lambda self: int(_tms("gps_max_concurrency", 8)))
    TMS_INGEST_DEADLOCK_RETRIES = property(lambda self: int(_tms("ingest_deadlock_retries", 3)))
    TMS_MAX_WINDOW_HOURS = property(lambda self: int(_tms("max_window_hours", 24)))
    # The fresh-start floor: nothing before it is fetched or reported missing.
    TMS_START_FROM = property(lambda self: str(_tms("start_from", "") or "").strip())
    TMS_LAG_ALERT_INTERVALS = property(lambda self: float(_tms("lag_alert_intervals", 3)))
    TMS_BOOT_CATCHUP = property(lambda self: bool(int(_tms("boot_catchup", 1) or 0)))
    TMS_BOOT_CATCHUP_DELAY_SECONDS = property(lambda self: int(_tms("boot_catchup_delay_s", 60)))
    TMS_BOOT_CATCHUP_MIN_GAP_MINUTES = property(lambda self: int(_tms("boot_catchup_min_gap_min", 10)))
    # Lanes: these are the SEED values; the live schedule is the one saved from
    # the Ingestion page (tta_sync_config), exactly as in Smart-Truck.
    TMS_SYNC_ENABLED = property(lambda self: bool(_lane("zonal", "enabled", False)))
    TMS_SYNC_INTERVAL_MINUTES = property(lambda self: int(_lane("zonal", "every_min", 30)))
    TMS_SYNC_LOOKBACK_MINUTES = property(lambda self: int(_lane("zonal", "lookback_min", 120)))
    TMS_SYNC_ENABLED_LOCAL = property(lambda self: bool(_lane("local", "enabled", False)))
    TMS_SYNC_INTERVAL_MINUTES_LOCAL = property(lambda self: int(_lane("local", "every_min", 20)))
    TMS_SYNC_LOOKBACK_MINUTES_LOCAL = property(lambda self: int(_lane("local", "lookback_min", 180)))

    @property
    def TMS_ENTITY_ID_LIST(self) -> list[int]:
        out = []
        for part in self.TMS_ENTITY_IDS.split(","):
            part = part.strip()
            if part:
                try:
                    out.append(int(part))
                except ValueError:
                    pass
        return out

    def sync_ready_for(self, source: str | None = None, entity_ids: list | None = None) -> bool:
        credentials = bool(self.TMS_AUTH_URL and self.TMS_API_BASE_URL
                           and self.TMS_USERNAME and self.TMS_AUTH_KEY)
        src = (source or self.TMS_TRIP_SOURCE or "").strip().lower()
        if src == "local_report":
            return credentials
        ids = entity_ids if entity_ids is not None else self.TMS_ENTITY_ID_LIST
        return credentials and bool(ids)

    @property
    def TMS_SYNC_READY(self) -> bool:
        return self.sync_ready_for(None)

    # -- logging -----------------------------------------------------------
    LOG_LEVEL = property(lambda self: str(get_config().get("logging.level", "INFO")))
    LOG_FORMAT = property(lambda self: str(get_config().get("logging.format", "%(message)s")))

    # -- LLM ---------------------------------------------------------------
    OPENAI_API_KEY = property(lambda self: str(get_config().get("integrations.llm.openai_api_key", "") or ""))
    AZURE_OPENAI_API_KEY = property(lambda self: str(get_config().get("integrations.llm.api_key", "") or ""))
    AZURE_OPENAI_ENDPOINT = property(lambda self: str(get_config().get("integrations.llm.endpoint", "") or ""))
    AZURE_OPENAI_CHAT_DEPLOYMENT_NAME = property(
        lambda self: str(get_config().get("integrations.llm.chat_deployment", "gpt-4.1")))
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME = property(
        lambda self: str(get_config().get("integrations.llm.embedding_deployment", "text-embedding-3-small")))
    AZURE_OPENAI_VERSION = property(
        lambda self: str(get_config().get("integrations.llm.api_version", "2024-12-01-preview")))

    @property
    def AZURE_OPENAI_READY(self) -> bool:
        return bool(self.AZURE_OPENAI_API_KEY and self.AZURE_OPENAI_ENDPOINT)

    # -- ML subscription keys ----------------------------------------------
    ML_API_KEYS = property(lambda self: str(get_config().setting("ml", "api_keys", "") or ""))
    ML_API_AUTH_ENABLED = property(
        lambda self: str(get_config().setting("ml", "api_auth_enabled", "0")) in ("1", "True", "true"))


settings = _Settings()
