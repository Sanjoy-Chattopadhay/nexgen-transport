"""Scheduled jobs with one runner each and a record of every run.

Each worker role registers its jobs here: an interval ("every 30 minutes"), a
cron expression ("0 2 * * *" = 02:00 daily) or a manual-only job. A run
  * takes a MySQL named lock, so the same job never runs twice at once across
    processes or machines (a second attempt is recorded as `skipped`);
  * writes a row to nx_events.job_run when it starts and updates it when it
    ends, with how long it took, what it touched (the function's return value)
    and any error.
The developer page lists every job with its next run, last result and a
"run now" button.

APScheduler does the timing; it is what Smart-Truck already used for its ETL
lanes. `coalesce` and `max_instances=1` stop a slow run from queueing copies of
itself behind it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from nexgen.core.config import get_config
from nexgen.core.db import connect, named_lock

logger = logging.getLogger(__name__)


def _runs_table() -> str:
    return f"`{get_config().schema('events')}`.job_run"


@dataclass
class Job:
    name: str
    func: Callable[[], Any]
    service: str
    every_minutes: float | None = None
    every_seconds: float | None = None
    cron: str | None = None
    description: str = ""
    enabled: bool = True
    first_delay_s: float | None = None
    # state
    last_status: str | None = None
    last_started: datetime | None = None
    last_finished: datetime | None = None
    last_seconds: float | None = None
    last_error: str | None = None
    last_summary: Any = None
    running: bool = False
    runs: int = field(default=0)

    @property
    def schedule_text(self) -> str:
        if self.cron:
            return f"cron {self.cron}"
        if self.every_minutes:
            return f"every {self.every_minutes:g} min"
        if self.every_seconds:
            return f"every {self.every_seconds:g} s"
        return "manual"


class JobRunner:
    """Holds one role's jobs and the scheduler that fires them."""

    def __init__(self, service: str):
        self.service = service
        self.jobs: dict[str, Job] = {}
        self._sched: BackgroundScheduler | None = None
        self._lock = threading.Lock()

    def add(self, name: str, func: Callable[[], Any], *, every_minutes: float | None = None,
            every_seconds: float | None = None, cron: str | None = None, description: str = "",
            enabled: bool = True, first_delay_s: float | None = None) -> Job:
        job = Job(name=name, func=func, service=self.service, every_minutes=every_minutes,
                  every_seconds=every_seconds, cron=cron, description=description,
                  enabled=enabled, first_delay_s=first_delay_s)
        self.jobs[name] = job
        if self._sched is not None:
            self._schedule(job)
        return job

    # -- execution -------------------------------------------------------
    def run(self, name: str, trigger: str = "schedule") -> dict:
        """Run a job now on the calling thread, under its lock, recorded."""
        job = self.jobs[name]
        started = datetime.now()
        t0 = time.perf_counter()
        run_id = None
        try:
            conn = connect("events")
        except Exception as exc:  # database down: run is impossible, say so
            job.last_status, job.last_error = "failed", f"database unavailable: {exc}"
            return {"status": "failed", "error": job.last_error}
        try:
            with named_lock(conn, f"job:{self.service}:{name}") as got:
                if not got:
                    self._record(conn, None, name, trigger, started, "skipped",
                                 {"reason": "already running elsewhere"}, None, 0.0)
                    return {"status": "skipped", "reason": "already running elsewhere"}
                run_id = self._record(conn, None, name, trigger, started, "running", None, None, None)
                job.running, job.last_started = True, started
                try:
                    summary = job.func()
                    status, error = "ok", None
                except Exception as exc:
                    summary, status = None, "failed"
                    error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}"
                    logger.exception("job %s.%s failed", self.service, name)
                secs = time.perf_counter() - t0
                job.running = False
                job.runs += 1
                job.last_status, job.last_error = status, error
                job.last_finished, job.last_seconds = datetime.now(), secs
                job.last_summary = summary
                self._record(conn, run_id, name, trigger, started, status, summary, error, secs)
                return {"status": status, "seconds": round(secs, 2), "summary": summary,
                        "error": error, "run_id": run_id}
        finally:
            job.running = False
            conn.close()

    def _record(self, conn, run_id, name, trigger, started, status, summary, error, secs) -> int | None:
        try:
            body = None if summary is None else json.dumps(summary, default=str)[:60000]
            with conn.cursor() as cur:
                if run_id is None:
                    cur.execute(
                        f"INSERT INTO {_runs_table()} (s_service, s_job, s_trigger, dt_started, s_status, "
                        "j_summary, s_error, d_seconds) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (self.service, name, trigger, started, status, body, error, secs))
                    rid = cur.lastrowid
                else:
                    cur.execute(
                        f"UPDATE {_runs_table()} SET dt_finished=NOW(3), s_status=%s, j_summary=%s, "
                        "s_error=%s, d_seconds=%s WHERE i_run_id=%s",
                        (status, body, error, secs, run_id))
                    rid = run_id
            conn.commit()
            return rid
        except Exception:
            logger.exception("could not record job run %s.%s", self.service, name)
            return run_id

    # -- scheduling ------------------------------------------------------
    def _schedule(self, job: Job) -> None:
        assert self._sched is not None
        if not job.enabled:
            return
        if job.cron:
            trigger = CronTrigger.from_crontab(job.cron, timezone=get_config().product.get("timezone"))
        elif job.every_minutes or job.every_seconds:
            seconds = (job.every_minutes or 0) * 60 + (job.every_seconds or 0)
            kw = {}
            if job.first_delay_s is not None:
                kw["start_date"] = datetime.now() + timedelta(seconds=job.first_delay_s)
            trigger = IntervalTrigger(seconds=seconds, **kw)
        else:
            return  # manual-only
        self._sched.add_job(self.run, trigger, args=[job.name, "schedule"], id=job.name,
                            replace_existing=True, coalesce=True, max_instances=1,
                            misfire_grace_time=300)

    def start(self) -> None:
        with self._lock:
            if self._sched is not None:
                return
            self._sched = BackgroundScheduler(timezone=get_config().product.get("timezone"))
            for job in self.jobs.values():
                self._schedule(job)
            self._sched.start()

    def stop(self) -> None:
        with self._lock:
            if self._sched is None:
                return
            self._sched.shutdown(wait=False)
            self._sched = None

    @property
    def running(self) -> bool:
        return self._sched is not None

    def set_enabled(self, name: str, enabled: bool) -> None:
        job = self.jobs[name]
        job.enabled = enabled
        if self._sched is not None:
            if enabled:
                self._schedule(job)
            elif self._sched.get_job(name):
                self._sched.remove_job(name)

    def run_in_background(self, name: str, trigger: str = "manual") -> None:
        threading.Thread(target=self.run, args=(name, trigger), name=f"job:{name}", daemon=True).start()

    def snapshot(self) -> list[dict]:
        out = []
        for job in self.jobs.values():
            nxt = None
            if self._sched is not None and self._sched.get_job(job.name):
                t = self._sched.get_job(job.name).next_run_time
                nxt = t.isoformat() if t else None
            out.append({
                "name": job.name, "description": job.description, "schedule": job.schedule_text,
                "enabled": job.enabled, "running": job.running, "next_run": nxt,
                "last_status": job.last_status, "last_seconds": job.last_seconds and round(job.last_seconds, 2),
                "last_started": job.last_started.isoformat() if job.last_started else None,
                "last_error": job.last_error, "runs": job.runs,
            })
        return out


def recorded(service: str, job: str, trigger: str, fn: Callable[[], Any]) -> dict:
    """Run `fn` under the job lock and record it in job_run, for code that
    keeps its own scheduler (the TMS lanes, whose intervals are edited live
    from the Ingestion page) but should still show on the developer page."""
    runner = JobRunner(service)
    runner.add(job, fn)
    return runner.run(job, trigger)


def recent_runs(service: str | None = None, job: str | None = None, limit: int = 100) -> list[dict]:
    sql = (f"SELECT i_run_id, s_service, s_job, s_trigger, dt_started, dt_finished, d_seconds, "
           f"s_status, j_summary, s_error FROM {_runs_table()}")
    where, params = [], []
    if service:
        where.append("s_service=%s")
        params.append(service)
    if job:
        where.append("s_job=%s")
        params.append(job)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY i_run_id DESC LIMIT %s"
    params.append(limit)
    with connect("events") as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())
