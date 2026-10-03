"""Calculation manual — how every figure on the platform is produced.

    GET /api/v1/manual   -> feed mapping, derivations, a worked example, coverage

Shares the dashboard filter dependency, so the worked example and the coverage
table describe the same window the reader is looking at elsewhere. That matters
more than it sounds: coverage is not a constant. The zonal lane reports delivery
status and the local lane does not, so "what fraction of trips carry an on-time
verdict" changes with the date range, and a manual that quoted a fixed number
would be wrong for most windows.
"""
from fastapi import APIRouter, Depends

from nexgen.services.analytics.api.tta_dashboard import dashboard_filters
from nexgen.shared.legacy_db import get_db
from nexgen.services.analytics.lib import calculation_manual as svc

router = APIRouter(prefix="/manual", tags=["Manual"])


@router.get("")
def calculation_manual(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Every calculation, traced from the raw API JSON to the number on screen.

    Four blocks:

    * `feed` — the JSON keys the upstream API sends, the column each lands in,
      and how the text durations are parsed into minutes.
    * `derivations` — every computed column with its formula, inputs, unit, and
      the rule that makes it NULL rather than zero.
    * `example` — one real trip carried through all of it, so the arithmetic can
      be checked rather than believed.
    * `coverage` — how many trips actually carry each input, split by feed lane.
      This is the denominator behind every percentage on the platform.
    """
    return svc.manual(conn, f)
