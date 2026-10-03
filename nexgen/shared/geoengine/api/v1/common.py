"""Helpers shared by the v1 routers."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException

FACILITY = ("micro", "site", "campus")

SCALE_SQL = ("CASE WHEN {a} < 10000 THEN 'micro' WHEN {a} < 1000000 THEN 'site' "
             "WHEN {a} < 100000000 THEN 'campus' ELSE 'regional' END")


def scale_sql(area_col: str) -> str:
    return SCALE_SQL.format(a=area_col)


def resolve_run(cur, run: int | None) -> int:
    """The run a request reads: the one named, else the published one."""
    if run:
        cur.execute("SELECT i_run_id FROM geo_run WHERE i_run_id=%s AND s_status='ok'", (run,))
        if not cur.fetchone():
            raise HTTPException(404, f"no finished run {run}")
        return run
    cur.execute("""SELECT i_run_id FROM geo_run WHERE s_status='ok'
                    ORDER BY b_published DESC, i_run_id DESC LIMIT 1""")
    row = cur.fetchone()
    if not row:
        raise HTTPException(503, "No finished run yet. Run `python -m nexgen.shared.geoengine.cli run --publish`.")
    return row["i_run_id"]


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(400, f"bad date {value!r}; use YYYY-MM-DD") from None


def day_bounds(d_from: str | None, d_to: str | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive calendar-day filter as a half-open datetime range."""
    a = parse_day(d_from)
    b = parse_day(d_to)
    return (datetime.combine(a, datetime.min.time()) if a else None,
            datetime.combine(b + timedelta(days=1), datetime.min.time()) if b else None)


def order_clause(sort: str | None, order: str | None, allowed: dict[str, str], default: str) -> str:
    """ORDER BY from a whitelist; the client never supplies SQL."""
    col = allowed.get(sort or "", allowed[default])
    direction = "ASC" if (order or "").lower() == "asc" else "DESC"
    return f"ORDER BY {col} {direction}"


def num(v):
    """Decimal -> int/float, for aggregate columns MySQL returns as Decimal."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def clean(row: dict) -> dict:
    return {k: num(v) for k, v in row.items()}


def rows(cur) -> list[dict]:
    return [clean(r) for r in cur.fetchall()]


def paged(total: int, page: int, page_size: int, items: list) -> dict:
    return {"total": int(total or 0), "page": page, "page_size": page_size,
            "pages": max(1, -(-int(total or 0) // page_size)), "items": items}


def percentile(values: list, q: float):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)
