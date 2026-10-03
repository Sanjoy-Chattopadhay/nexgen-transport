"""The live service: pull new fixes, run the detector, publish the result.

Two modes, one code path
------------------------
**tail** — read rows added to `geo_gps_ping` since the last cursor position
and process them. This is the production shape: a fleet feed appends, this
consumes.

**replay** — walk the historical feed as though it were arriving now,
`speed` times faster than it happened. The corpus is two months of recorded
trips, so without this there is nothing live to look at, and a control centre
that cannot be demonstrated cannot be evaluated. Replay is also how the live
detector gets tested against the batch one: same fixes, same order, and the
crossings must match.

Both feed the same `LiveDetector`. The only difference is which rows the
cursor advances over and how long the loop sleeps.

Two modes need two cursors, and conflating them is a real bug
-----------------------------------------------------------
**tail** advances on `id`. Fixes arrive late -- a device that reconnects
flushes an hour of backlog -- and a timestamp cursor would skip every one of
them silently. The id is monotonic on insert, so it catches them.

**replay** advances on `(dt_message, id)`. It has to: insertion order in this
feed is not chronological. The row with the earliest timestamp
(2026-07-10 07:50) carries id 2,114,152, while id 1 is three days later, and
113 of the first 200,000 rows by id go backwards in time. An id cursor paired
with a timestamp horizon therefore skips every row whose id it has passed but
whose timestamp had not yet come due -- silently, and permanently, because the
cursor never goes back. The id is kept as a tie-break so fixes sharing a
second still have a total order.

Durability
----------
Positions and events are written every batch, and the cursor is written
*after* them in the same transaction. A crash therefore reprocesses the last
batch rather than losing it; the crossing detector is deterministic over the
same input, and position rows are upserts, so reprocessing is harmless.

In-memory tracker state is *not* persisted. After a restart each vehicle
re-establishes its state from the first unambiguous fix, which costs one
confirmation window of history and cannot invent a crossing that did not
happen. Persisting it would be a correctness liability for very little gain.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from datetime import datetime, timedelta

from nexgen.shared.geoengine.config import DetectorConfig, settings
from nexgen.shared.geoengine.db import geo_session
from nexgen.shared.geoengine.engine.live import LiveDetector
from nexgen.shared.geoengine.store import build_index, scale_of

logger = logging.getLogger(__name__)

# Small enough that a batch completes in a few seconds even at high replay
# speed. A large batch is more efficient per row but makes the UI lurch:
# nothing is written for half a minute and then everything moves at once.
BATCH_ROWS = 4_000


class LiveService:
    """Owns the detector, the cursor and the write loop."""

    def __init__(self, mode: str = "replay", speed: float = 120.0,
                 detector: DetectorConfig | None = None,
                 poll_seconds: float = 2.0,
                 reset: bool = False,
                 start_at: datetime | None = None,
                 window_hours: float | None = None):
        self.mode = mode
        # Replay can start from the tail of the feed rather than its start.
        # The first days of this corpus carry a handful of vehicles; the last
        # carry 400-600, which is what a control centre is actually for.
        self.window_hours = window_hours
        self.speed = max(1.0, speed)
        self.cfg = detector or settings.detector
        self.poll_seconds = poll_seconds
        self.reset = reset
        self.start_at = start_at
        self._stop = threading.Event()
        self.detector: LiveDetector | None = None
        self.started = datetime.now()
        self.batches = 0

    # -- lifecycle -----------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from nexgen.shared.geoengine.winperf import opt_out_of_power_throttling
        opt_out_of_power_throttling()
        index = build_index(active_only=True, pad_m=_watch_pad())
        self.detector = LiveDetector(index, self.cfg)
        logger.info("live detector ready: %s fences, mode=%s speed=%sx",
                    len(index), self.mode, self.speed)

        self.origin = self._replay_origin()
        cursor = self._load_cursor()
        if cursor[0] is None:
            cursor = (self.origin, 0)
        logger.info("resuming from %s (id %s)", cursor[0], cursor[1])

        # Replay pacing: wall-clock elapsed, multiplied, mapped onto feed time.
        feed_clock = cursor[0]
        wall_start = time.monotonic()

        while not self._stop.is_set():
            if self.mode == "replay":
                elapsed = (time.monotonic() - wall_start) * self.speed
                horizon = feed_clock + timedelta(seconds=elapsed)
                rows = self._fetch(cursor, horizon)
            else:
                rows = self._fetch(cursor, None)

            if not rows:
                if self.mode == "replay" and self._exhausted(cursor):
                    logger.info("replay reached the end of the feed; looping")
                    cursor = (self.origin, 0)
                    self._save_cursor(cursor, 0)
                    feed_clock = self.origin
                    wall_start = time.monotonic()
                    if self.detector:
                        self.detector.vehicles.clear()
                    continue
                self._stop.wait(self.poll_seconds)
                continue

            cursor = self._process(rows)
            self.batches += 1

    # -- the work ------------------------------------------------------------

    def _process(self, rows) -> int:
        positions: dict[str, tuple] = {}
        events: list[tuple] = []
        last_id = 0
        last_ts = None

        for r in rows:
            # Rows arrive in cursor order, so the last one is the new cursor.
            last_id = r["id"]
            last_ts = r["dt_message"]
            asset = r["s_asset_id"]
            if not asset:
                continue
            evs, pos = self.detector.feed(
                asset_id=asset,
                ts=r["dt_message"],
                lat=float(r["d_lat"]),
                lon=float(r["d_long"]),
                speed=r["i_speed"],
                trip_no=r["i_trip_no"],
            )
            if pos is None:
                continue
            positions[asset] = (
                asset, pos["ts"], pos["lat"], pos["lon"], pos["speed"],
                pos["trip_no"], pos["fence_id"], pos["site_id"],
                pos["site_name"], pos["category"], pos["inside_count"],
                pos["scale"],
            )
            for e in evs or ():
                events.append((
                    e.ts, e.asset_id, e.trip_no, e.fence.fence_id,
                    e.fence.site_id, e.fence.name, e.fence.category,
                    scale_of(e.fence), e.event, e.severity, e.gap_seconds,
                    e.lat, e.lon, e.speed, e.limit, e.detail[:255],
                    e.confirmed_by,
                ))

        with geo_session() as conn:
            with conn.cursor() as cur:
                if positions:
                    cur.executemany("""
                        INSERT INTO geo_live_position
                            (s_asset_id, dt_message, d_lat, d_long, i_speed, i_trip_no,
                             i_fence_id, i_site_id, s_site_name, s_category,
                             i_inside_count, s_scale)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON DUPLICATE KEY UPDATE
                            dt_message=VALUES(dt_message), d_lat=VALUES(d_lat),
                            d_long=VALUES(d_long), i_speed=VALUES(i_speed),
                            i_trip_no=VALUES(i_trip_no), i_fence_id=VALUES(i_fence_id),
                            i_site_id=VALUES(i_site_id), s_site_name=VALUES(s_site_name),
                            s_category=VALUES(s_category),
                            i_inside_count=VALUES(i_inside_count),
                            s_scale=VALUES(s_scale)
                    """, list(positions.values()))
                if events:
                    cur.executemany("""
                        INSERT INTO geo_live_event
                            (dt_event, s_asset_id, i_trip_no, i_fence_id, i_site_id,
                             s_site_name, s_category, s_scale, s_event, s_severity,
                             i_gap_seconds, d_lat, d_long, i_speed, i_limit, s_detail,
                             s_confirmed_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, events)
                # Cursor last, inside the same transaction: a crash replays
                # this batch rather than losing it, and replaying is safe.
                cur.execute("""
                    INSERT INTO geo_live_cursor (i_id, i_last_ping_id, dt_last_message, i_processed)
                    VALUES (1, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        i_last_ping_id=VALUES(i_last_ping_id),
                        dt_last_message=VALUES(dt_last_message),
                        i_processed=geo_live_cursor.i_processed+VALUES(i_processed)
                """, (last_id, last_ts, len(rows)))
            conn.commit()

        if events:
            logger.info("batch %s: %s fixes, %s positions, %s events, feed at %s",
                        self.batches, len(rows), len(positions), len(events), last_ts)
        return (last_ts, last_id)

    # -- feed access ---------------------------------------------------------

    def _fetch(self, cursor, horizon: datetime | None):
        last_ts, last_id = cursor
        cols = ("SELECT id, i_trip_no, s_asset_id, dt_message, d_lat, d_long, i_speed "
                "FROM geo_gps_ping ")
        if self.mode == "replay":
            # Ordered by time so the cursor and the horizon agree about what
            # "next" means. The id only breaks ties within a second.
            sql = cols + "WHERE (dt_message > %s OR (dt_message = %s AND id > %s))"
            params: list = [last_ts, last_ts, last_id]
            if horizon is not None:
                sql += " AND dt_message <= %s"
                params.append(horizon)
            sql += " ORDER BY dt_message, id LIMIT %s"
        else:
            sql = cols + "WHERE id > %s ORDER BY id LIMIT %s"
            params = [last_id]
        params.append(BATCH_ROWS)
        with geo_session() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _exhausted(self, cursor) -> bool:
        last_ts, last_id = cursor
        with geo_session() as conn, conn.cursor() as cur:
            if self.mode == "replay":
                cur.execute(
                    "SELECT COUNT(*) n FROM geo_gps_ping "
                    "WHERE dt_message > %s OR (dt_message = %s AND id > %s)",
                    (last_ts, last_ts, last_id))
            else:
                cur.execute("SELECT COUNT(*) n FROM geo_gps_ping WHERE id > %s",
                            (last_id,))
            return cur.fetchone()["n"] == 0

    def _replay_origin(self) -> datetime:
        """Where a fresh replay starts in feed time."""
        if self.start_at:
            return self.start_at
        with geo_session() as conn, conn.cursor() as cur:
            cur.execute("SELECT MIN(dt_message) a, MAX(dt_message) b FROM geo_gps_ping")
            row = cur.fetchone()
        if not row or not row["b"]:
            return datetime.now()
        if self.window_hours:
            return max(row["a"], row["b"] - timedelta(hours=self.window_hours))
        return row["a"]

    def _load_cursor(self):
        with geo_session() as conn, conn.cursor() as cur:
            if self.reset:
                cur.execute("DELETE FROM geo_live_cursor")
                cur.execute("DELETE FROM geo_live_event")
                cur.execute("DELETE FROM geo_live_position")
                conn.commit()
                return (None, 0)
            cur.execute(
                "SELECT i_last_ping_id, dt_last_message FROM geo_live_cursor WHERE i_id=1")
            row = cur.fetchone()
        if not row:
            return (None, 0)
        return (row["dt_last_message"], row["i_last_ping_id"] or 0)

    def _save_cursor(self, cursor, processed: int) -> None:
        ts, ping_id = cursor
        with geo_session() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO geo_live_cursor (i_id, i_last_ping_id, dt_last_message, i_processed)
                VALUES (1,%s,%s,%s)
                ON DUPLICATE KEY UPDATE i_last_ping_id=VALUES(i_last_ping_id),
                                        dt_last_message=VALUES(dt_last_message)
            """, (ping_id, ts, processed))
            conn.commit()


def _watch_pad() -> float:
    from nexgen.shared.geoengine.engine.live import WATCH_PAD_M
    from nexgen.shared.geoengine.store import max_tolerance_m
    return WATCH_PAD_M + max_tolerance_m()


def main(mode: str = "replay", speed: float = 120.0, reset: bool = False,
         poll_seconds: float = 2.0, window_hours: float | None = None,
         start_at: datetime | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s live: %(message)s",
    )
    svc = LiveService(mode=mode, speed=speed, reset=reset,
                      poll_seconds=poll_seconds,
                      window_hours=window_hours, start_at=start_at)

    def _sig(_signum, _frame):
        logger.info("stopping ...")
        svc.stop()

    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass

    try:
        svc.run()
    except KeyboardInterrupt:
        pass
    finally:
        if svc.detector:
            logger.info("final: %s", json.dumps(svc.detector.stats()))
    return 0
