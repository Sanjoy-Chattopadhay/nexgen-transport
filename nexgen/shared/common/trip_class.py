"""
Trip-class scoping — filter API responses to one upstream classification.

The two eTrans trip feeds (zonal / local) land in the same tables tagged by
`tta_trips.s_trip_class`, so "show me only local trips" is a filter, not a
different data source. This module is the single source of truth for that
filter, deliberately mirroring backend/app/core/consignor.py:

  * query-param based and OPTIONAL — absent means ALL classes, which is the
    pre-existing behaviour and keeps every old URL working;
  * one dependency (`trip_class_scope`) + one SQL helper (`class_clause`), so
    adding a third class later touches this file and tta_lanes.py only.

Unlike consignor scope this is a VIEW preference, not a tenancy boundary: it
narrows what a user is looking at, and is never a security control. Per-record
endpoints therefore do NOT 404 out-of-class records — opening a specific trip by
number always works, whichever filter is active.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Query

from nexgen.shared.feed.tta_lanes import LANE_KEYS

# Values accepted on the wire. "all" and "" both mean unfiltered.
ALL = "all"
VALID = set(LANE_KEYS) | {ALL, ""}

QUERY_PARAM = "trip_class"


@dataclass(frozen=True)
class TripClassScope:
    """Resolved trip-class filter for one request.

    value -> "zonal" | "local" when filtered, else None (all classes).
    """
    value: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.value is not None

    @property
    def label(self) -> str:
        return self.value or ALL


def resolve_trip_class(raw) -> TripClassScope:
    """Validate a raw trip-class value.

    Empty / "all" / None -> unfiltered. Unknown -> 422 (fail loud, so a typo
    isn't silently served as 'everything').
    """
    if raw is None:
        return TripClassScope()
    v = str(raw).strip().lower()
    if v in ("", ALL):
        return TripClassScope()
    if v not in VALID:
        raise HTTPException(
            422, f"Invalid trip_class {raw!r}. Valid: {', '.join(sorted(LANE_KEYS))}, all")
    return TripClassScope(value=v)


def trip_class_scope(
    trip_class: Optional[str] = Query(None, description="zonal | local | all"),
) -> TripClassScope:
    """Dependency: inject the validated TripClassScope into any endpoint."""
    return resolve_trip_class(trip_class)


def class_clause(scope: TripClassScope, column: str = "s_trip_class",
                 alias: str = "") -> tuple[str, list]:
    """Filter a table carrying the class column.

    Unfiltered -> ('', []) so all rows are returned.
    Filtered   -> ('<alias.>col = %s', [value]).
    """
    if not scope.active:
        return "", []
    col = f"{alias}.{column}" if alias else column
    return f"{col} = %s", [scope.value]
