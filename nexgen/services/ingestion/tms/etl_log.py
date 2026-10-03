"""
ETL event log — same-day, file-backed, UI-readable.

Records every external ETL call (auth / trip-report / GPS) plus per-run
summaries as JSON lines in logs/etl_events_<YYYY-MM-DD>.jsonl. Each event holds
the call payload (secrets masked), timestamp, elapsed time and response status —
and the exception detail when a call fails.

"Keep only same day": the file name carries the date and older files are purged
on the first write of a new day, so only today's log is ever retained. Read it
from the UI via GET /api/v1/tta/sync/logs.
"""

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from nexgen.core.config import ROOT as PROJECT_ROOT
LOG_DIR = PROJECT_ROOT / "logs"

_lock = threading.Lock()
_last_purge_day: str | None = None

_MAX_PAYLOAD_CHARS = 4000            # truncate very large payloads
_MAX_LINES_KEPT = 20000             # hard cap on a single day's file
# Keys whose values must never be written to disk.
_SENSITIVE = {"authkey", "auth_key", "password", "auth", "token",
              "refreshtoken", "refresh_token", "authorization", "bearer"}


def _today() -> str:
    return date.today().isoformat()


def _current_file() -> Path:
    return LOG_DIR / f"etl_events_{_today()}.jsonl"


def _mask(obj):
    """Recursively redact sensitive fields so secrets never hit the log file."""
    if isinstance(obj, dict):
        return {k: ("***" if str(k).lower() in _SENSITIVE else _mask(v))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mask(v) for v in obj]
    return obj


def _truncate(obj):
    s = json.dumps(obj, default=str, ensure_ascii=False)
    if len(s) > _MAX_PAYLOAD_CHARS:
        return s[:_MAX_PAYLOAD_CHARS] + f"...(+{len(s) - _MAX_PAYLOAD_CHARS} chars)"
    return obj


def _purge_old_locked() -> None:
    """Delete any ETL log files that aren't today's. Cheap; runs once per day."""
    global _last_purge_day
    today = _today()
    if _last_purge_day == today:
        return
    keep = _current_file().name
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        for f in LOG_DIR.glob("etl_events_*.jsonl"):
            if f.name != keep:
                try:
                    f.unlink()
                except OSError:
                    pass
    except OSError as e:
        logger.warning("ETL log purge failed: %s", e)
    _last_purge_day = today


def log_event(kind: str, *, method: str | None = None, url: str | None = None,
              payload=None, params=None, status=None, elapsed_ms=None,
              error: str | None = None, **extra) -> None:
    """Append one ETL event. Never raises — logging must not break the ETL."""
    try:
        event = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,                       # auth | trips | gps | run
            "method": method,
            "url": url,
            "payload": _truncate(_mask(payload)) if payload is not None else None,
            "params": _mask(params) if params else None,
            "status": status,                   # HTTP code, or ok/partial/error
            "elapsed_ms": elapsed_ms,
            "error": error,
        }
        if extra:
            event.update(extra)
        line = json.dumps(event, default=str, ensure_ascii=False)
        with _lock:
            _purge_old_locked()
            with open(_current_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # noqa: BLE001 — logging is best-effort
        logger.warning("Could not write ETL log event: %s", e)


def read_events(limit: int = 200, kind: str | None = None,
                status_filter: str | None = None) -> dict:
    """Return today's events, newest first (capped at `limit`)."""
    path = _current_file()
    events: list[dict] = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-_MAX_LINES_KEPT:]
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        except OSError as e:
            logger.warning("Could not read ETL log: %s", e)

    if kind:
        events = [e for e in events if e.get("kind") == kind]
    if status_filter == "error":
        events = [e for e in events if e.get("error") or str(e.get("status")) not in ("200", "ok")]

    events.reverse()  # newest first
    total = len(events)
    return {
        "date": _today(),
        "file": str(path),
        "total": total,
        "events": events[:max(1, min(limit, _MAX_LINES_KEPT))],
    }
