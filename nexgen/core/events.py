"""Domain events through a transactional outbox in MySQL.

A service that changes data announces it by inserting an event row into
`nx_events.event_log` **on the same connection, in the same transaction** as
the change. Either both commit or neither does, so there is no window in which
data changed but nobody was told (or the reverse) -- the failure mode of
publishing to a broker after a commit.

Consumers poll the log in id order and remember the last id they applied in
`consumer_offset`. Delivery is at least once: a consumer that crashes after
handling an event but before saving its offset sees it again, so every handler
is idempotent (upserts on natural keys, recomputation of whole groups).

The one subtle part is ordering. AUTO_INCREMENT ids are allocated when a row
is inserted, not when its transaction commits, so a reader can see id 11
committed while id 10 is still in flight. Advancing past 10 would lose it for
good. A consumer therefore only advances over a contiguous run of ids; when it
meets a gap it waits, and only treats the gap as a rolled-back transaction
once it has stayed empty for `gap_timeout_s` (60 s by default -- every
transaction in this system is far shorter).

Events carry ids, time windows and the keys a change touched -- never bulk
rows. The consumer reads what it needs from the owner's published views.

The `mysql` driver needs nothing beyond the database every service already
uses, and works on a Windows laptop. A Redis Streams driver can replace the
transport later behind the same `publish` / `Consumer` interface.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from nexgen.core.config import get_config
from nexgen.core.db import connect, named_lock

logger = logging.getLogger(__name__)


def _events_table() -> str:
    return f"`{get_config().schema('events')}`.event_log"


def _offsets_table() -> str:
    return f"`{get_config().schema('events')}`.consumer_offset"


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat(sep=" ")
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return str(o)


def publish(conn, event_type: str, payload: dict, *, tenant_id: int, source: str,
            trace_id: str | None = None) -> int:
    """Insert one event using the caller's connection; the caller commits.

    Call it inside the transaction that made the change.
    """
    body = json.dumps(payload, default=_json_default, separators=(",", ":"))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {_events_table()} (s_type, i_tenant_id, s_source, j_payload, s_trace_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (event_type, tenant_id, source, body, trace_id or uuid.uuid4().hex[:16]),
        )
        return cur.lastrowid


def publish_now(schema_key: str, event_type: str, payload: dict, *, tenant_id: int,
                source: str) -> int:
    """Publish in its own short transaction (for changes that are already committed,
    such as the end of a long batch)."""
    with connect(schema_key) as conn:
        eid = publish(conn, event_type, payload, tenant_id=tenant_id, source=source)
        conn.commit()
        return eid


@dataclass
class Event:
    id: int
    type: str
    tenant_id: int
    source: str
    payload: dict
    created: datetime
    trace_id: str | None = None


@dataclass
class ConsumerState:
    name: str
    offset: int = 0
    processed: int = 0
    failed: int = 0
    last_error: str | None = None
    last_error_at: datetime | None = None
    waiting_gap: int | None = None
    running: bool = False
    stats: dict = field(default_factory=dict)


class Consumer:
    """Applies events of the given types to a handler, in order, at least once.

    `handler(events)` receives a list of Event (a batch) and must be
    idempotent. If it raises, the offset does not move: the batch is retried
    with backoff and the error is shown on the developer page, where an
    operator can retry at once or skip the event that fails.
    """

    def __init__(self, name: str, types: Iterable[str], handler: Callable[[list[Event]], None],
                 *, schema_key: str, batch: int | None = None, poll_ms: int | None = None):
        cfg = get_config()
        ev = cfg.get("events") or {}
        self.name = name
        self.types = tuple(types)
        self.handler = handler
        self.schema_key = schema_key
        self.batch = int(batch or ev.get("batch", 200))
        self.poll_s = float(poll_ms or ev.get("poll_ms", 1000)) / 1000.0
        self.gap_timeout = float(ev.get("gap_timeout_s", 60))
        self.state = ConsumerState(name)
        self._gap_seen: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = 0.0
        self._skip_requested: int | None = None

    # -- offsets ---------------------------------------------------------
    def _load_offset(self, conn) -> int:
        with conn.cursor() as cur:
            cur.execute(f"INSERT IGNORE INTO {_offsets_table()} (s_consumer) VALUES (%s)", (self.name,))
            cur.execute(f"SELECT i_last_event_id AS o FROM {_offsets_table()} WHERE s_consumer=%s",
                        (self.name,))
            row = cur.fetchone()
        conn.commit()
        return int(row["o"]) if row else 0

    def _save_offset(self, conn, offset: int, processed: int) -> None:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_offsets_table()} SET i_last_event_id=%s, i_processed=i_processed+%s, "
                "s_last_error=NULL, dt_updated=NOW(3) WHERE s_consumer=%s",
                (offset, processed, self.name))
        conn.commit()

    def _save_error(self, conn, err: str) -> None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {_offsets_table()} SET i_failed=i_failed+1, s_last_error=%s, "
                    "dt_last_error=NOW(3) WHERE s_consumer=%s", (err[:4000], self.name))
            conn.commit()
        except Exception:  # pragma: no cover - db down while reporting a db error
            logger.exception("could not record consumer error")

    # -- one pass ----------------------------------------------------------
    def poll_once(self) -> int:
        """Apply what is available. Returns the number of events applied."""
        with connect(self.schema_key) as conn:
            with named_lock(conn, f"consumer:{self.name}") as got:
                if not got:
                    return 0
                offset = self._load_offset(conn)
                if self._skip_requested is not None and self._skip_requested > offset:
                    offset = self._skip_requested
                    self._save_offset(conn, offset, 0)
                    self._skip_requested = None
                self.state.offset = offset
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT i_event_id, s_type, i_tenant_id, s_source, j_payload, s_trace_id, dt_created "
                        f"FROM {_events_table()} WHERE i_event_id > %s ORDER BY i_event_id LIMIT %s",
                        (offset, self.batch))
                    rows = cur.fetchall()
                conn.commit()
                if not rows:
                    self.state.waiting_gap = None
                    return 0
                # Only a contiguous run of ids is safe to pass over.
                usable, expected = [], offset + 1
                now = time.monotonic()
                for r in rows:
                    eid = int(r["i_event_id"])
                    if eid != expected:
                        first = self._gap_seen.setdefault(expected, now)
                        if now - first < self.gap_timeout:
                            self.state.waiting_gap = expected
                            break
                        logger.warning("%s: event ids %s..%s never appeared; treating as rolled back",
                                       self.name, expected, eid - 1)
                        self._gap_seen.pop(expected, None)
                    usable.append(r)
                    expected = eid + 1
                else:
                    self.state.waiting_gap = None
                if not usable:
                    return 0
                last = int(usable[-1]["i_event_id"])
                mine = [
                    Event(id=int(r["i_event_id"]), type=r["s_type"], tenant_id=int(r["i_tenant_id"]),
                          source=r["s_source"], created=r["dt_created"], trace_id=r["s_trace_id"],
                          payload=json.loads(r["j_payload"]) if isinstance(r["j_payload"], (str, bytes))
                          else (r["j_payload"] or {}))
                    for r in usable if r["s_type"] in self.types
                ]
                if mine:
                    try:
                        self.handler(mine)
                    except Exception as exc:
                        self.state.failed += 1
                        self.state.last_error = f"{type(exc).__name__}: {exc}"
                        self.state.last_error_at = datetime.now()
                        self._save_error(conn, self.state.last_error)
                        logger.exception("%s: handler failed on events %s..%s",
                                         self.name, mine[0].id, mine[-1].id)
                        raise
                self._save_offset(conn, last, len(mine))
                self.state.offset = last
                self.state.processed += len(mine)
                self.state.last_error = None
                return len(mine)

    # -- background loop ---------------------------------------------------
    def _loop(self) -> None:
        self.state.running = True
        while not self._stop.is_set():
            try:
                n = self.poll_once()
                self._backoff = 0.0
                if n == 0:
                    self._stop.wait(self.poll_s)
            except Exception:
                self._backoff = min(300.0, max(2.0, self._backoff * 2 or 2.0))
                self._stop.wait(self._backoff)
        self.state.running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"consumer:{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def retry_now(self) -> None:
        self._backoff = 0.0

    def skip(self, event_id: int) -> None:
        """Move past one failing event (operator decision, from the developer page)."""
        self._skip_requested = event_id

    def snapshot(self) -> dict:
        s = self.state
        return {"name": s.name, "types": list(self.types), "offset": s.offset,
                "processed": s.processed, "failed": s.failed, "last_error": s.last_error,
                "last_error_at": s.last_error_at.isoformat() if s.last_error_at else None,
                "waiting_gap": s.waiting_gap, "running": s.running,
                "backoff_s": round(self._backoff, 1)}


def consumer_overview() -> list[dict]:
    """Every consumer's offset and lag against the newest event (developer page)."""
    with connect("events") as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(i_event_id), 0) AS m FROM event_log")
        head = int(cur.fetchone()["m"])
        cur.execute("SELECT s_consumer, i_last_event_id, i_processed, i_failed, s_last_error, "
                    "dt_last_error, dt_updated FROM consumer_offset ORDER BY s_consumer")
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({**r, "head": head, "lag": max(0, head - int(r["i_last_event_id"]))})
    return out


def recent_events(limit: int = 100, event_type: str | None = None) -> list[dict]:
    with connect("events") as conn, conn.cursor() as cur:
        if event_type:
            cur.execute("SELECT i_event_id, s_type, i_tenant_id, s_source, j_payload, dt_created "
                        "FROM event_log WHERE s_type=%s ORDER BY i_event_id DESC LIMIT %s",
                        (event_type, limit))
        else:
            cur.execute("SELECT i_event_id, s_type, i_tenant_id, s_source, j_payload, dt_created "
                        "FROM event_log ORDER BY i_event_id DESC LIMIT %s", (limit,))
        return list(cur.fetchall())


def purge_old(days: int | None = None) -> int:
    """Drop events older than the retention window, in batches."""
    days = int(days or (get_config().get("events") or {}).get("retention_days", 14))
    total = 0
    with connect("events") as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM event_log WHERE dt_created < NOW() - INTERVAL %s DAY "
                            "ORDER BY i_event_id LIMIT 10000", (days,))
                n = cur.rowcount
            conn.commit()
            total += n
            if n < 10000:
                break
    return total
