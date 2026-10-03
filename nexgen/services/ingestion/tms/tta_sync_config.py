"""
Screen-editable ETL sync configuration, persisted in MySQL (app_settings).

One row PER LANE — `tta_sync_config` for zonal, `tta_sync_config_local` for
local — so the two schedules are stored and edited independently: changing the
local interval can never disturb the zonal one. The live schedule (enabled /
interval / lookback) lives here so it can be changed from the ETL screen at
runtime without touching .env or restarting; the .env TMS_SYNC_* values are
only the initial seed.

This supersedes the single `trip_source` switch, which let one job pull from
one feed at a time. That value is still read once, on the zonal row, and used
to seed whichever lane it named — see migrate_legacy_source() — so an existing
install that had selected "local_report" comes up with the local lane enabled
instead of silently reverting to zonal.

Entity ids: the zonal lane pulls per entity (DB override first, .env
TMS_ENTITY_IDS as the fallback). The local report is not entity-scoped, so the
field is ignored for that lane.
"""

import logging

from nexgen.shared.legacy_settings import settings
from nexgen.shared.analysis.tta_config import get_setting, save_setting
from nexgen.shared.feed.tta_lanes import LANES, ZONAL, Lane, get_lane

logger = logging.getLogger(__name__)

# Kept for backward compatibility: the API still reports the legacy source ids
# so an older client reading /settings does not break.
TRIP_SOURCES = ("tta_report", "local_report")


def _defaults(lane: Lane) -> dict:
    """Seed values from .env — used until a row is saved from the screen."""
    return {
        "enabled": lane.default_enabled,
        "interval_minutes": lane.default_interval,
        "lookback_minutes": lane.default_lookback,
        "entity_ids": [],   # empty => fall back to .env TMS_ENTITY_IDS
    }


def _as_lane(lane: Lane | str | None) -> Lane:
    return lane if isinstance(lane, Lane) else get_lane(lane)


def get_sync_config(conn, lane: Lane | str | None = ZONAL) -> dict:
    """Effective config for one lane: DB row merged over .env-derived defaults."""
    ln = _as_lane(lane)
    return get_setting(conn, ln.config_key, _defaults(ln))


def save_sync_config(conn, cfg: dict, lane: Lane | str | None = ZONAL) -> dict:
    """Validate + persist one lane's screen-edited config. Returns the stored value."""
    ln = _as_lane(lane)
    defaults = _defaults(ln)
    stored = get_setting(conn, ln.config_key, defaults)

    out = dict(stored)

    if "enabled" in cfg:
        out["enabled"] = bool(cfg["enabled"])

    if "interval_minutes" in cfg:
        try:
            iv = int(cfg["interval_minutes"])
        except (TypeError, ValueError):
            raise ValueError("interval_minutes must be an integer")
        if not (1 <= iv <= 10080):  # 1 min .. 1 week
            raise ValueError("interval_minutes must be between 1 and 10080")
        out["interval_minutes"] = iv

    if "lookback_minutes" in cfg:
        try:
            lb = int(cfg["lookback_minutes"])
        except (TypeError, ValueError):
            raise ValueError("lookback_minutes must be an integer")
        if not (0 <= lb <= 100000):
            raise ValueError("lookback_minutes must be >= 0")
        out["lookback_minutes"] = lb

    if "entity_ids" in cfg and cfg["entity_ids"] is not None:
        ids = cfg["entity_ids"]
        if isinstance(ids, str):
            ids = [p.strip() for p in ids.split(",")]
        cleaned = []
        for i in ids:
            try:
                cleaned.append(int(i))
            except (TypeError, ValueError):
                raise ValueError(f"entity id '{i}' is not an integer")
        out["entity_ids"] = cleaned

    save_setting(conn, ln.config_key, out)
    logger.info("ETL sync config saved for lane %s: %s", ln.key, out)
    return out


def effective_entity_ids(cfg: dict) -> list[int]:
    """Entity ids to pull: DB override if set, else .env TMS_ENTITY_IDS."""
    ids = cfg.get("entity_ids") or []
    return ids if ids else settings.TMS_ENTITY_ID_LIST


def all_sync_configs(conn) -> dict[str, dict]:
    """Every lane's effective config, keyed by lane — used to (re)build the
    scheduler on startup and after any edit."""
    return {key: get_sync_config(conn, lane) for key, lane in LANES.items()}


def migrate_legacy_source(conn) -> dict | None:
    """One-time carry-over from the single-source switch to two lanes.

    Before lanes existed, one job pulled from whichever feed `trip_source`
    named. If that value survives on the zonal config row, honour the operator's
    last choice: the named lane inherits the schedule that was running, and the
    key is dropped so this never fires twice. An install that had chosen
    "local_report" would otherwise come back up syncing zonal — quietly pulling
    the wrong feed.

    Returns the migration summary, or None when there was nothing to migrate.
    """
    zonal = LANES[ZONAL]
    # get_setting only returns keys present in the default, so every field this
    # migration reads (or has to write back) must be named here.
    probe = {**_defaults(zonal), "trip_source": None}
    raw = get_setting(conn, zonal.config_key, probe)
    source = raw.get("trip_source")
    if not source:
        return None

    target = get_lane(source)
    interval = raw.get("interval_minutes")
    lookback = raw.get("lookback_minutes")
    enabled = bool(raw.get("enabled"))

    # Drop the now-meaningless key from the zonal row. Built here (not written
    # yet) because the zonal row is rewritten exactly once below — an earlier
    # version disabled the lane and then wrote this dict back over the top,
    # silently re-enabling it.
    cleaned = {k: v for k, v in raw.items() if k != "trip_source"}

    if target.key != ZONAL:
        # The running schedule belonged to the local feed — move it there, and
        # leave the zonal lane switched OFF rather than inventing a cadence for
        # a feed the operator had not selected.
        carried = {"enabled": enabled}
        if interval is not None:
            carried["interval_minutes"] = int(interval)
        if lookback is not None:
            carried["lookback_minutes"] = int(lookback)
        save_sync_config(conn, carried, target)
        cleaned["enabled"] = False

    save_setting(conn, zonal.config_key, cleaned)
    logger.info("Migrated legacy trip_source=%s onto lane %s", source, target.key)
    return {"from_source": source, "to_lane": target.key, "enabled": enabled}
