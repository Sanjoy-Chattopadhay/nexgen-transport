"""Geo-Fencing's settings, built from NexGen's services.yaml.

The engine reads `settings.detector`, `settings.fit`, `settings.osrm`, ... as
it always did; the values now come from services.yaml (geofence and routing
settings, integrations.osrm) instead of .env, with the same defaults.
`geo_db` / `src_db` point at the schema of the service hosting the engine
(geofence or routing); the "source" tables there are alias views over the
fleet service's published v1 views, opened read-only by src_conn().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nexgen.core.config import ROOT, get_config


@dataclass(frozen=True)
class DetectorConfig:
    hysteresis_m: float = 25.0
    confirm_seconds: float = 90.0
    escape_m: float = 250.0
    max_plausible_kmph: float = 150.0
    max_gap_seconds: float = 1800.0
    default_tolerance_m: float = 0.0
    adaptive_band: bool = True
    band_fraction: float = 0.5
    min_band_m: float = 5.0

    def band_for(self, fence) -> float:
        r = getattr(fence, "inradius_m", None)
        if not self.adaptive_band or r is None:
            return self.hysteresis_m
        r = float(r) + float(getattr(fence, "tolerance_m", 0.0) or 0.0)
        band = min(self.hysteresis_m, max(self.min_band_m, self.band_fraction * r), 0.8 * r)
        return max(0.5, band)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class FitConfig:
    variant: str = "fitted"
    still_kmph: float = 5.0
    window_fixes: int = 3
    window_seconds: float = 900.0
    still_spread_m: float = 25.0
    spike_m: float = 100.0
    spike_base_m: float = 30.0
    spike_max_dt_s: float = 150.0
    stop_min_seconds: float = 180.0
    gap_min_seconds: float = 300.0
    gap_moved_m: float = 300.0

    def as_dict(self) -> dict:
        return {f"fit_{k}" if k != "variant" else "fit_variant": v for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class SchedulerConfig:
    every_minutes: float = 15.0
    sync_fleet: bool = False          # the feed is the fleet service's; nothing to copy
    trip_sync_days: int = 45
    workers: int = max(1, min(4, (os.cpu_count() or 2) - 1))
    default_window_days: int = 90


@dataclass(frozen=True)
class RouteConfig:
    cost_per_km: float = 28.0
    cost_per_hour: float = 150.0
    detention_per_hour: float = 150.0
    free_hours: float = 4.0
    revenue_per_km: float | None = None
    tolerance_pct: float = 3.0
    truck_time_factor: float = 1.25
    rest_min_per_4h: float = 30.0
    min_plan_km: float = 10.0
    off_m: float = 300.0
    on_m: float = 150.0
    confirm_s: float = 180.0
    escape_m: float = 2000.0
    terminal_m: float = 1500.0
    long_stop_s: float = 900.0
    learned_off_m: float = 600.0
    learned_on_m: float = 300.0
    learned_terminal_m: float = 3000.0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Settings:
    geo_db: dict = field(default_factory=dict)
    src_db: dict = field(default_factory=dict)
    src_gps_table: str = "tta_trip_gps"
    src_trip_table: str = "tta_trips"
    masters_dir: Path = ROOT / "Masters"
    site_csv: str = "TBL_SITE_GEO_20260128.CSV"
    site_dtls_csv: str = "TBL_SITE_GEO_DTLS_20260128.CSV"
    site_polygon_csv: str = "tbl_site_polygon.csv"
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    fit: FitConfig = field(default_factory=FitConfig)
    osrm: object = None
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    routing: RouteConfig = field(default_factory=RouteConfig)
    prefilter_enabled: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8104
    out_dir: Path = ROOT / "out"

    @property
    def geo_db_name(self) -> str:
        return self.geo_db["database"]


def _pick(cls, values: dict):
    names = set(cls.__dataclass_fields__)
    return cls(**{k: v for k, v in (values or {}).items() if k in names})


def _db_params() -> dict:
    from nexgen.core.db import _params
    from nexgen.shared.legacy_db import current_schema_key
    try:
        key = current_schema_key()
    except RuntimeError:
        key = "geofence"
    return {**_params(key), "charset": "utf8mb4"}


def load_osrm():
    from nexgen.shared.geoengine.osrm.client import OsrmConfig
    o = get_config().get("integrations.osrm", {}) or {}
    return OsrmConfig(url=(str(o.get("url") or "").strip() or None), profile=str(o.get("profile", "driving")),
                      timeout_s=float(o.get("timeout_s", 15.0)), match_chunk=int(o.get("match_chunk", 100)),
                      match_overlap=int(o.get("match_overlap", 10)), radius_m=float(o.get("radius_m", 15.0)),
                      max_snap_m=float(o.get("max_snap_m", 30.0)), min_confidence=float(o.get("min_confidence", 0.0)),
                      snap_moves=bool(o.get("snap_moves", True)), route_gaps=bool(o.get("route_gaps", True)))


def load_fit() -> FitConfig:
    return _pick(FitConfig, get_config().setting("geofence", "fit", {}))


def load_settings() -> Settings:
    cfg = get_config()
    geo = cfg.service("geofence").settings
    ing = cfg.service("ingestion").settings
    refresh = dict(geo.get("refresh") or {})
    if not refresh.get("workers"):
        refresh.pop("workers", None)
    routing = dict(cfg.service("routing").settings)
    if not routing.get("revenue_per_km"):
        routing["revenue_per_km"] = None
    params = _db_params()
    return Settings(
        geo_db=params, src_db=params,
        masters_dir=cfg.path(ing.get("masters_dir", "Masters")),
        site_csv=str(ing.get("site_csv", "TBL_SITE_GEO_20260128.CSV")),
        site_dtls_csv=str(ing.get("site_dtls_csv", "TBL_SITE_GEO_DTLS_20260128.CSV")),
        site_polygon_csv=str(ing.get("site_polygon_csv", "tbl_site_polygon.csv")),
        detector=_pick(DetectorConfig, geo.get("detector")),
        fit=load_fit(), osrm=load_osrm(),
        scheduler=_pick(SchedulerConfig, refresh),
        routing=_pick(RouteConfig, routing),
        prefilter_enabled=bool((geo.get("prefilter") or {}).get("enabled", True)),
        api_host=cfg.host, api_port=cfg.service("geofence").port,
    )


settings = load_settings()
