"""
ETL lane registry — the ONE place that knows how the two eTrans feeds differ.
-----------------------------------------------------------------------------
The upstream publishes trips in two classifications:

  zonal  POST /vehicle/track/ttaReport/{entity_id}      (source id "tta_report")
         56 fields, entity-scoped, carries all derived metrics.

  local  POST /vehicle/track/tripDetailsTtaLocalReport  (source id "local_report")
         26 fields, NOT entity-scoped, no derived metrics.

Both resolve GPS through the SAME endpoint, so a lane differs only in how its
trip list is requested, how far its watermark may advance, and which
`s_trip_class` tag its rows carry.

This replaces the earlier single `trip_source` SWITCH (one job that pulled from
one feed at a time) with two lanes that run side by side on their own
schedules. The old source ids are kept as aliases so a stored
`trip_source: "local_report"` config keeps resolving to the right lane.

Fetching itself still lives in tta_api_sync.fetch_trips / fetch_trips_local —
this module only says WHICH one a lane uses, and with what schedule.
"""

from __future__ import annotations

from dataclasses import dataclass

import os

from nexgen.shared.legacy_settings import settings

ZONAL = "zonal"
LOCAL = "local"

# Source ids used by the previous single-source config, mapped onto lanes.
SOURCE_ALIASES = {
    "tta_report": ZONAL,
    "local_report": LOCAL,
    "ttareport": ZONAL,
    "localreport": LOCAL,
}


@dataclass(frozen=True)
class Lane:
    key: str                    # "zonal" | "local" — also tta_trips.s_trip_class
    source_id: str              # legacy trip_source value this lane came from
    label: str                  # shown on the ETL screen
    description: str
    endpoint: str               # path under TMS_API_BASE_URL
    entity_scoped: bool         # zonal pulls per entity id; local does not
    config_key: str             # app_settings row holding the live schedule
    watermark_key: str          # app_settings row holding last-synced
    job_id: str                 # APScheduler job id
    default_enabled: bool
    default_interval: int       # minutes
    default_lookback: int       # minutes
    # Trip field the UPSTREAM filters its window on, when that field is
    # published LATE. Set => the watermark advances only as far as the newest
    # value actually seen, so rows the upstream has not published yet are never
    # stepped over. None => plain wall-clock watermark.
    watermark_field: str | None = None
    # How far behind wall-clock this lane's watermark sits even when it is
    # perfectly healthy. A lane that advances to the newest value the SOURCE
    # has published can never catch up to 'now' — the source itself is behind.
    # Health checks add this to the lag budget; without it such a lane alerts
    # permanently, and a permanently-red alert is one nobody reads.
    expected_source_lag_minutes: int = 0

    @property
    def url(self) -> str:
        base = settings.TMS_API_BASE_URL.rstrip("/")
        ep = self.endpoint if self.endpoint.startswith("/") else "/" + self.endpoint
        return base + ep

    def ready(self, entity_ids: list | None = None) -> bool:
        """True when enough is configured for THIS lane to log in and fetch.

        Delegates to settings.sync_ready_for so readiness stays defined in one
        place: credentials for both lanes, plus entity ids for the entity-scoped
        one.

        `entity_ids` MUST be the effective list (DB config first, .env as the
        fallback) whenever the caller has a connection — see
        tta_sync_config.effective_entity_ids. Entity ids are normally set from
        the ETL screen, so judging by the .env seed alone reports a perfectly
        valid screen-configured lane as "not configured" and silently keeps its
        job from ever being scheduled.
        """
        return settings.sync_ready_for(self.source_id, entity_ids)


LANES: dict[str, Lane] = {
    ZONAL: Lane(
        key=ZONAL,
        source_id="tta_report",
        label="Zonal trips",
        description="TTA Report, per consignor — full trip record with derived metrics.",
        endpoint=settings.TMS_TRIP_ENDPOINT,
        entity_scoped=True,
        config_key="tta_sync_config",       # unchanged — the existing row keeps working
        watermark_key="tta_api_sync",       # unchanged — no re-backfill on upgrade
        job_id="tms_api_sync",
        default_enabled=settings.TMS_SYNC_ENABLED,
        default_interval=settings.TMS_SYNC_INTERVAL_MINUTES,
        default_lookback=settings.TMS_SYNC_LOOKBACK_MINUTES,
        # The zonal report takes DATE-only bounds, so a re-query re-reads whole
        # days and a publication lag cannot open a gap.
        watermark_field=None,
        expected_source_lag_minutes=0,   # date-bounded window: no structural lag
    ),
    LOCAL: Lane(
        key=LOCAL,
        source_id="local_report",
        label="Local trips",
        description="TTA Local Report — short-haul trips, no entity scope, no derived metrics.",
        endpoint=settings.TMS_LOCAL_TRIP_ENDPOINT,
        entity_scoped=False,
        config_key="tta_sync_config_local",
        watermark_key="tta_api_sync_local",
        job_id="tms_api_sync_local",
        default_enabled=settings.TMS_SYNC_ENABLED_LOCAL,
        default_interval=settings.TMS_SYNC_INTERVAL_MINUTES_LOCAL,
        default_lookback=settings.TMS_SYNC_LOOKBACK_MINUTES_LOCAL,
        # Measured against the live feed: this report filters on dt_trip_start
        # and publishes LATE — at 17:53 the newest dt_trip_start on offer was
        # 15:33. A wall-clock watermark would march past trips that had not been
        # published yet and lose them permanently.
        watermark_field="dt_trip_start",
        # Measured against the live feed: newest dt_trip_start on offer ran
        # ~2.3 h behind wall-clock. Budget 3 h so normal publication delay is
        # not reported as the lane falling behind.
        expected_source_lag_minutes=int(
            os.getenv("TMS_LOCAL_SOURCE_LAG_MINUTES", "180")),
    ),
}

LANE_KEYS: tuple[str, ...] = (ZONAL, LOCAL)


def get_lane(key: str | None) -> Lane:
    """Resolve a lane by key OR by a legacy trip_source id, defaulting to zonal."""
    k = (key or ZONAL).strip().lower()
    k = SOURCE_ALIASES.get(k, k)
    return LANES.get(k, LANES[ZONAL])


def is_lane(key: str | None) -> bool:
    k = (key or "").strip().lower()
    return k in LANES or k in SOURCE_ALIASES
