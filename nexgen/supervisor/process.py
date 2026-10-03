"""Start, watch, stop and restart the service processes.

`python -m nexgen run` starts this supervisor. It launches every service as
its own child process (`python -m nexgen serve <name>`), keeps each one in the
state you asked for, and restarts a service that crashes -- at most
`run.restart_limit` times in `run.restart_window_s`, after which it marks the
service `crashed` and leaves it for a person to look at, rather than burning
CPU on a crash loop.

What you asked for is stored in nx_events.service_state, so stopping a module
from the developer page survives a restart of the whole system.

Stopping is graceful first: the service is asked to shut down over its
internal API (its workers finish the batch they are on), and only killed --
with its whole process tree, including any worker pool -- if it has not exited
within `run.stop_timeout_s`.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

import httpx
import psutil

from nexgen.core.config import ROOT, get_config
from nexgen.core.logs import logs_dir

logger = logging.getLogger(__name__)


class ManagedService:
    def __init__(self, name: str):
        self.name = name
        self.spec = get_config().service(name)
        self.proc: subprocess.Popen | None = None
        self.desired = "running"
        self.state = "stopped"          # stopped | starting | running | stopping | crashed
        self.started_at: datetime | None = None
        self.last_exit_code: int | None = None
        self.last_exit_at: datetime | None = None
        self.restarts: deque[float] = deque()
        self.health: dict | None = None
        self.health_at: datetime | None = None
        self.health_error: str | None = None
        self._lock = threading.RLock()
        self._console = None

    @property
    def port(self) -> int:
        return self.spec.port

    @property
    def url(self) -> str:
        return f"http://{get_config().host}:{self.port}"

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc and self.proc.poll() is None else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- start/stop ------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.alive():
                return
            self._port_check()
            cmd = [sys.executable, "-m", "nexgen", "serve", self.name]
            env = {**os.environ, "NEXGEN_SERVICE": self.name, "PYTHONUNBUFFERED": "1",
                   "NEXGEN_SUPERVISOR_PID": str(os.getpid())}
            console_path = logs_dir() / f"{self.name}.console.log"
            self._console = open(console_path, "w", encoding="utf-8", errors="replace")
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            self.proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=self._console,
                                         stderr=subprocess.STDOUT, creationflags=flags)
            self.state = "starting"
            self.started_at = datetime.now()
            self.health = None
            logger.info("started %s (pid %s) on port %s", self.name, self.proc.pid, self.port)

    def _port_check(self) -> None:
        """Refuse to start onto a port someone else is holding: the gateway
        would route this service's traffic to a stranger."""
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == self.port and c.status == psutil.CONN_LISTEN:
                owner: list[str] = []
                try:
                    owner = psutil.Process(c.pid).cmdline() if c.pid else []
                except Exception:
                    pass
                if "nexgen" in owner and "serve" in owner and self.name in owner:
                    # An orphan of an earlier supervisor that was killed hard:
                    # ours to replace.
                    logger.warning("replacing orphaned %s (pid %s)", self.name, c.pid)
                    try:
                        psutil.Process(c.pid).kill()
                        psutil.Process(c.pid).wait(10)
                    except psutil.Error:
                        pass
                    return
                raise RuntimeError(f"port {self.port} for {self.name} is already in use by "
                                   f"pid {c.pid} ({' '.join(owner)[:120]})")

    def stop(self, timeout: float | None = None) -> None:
        with self._lock:
            if not self.alive():
                self.state = "stopped"
                return
            self.state = "stopping"
            timeout = float(timeout or get_config().run.get("stop_timeout_s", 15))
            try:
                httpx.post(f"{self.url}/internal/v1/shutdown", timeout=3.0)
            except Exception:
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                logger.warning("%s did not stop in %.0fs; killing its process tree", self.name, timeout)
                self.kill_tree()
            self.last_exit_code = self.proc.returncode
            self.last_exit_at = datetime.now()
            self.state = "stopped"
            self.health = None
            if self._console:
                self._console.close()
                self._console = None

    def kill_tree(self) -> None:
        if not self.proc:
            return
        try:
            parent = psutil.Process(self.proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            parent.kill()
        except psutil.Error:
            pass
        try:
            self.proc.wait(10)
        except Exception:
            pass

    # -- watching --------------------------------------------------------
    def check(self) -> None:
        """Called by the monitor: notice exits, restart within limits."""
        with self._lock:
            if self.proc is None:
                return
            code = self.proc.poll()
            if code is None:
                return
            if self.state in ("stopping", "stopped"):
                return
            self.last_exit_code, self.last_exit_at = code, datetime.now()
            self.health = None
            if self.desired != "running":
                self.state = "stopped"
                return
            cfg = get_config().run
            window, limit = float(cfg.get("restart_window_s", 300)), int(cfg.get("restart_limit", 5))
            now = time.monotonic()
            while self.restarts and now - self.restarts[0] > window:
                self.restarts.popleft()
            if len(self.restarts) >= limit:
                self.state = "crashed"
                logger.error("%s exited with %s and has restarted %d times in %.0fs; giving up",
                             self.name, code, len(self.restarts), window)
                return
            self.restarts.append(now)
            logger.warning("%s exited with %s; restarting", self.name, code)
            self.state = "stopped"
            try:
                self.start()
            except Exception as exc:
                self.state = "crashed"
                self.health_error = str(exc)

    def poll_health(self, client: httpx.Client) -> None:
        if not self.alive():
            return
        try:
            r = client.get(f"{self.url}/health", timeout=3.0)
            r.raise_for_status()
            self.health, self.health_error = r.json(), None
            self.health_at = datetime.now()
            if self.state == "starting":
                self.state = "running"
        except Exception as exc:
            self.health_error = f"{type(exc).__name__}: {exc}"
            if self.state == "running" and self.health_at and \
                    (datetime.now() - self.health_at).total_seconds() > 30:
                self.state = "starting"   # alive but not answering: shown as not ready

    def resources(self) -> dict:
        if not self.alive():
            return {}
        try:
            p = psutil.Process(self.proc.pid)
            procs = [p] + p.children(recursive=True)
            mem = sum(x.memory_info().rss for x in procs if x.is_running())
            cpu = sum(x.cpu_percent(None) for x in procs if x.is_running())
            return {"memory_mb": round(mem / 1048576, 1), "cpu_percent": round(cpu, 1),
                    "processes": len(procs)}
        except psutil.Error:
            return {}

    def snapshot(self) -> dict:
        h = self.health or {}
        return {
            "name": self.name, "title": self.spec.title, "description": self.spec.description,
            "port": self.port, "desired": self.desired, "state": self.state, "pid": self.pid,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "uptime_s": int((datetime.now() - self.started_at).total_seconds())
            if self.started_at and self.alive() else None,
            "restarts_recent": len(self.restarts),
            "last_exit_code": self.last_exit_code,
            "last_exit_at": self.last_exit_at.isoformat() if self.last_exit_at else None,
            "health_status": h.get("status"), "health_error": self.health_error,
            "health_at": self.health_at.isoformat() if self.health_at else None,
            "roles": h.get("roles", {}), "routes": list(self.spec.routes),
            "declared_roles": [{"name": r.name, "autostart": r.autostart, "description": r.description}
                               for r in self.spec.roles],
            **self.resources(),
        }


class Supervisor:
    def __init__(self):
        cfg = get_config()
        self.services: dict[str, ManagedService] = {n: ManagedService(n) for n in cfg.service_names}
        self.started_at = datetime.now()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- desired state, persisted ------------------------------------------
    def _load_desired(self) -> None:
        try:
            from nexgen.core.db import connect
            with connect("events") as conn, conn.cursor() as cur:
                cur.execute("SELECT s_service, s_desired FROM service_state")
                for r in cur.fetchall():
                    if r["s_service"] in self.services:
                        self.services[r["s_service"]].desired = r["s_desired"]
        except Exception as exc:
            logger.warning("could not read saved service states (%s); starting everything", exc)

    def _save_desired(self, name: str, desired: str) -> None:
        try:
            from nexgen.core.db import connect
            with connect("events") as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO service_state (s_service, s_desired) VALUES (%s, %s) "
                            "ON DUPLICATE KEY UPDATE s_desired=VALUES(s_desired), dt_changed=NOW()",
                            (name, desired))
                conn.commit()
        except Exception:
            logger.exception("could not save desired state of %s", name)

    # -- control ---------------------------------------------------------
    def start_all(self) -> None:
        self._load_desired()
        for svc in self.services.values():
            if svc.desired == "running":
                try:
                    svc.start()
                except Exception as exc:
                    svc.state, svc.health_error = "crashed", str(exc)
                    logger.error("could not start %s: %s", svc.name, exc)
        self._threads = [
            threading.Thread(target=self._monitor, name="monitor", daemon=True),
            threading.Thread(target=self._health_loop, name="health", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def start(self, name: str) -> dict:
        svc = self.services[name]
        svc.desired = "running"
        svc.restarts.clear()
        self._save_desired(name, "running")
        svc.start()
        return svc.snapshot()

    def stop(self, name: str) -> dict:
        svc = self.services[name]
        svc.desired = "stopped"
        self._save_desired(name, "stopped")
        svc.stop()
        return svc.snapshot()

    def restart(self, name: str) -> dict:
        svc = self.services[name]
        svc.stop()
        svc.desired = "running"
        svc.restarts.clear()
        self._save_desired(name, "running")
        svc.start()
        return svc.snapshot()

    def shutdown(self) -> None:
        """Stop every child (the supervisor is exiting). Desired states are kept."""
        self._stop.set()
        threads = []
        for svc in self.services.values():
            t = threading.Thread(target=svc.stop, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(30)

    # -- loops -----------------------------------------------------------
    def _monitor(self) -> None:
        while not self._stop.wait(2.0):
            for svc in self.services.values():
                try:
                    svc.check()
                except Exception:
                    logger.exception("monitor check failed for %s", svc.name)

    def _health_loop(self) -> None:
        with httpx.Client() as client:
            while not self._stop.is_set():
                for svc in self.services.values():
                    svc.poll_health(client)
                self._stop.wait(3.0)

    def snapshot(self) -> dict:
        return {"started_at": self.started_at.isoformat(), "pid": os.getpid(),
                "services": [s.snapshot() for s in self.services.values()]}
