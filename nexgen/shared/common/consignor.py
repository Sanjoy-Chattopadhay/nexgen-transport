"""
Consignor scoping — the single source of truth for filtering API responses to
one consignor (multi-tenant by consignor, no auth).

Design goals (see task brief):
  * Route/param based, optional. Absent  -> all consignors (rollup).
  * Modular: every endpoint depends on `consignor_scope`, and every query uses
    one of the two small SQL helpers. Adding auth / user->consignor mapping
    later means changing only how the scope is *resolved*, not every query.

Two filtering styles, because the app has two kinds of tables:
  1. Base fact tables carry the consignor id directly
       - trips.cnr_id           -> use `base_clause(scope, "cnr_id")`
       - tta_trips.i_cnr_id      -> use `base_clause(scope, "i_cnr_id")`
     Unscoped => no clause (all rows).
  2. Pre-aggregated summary tables carry a `cnr_id` dimension where
     CONSIGNOR_ROLLUP_ID (0) is the "all consignors" rollup row
       - driver_summary / route_summary / vehicle_summary / daily_fleet_stats
       - use `summary_clause(scope)` -> always adds `cnr_id = <id-or-0>`.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Query

from nexgen.shared.legacy_db import get_db
from nexgen.shared.legacy_settings import settings

ROLLUP_ID = settings.CONSIGNOR_ROLLUP_ID


@dataclass(frozen=True)
class ConsignorScope:
    """Resolved consignor scope for one request.

    id   -> the consignor id when scoped, else None (all consignors).
    name -> consignor display name when scoped, else None.
    """
    id: Optional[int] = None
    name: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.id is not None

    @property
    def summary_id(self) -> int:
        """cnr_id to match in summary tables (rollup sentinel when unscoped)."""
        return self.id if self.id is not None else ROLLUP_ID

    def allows(self, cnr_id) -> bool:
        """True when a row owned by `cnr_id` is visible under this scope.

        Unscoped -> everything is visible. Scoped -> only this consignor's rows.
        Used to guard per-record endpoints so one consignor can never read
        another consignor's record by id.
        """
        return self.id is None or cnr_id == self.id


# ----------------------------------------------------------------------
# Resolution (FastAPI dependency)
# ----------------------------------------------------------------------

def resolve_consignor(conn, raw) -> ConsignorScope:
    """Validate a raw consignor id against the consignors table.

    Empty / "0" / None  -> unscoped (all consignors).
    Unknown id          -> 404 (fail loud so a bad URL isn't silently 'all').
    """
    if raw is None or str(raw).strip() in ("", "0"):
        return ConsignorScope()
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Invalid consignor id: {raw!r}")
    with conn.cursor() as cur:
        cur.execute("SELECT i_cnr_id, s_cnr_name FROM consignors WHERE i_cnr_id = %s", (cid,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Consignor {cid} not found")
    return ConsignorScope(id=row["i_cnr_id"], name=row["s_cnr_name"])


def consignor_scope(
    # Internal arg name deliberately differs from "consignor_id" so it never
    # collides with a `consignor_id` PATH param (e.g. /consignors/{consignor_id}).
    consignor_id_q: Optional[str] = Query(None, alias=settings.CONSIGNOR_QUERY_PARAM),
    conn=Depends(get_db),
) -> ConsignorScope:
    """Dependency: inject the validated ConsignorScope into any endpoint."""
    return resolve_consignor(conn, consignor_id_q)


# ----------------------------------------------------------------------
# SQL helpers — return (clause, params) fragments to splice into WHERE lists
# ----------------------------------------------------------------------

def base_clause(scope: ConsignorScope, column: str = "cnr_id", alias: str = "") -> tuple[str, list]:
    """Filter a base fact table by its consignor column.

    Unscoped -> ('', []) so all rows are returned.
    Scoped   -> ('<alias.>col = %s', [id]).
    """
    if not scope.active:
        return "", []
    col = f"{alias}.{column}" if alias else column
    return f"{col} = %s", [scope.id]


def summary_clause(scope: ConsignorScope, column: str = "cnr_id", alias: str = "") -> tuple[str, list]:
    """Filter a summary table by its cnr_id dimension (rollup row when unscoped)."""
    col = f"{alias}.{column}" if alias else column
    return f"{col} = %s", [scope.summary_id]


def enforce_tta_trip_scope(conn, scope: ConsignorScope, trip_no: int) -> None:
    """Guard a per-trip TTA endpoint: 404 when the trip is out of the active scope.

    TTA GPS/analysis tables carry no consignor column of their own; the trip's
    owner lives on `tta_trips.i_cnr_id`. Rather than let a consignor fetch another
    consignor's trip (and its GPS/analysis) by guessing `i_trip_no`, resolve the
    owner and 404 on a mismatch. Unscoped requests are never blocked.
    """
    if not scope.active:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT i_cnr_id FROM tta_trips WHERE i_trip_no = %s", (trip_no,))
        row = cur.fetchone()
    if not row or not scope.allows(row["i_cnr_id"]):
        raise HTTPException(404, f"Trip {trip_no} not found")
