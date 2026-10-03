"""
Data-gap register — the durable record of spans this ETL knows it is missing.
-----------------------------------------------------------------------------
A lane can only reach back TMS_MAX_WINDOW_HOURS in one run. When it has been
down longer than that, the run clamps its window to the cap and the span before
the clamp is REAL MISSING DATA.

Before this module that fact lived in three places, and all three forgot it
within a day:
  * `_last_run[lane]["data_gap"]` — an in-process dict, overwritten by the next
    clean tick (~30 min) and empty after any restart;
  * `tta_sync_runs` — no gap column, and window_start is written AFTER the
    clamp, so the row looks like an ordinary 24 h window;
  * `logs/etl_events_*.jsonl` — same-day only, purged on the first write of the
    next day.

So half an hour after coming back from a two-week outage the ETL screen showed
the lane green — fresh watermark, low lag — with two weeks missing underneath.
Being nagged is annoying; being told everything is fine while it is not is
worse, and that is what this table exists to prevent.

Declining is therefore a RECORDED DECISION, not amnesia. A gap leaves the
banner only when somebody chose that — dismissed or snoozed, with a timestamp —
or when a backfill actually covered it. `dismissed` still counts on the lane
card, so a lane carrying a known hole never renders as plain healthy.

States
    open       detected, nobody has decided yet      -> banner
    snoozed    "remind me later", until snooze_until -> banner again after that
    dismissed  explicitly declined                   -> no banner, still counted
    recovered  a backfill covered the whole span     -> closed
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Two detections this close together describe the same outage, so they are
# merged into one row instead of accumulating near-duplicate slivers. A held
# watermark re-detects the same span with a slightly later end on every run;
# without the merge a week of holding would file a week of rows.
_MERGE_TOLERANCE = timedelta(hours=1)

OPEN, SNOOZED, DISMISSED, RECOVERED = "open", "snoozed", "dismissed", "recovered"
# The states that still mean "data is missing" — anything but `recovered`.
UNRESOLVED = (OPEN, SNOOZED, DISMISSED)

_DDL = """
CREATE TABLE IF NOT EXISTS tta_data_gaps (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    lane VARCHAR(16) NOT NULL,
    gap_start DATETIME NOT NULL,       -- first instant NOT covered
    gap_end DATETIME NOT NULL,         -- last instant NOT covered
    hours DECIMAL(10,1) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'open',
    detected_at DATETIME NOT NULL,
    detected_by VARCHAR(32) NULL,      -- trigger that found it
    decided_at DATETIME NULL,          -- when dismissed / snoozed
    snooze_until DATETIME NULL,
    note VARCHAR(255) NULL,
    recovered_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_gaps_lane_state (lane, state),
    INDEX idx_gaps_span (lane, gap_start, gap_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def bootstrap(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def _iso(row: dict) -> dict:
    for k in ("gap_start", "gap_end", "detected_at", "decided_at",
              "snooze_until", "recovered_at", "created_at"):
        if row.get(k) is not None and hasattr(row[k], "isoformat"):
            row[k] = row[k].isoformat()
    if row.get("hours") is not None:
        row["hours"] = float(row["hours"])
    return row


def record_gap(conn, lane_key: str, start: datetime, end: datetime,
               detected_by: str = "scheduled") -> dict | None:
    """File a missing span, merging into an overlapping unresolved row.

    Returns the stored row, or None when the span is empty. Never raises — a
    bookkeeping failure must not fail the sync that found the gap.
    """
    if end <= start:
        return None
    try:
        bootstrap(conn)
        with conn.cursor() as cur:
            # An adjacent-or-overlapping row for this lane is the SAME outage.
            cur.execute(
                """SELECT * FROM tta_data_gaps
                   WHERE lane=%s AND state IN (%s,%s,%s)
                     AND gap_start <= %s AND gap_end >= %s
                   ORDER BY gap_start LIMIT 1""",
                (lane_key, OPEN, SNOOZED, DISMISSED,
                 end + _MERGE_TOLERANCE, start - _MERGE_TOLERANCE),
            )
            existing = cur.fetchone()

            if existing:
                new_start = min(existing["gap_start"], start)
                new_end = max(existing["gap_end"], end)
                if new_start == existing["gap_start"] and new_end == existing["gap_end"]:
                    return _iso(dict(existing))     # nothing new to say
                hours = round((new_end - new_start).total_seconds() / 3600, 1)
                # Widening re-opens a dismissed gap: the operator declined the
                # span they were shown, not one that has since grown.
                state = existing["state"] if existing["state"] != DISMISSED else OPEN
                cur.execute(
                    """UPDATE tta_data_gaps
                       SET gap_start=%s, gap_end=%s, hours=%s, state=%s
                       WHERE id=%s""",
                    (new_start, new_end, hours, state, existing["id"]),
                )
                conn.commit()
                logger.warning("TMS[%s] data gap widened to %s .. %s (%sh)",
                               lane_key, new_start, new_end, hours)
                cur.execute("SELECT * FROM tta_data_gaps WHERE id=%s", (existing["id"],))
                return _iso(dict(cur.fetchone()))

            hours = round((end - start).total_seconds() / 3600, 1)
            cur.execute(
                """INSERT INTO tta_data_gaps
                   (lane, gap_start, gap_end, hours, state, detected_at, detected_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (lane_key, start, end, hours, OPEN, datetime.now(), detected_by),
            )
            gap_id = cur.lastrowid
        conn.commit()
        logger.error("TMS[%s] DATA GAP recorded #%s: %s .. %s (%sh) — open until "
                     "backfilled or dismissed", lane_key, gap_id, start, end, hours)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tta_data_gaps WHERE id=%s", (gap_id,))
            return _iso(dict(cur.fetchone()))
    except Exception as e:  # noqa: BLE001 — bookkeeping is best-effort
        logger.exception("Could not record data gap for %s: %s", lane_key, e)
        return None


def list_gaps(conn, lane_key: str | None = None, states: tuple | None = None,
              limit: int = 100) -> list[dict]:
    """Gap rows, newest missing span first."""
    try:
        bootstrap(conn)
        where, params = [], []
        if lane_key:
            where.append("lane = %s")
            params.append(lane_key)
        if states:
            placeholders = ",".join(["%s"] * len(states))
            where.append("state IN (" + placeholders + ")")
            params.extend(states)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(int(limit))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM tta_data_gaps " + clause +
                " ORDER BY gap_start DESC LIMIT %s", params)
            return [_iso(dict(r)) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read data gaps: %s", e)
        return []


def actionable_gaps(conn, limit: int = 50) -> list[dict]:
    """What the banner shows: open gaps, plus snoozed ones whose time is up.

    An expired snooze is indistinguishable from open here on purpose — "remind
    me later" has to actually remind.
    """
    try:
        bootstrap(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM tta_data_gaps
                   WHERE state=%s
                      OR (state=%s AND (snooze_until IS NULL OR snooze_until <= %s))
                   ORDER BY gap_start DESC LIMIT %s""",
                (OPEN, SNOOZED, datetime.now(), int(limit)),
            )
            return [_iso(dict(r)) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read actionable data gaps: %s", e)
        return []


def lane_summary(conn, lane_key: str) -> dict:
    """Per-lane counts for the lane card.

    `missing_hours` deliberately includes DISMISSED gaps: declining to backfill
    does not put the data back, and a lane with a dismissed hole must not render
    as plain healthy.
    """
    empty = {"open": 0, "snoozed": 0, "dismissed": 0, "recovered": 0,
             "unresolved": 0, "missing_hours": 0.0, "earliest_missing": None}
    try:
        bootstrap(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT state, COUNT(*) AS n, COALESCE(SUM(hours),0) AS h,
                          MIN(gap_start) AS earliest
                   FROM tta_data_gaps WHERE lane=%s GROUP BY state""",
                (lane_key,),
            )
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not summarise data gaps for %s: %s", lane_key, e)
        return empty

    out = dict(empty)
    earliest = None
    for r in rows:
        state = r["state"]
        if state in out:
            out[state] = int(r["n"])
        if state in UNRESOLVED:
            out["unresolved"] += int(r["n"])
            out["missing_hours"] += float(r["h"] or 0)
            if r["earliest"] and (earliest is None or r["earliest"] < earliest):
                earliest = r["earliest"]
    out["missing_hours"] = round(out["missing_hours"], 1)
    out["earliest_missing"] = earliest.isoformat() if earliest else None
    return out


def set_state(conn, gap_id: int, state: str, snooze_hours: int | None = None,
              note: str | None = None) -> dict | None:
    """Apply an operator decision to one gap. Returns None when the id is unknown."""
    if state not in (OPEN, SNOOZED, DISMISSED, RECOVERED):
        raise ValueError("Unknown gap state " + repr(state))
    bootstrap(conn)
    now = datetime.now()
    snooze_until = (now + timedelta(hours=int(snooze_hours or 24))
                    if state == SNOOZED else None)
    recovered_at = now if state == RECOVERED else None
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE tta_data_gaps
               SET state=%s, snooze_until=%s, note=%s, decided_at=%s, recovered_at=%s
               WHERE id=%s""",
            (state, snooze_until, note, now, recovered_at, int(gap_id)),
        )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM tta_data_gaps WHERE id=%s", (int(gap_id),))
        row = cur.fetchone()
    if not row:
        return None
    logger.info("Data gap #%s -> %s%s", gap_id, state,
                (" until " + str(snooze_until)) if snooze_until else "")
    return _iso(dict(row))


def mark_recovered(conn, lane_key: str, start: datetime, end: datetime) -> int:
    """Close every unresolved gap FULLY covered by a completed backfill.

    Fully covered, not merely overlapping: a backfill that filled half a gap
    leaves the other half missing, and closing the row would hide it. Those
    rows stay open and are re-stated by the next detection instead.
    """
    try:
        bootstrap(conn)
        placeholders = ",".join(["%s"] * len(UNRESOLVED))
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE tta_data_gaps
                   SET state=%s, recovered_at=%s
                   WHERE lane=%s AND state IN (""" + placeholders + """)
                     AND gap_start >= %s AND gap_end <= %s""",
                (RECOVERED, datetime.now(), lane_key, *UNRESOLVED, start, end),
            )
            n = cur.rowcount
        conn.commit()
        if n:
            logger.info("Backfill %s .. %s closed %d data gap(s) on %s",
                        start, end, n, lane_key)
        return n
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not mark gaps recovered for %s: %s", lane_key, e)
        return 0
