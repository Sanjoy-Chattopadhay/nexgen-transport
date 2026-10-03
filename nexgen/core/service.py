"""The runtime every service shares: one process, an API, and worker roles.

A service is one OS process, started by the supervisor (or on its own with
`python -m nexgen serve <name>`). Inside it:

  * the **api** role is the FastAPI app on the service's port. Stopping the
    api role does not kill the process: public routes answer
    `503 service_stopped` while /health and /internal/* keep working, so the
    developer page can still see and restart it.
  * **worker** roles are background threads: event consumers, scheduled jobs
    and long-running loops. Each starts and stops on its own.

Every service answers the same control surface, which is what the developer
page drives:

    GET  /health                       liveness and role states
    GET  /ready                        database reachable, migrations applied
    GET  /internal/v1/roles            roles, consumers, jobs
    POST /internal/v1/roles/{r}/start  start one role
    POST /internal/v1/roles/{r}/stop   stop one role
    POST /internal/v1/jobs/{j}/run     run a job now
    POST /internal/v1/consumers/{c}/retry | skip?event_id=
    POST /internal/v1/shutdown         graceful stop of the whole process
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from nexgen.core.config import get_config
from nexgen.core.events import Consumer
from nexgen.core.jobs import JobRunner
from nexgen.core.logs import setup_logging

logger = logging.getLogger(__name__)

_CONTROL_PREFIXES = ("/health", "/ready", "/internal/", "/docs", "/openapi.json")


class Role:
    """A unit that can be started and stopped inside a service process."""

    kind = "worker"

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.started_at: datetime | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:  # pragma: no cover - overridden
        return False

    def start(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def snapshot(self) -> dict:
        return {"name": self.name, "kind": self.kind, "description": self.description,
                "running": self.running, "last_error": self.last_error,
                "started_at": self.started_at.isoformat() if self.started_at else None}


class ApiRole(Role):
    kind = "api"

    def __init__(self, description: str = "public API"):
        super().__init__("api", description)
        self._on = False

    @property
    def running(self) -> bool:
        return self._on

    def start(self) -> None:
        self._on = True
        self.started_at = datetime.now()

    def stop(self) -> None:
        self._on = False


class WorkerRole(Role):
    """Event consumers + scheduled jobs + free-running loops, as one switch."""

    def __init__(self, name: str, service: str, description: str = ""):
        super().__init__(name, description)
        self.service = service
        self.consumers: list[Consumer] = []
        self.jobs = JobRunner(service)
        self.loops: list[tuple[str, Callable[[threading.Event], None]]] = []
        self._loop_threads: list[threading.Thread] = []
        self._loop_stop = threading.Event()
        self._on = False
        self.on_start: list[Callable[[], None]] = []
        self.on_stop: list[Callable[[], None]] = []

    @property
    def running(self) -> bool:
        return self._on

    def consumer(self, name: str, types, handler, **kw) -> Consumer:
        c = Consumer(f"{self.service}.{name}", types, handler, schema_key=kw.pop("schema_key", self.service), **kw)
        self.consumers.append(c)
        return c

    def loop(self, name: str, fn: Callable[[threading.Event], None]) -> None:
        """A function that runs until the event it is given is set."""
        self.loops.append((name, fn))

    def start(self) -> None:
        if self._on:
            return
        for hook in self.on_start:
            hook()
        for c in self.consumers:
            c.start()
        self.jobs.start()
        self._loop_stop.clear()
        self._loop_threads = []
        for name, fn in self.loops:
            t = threading.Thread(target=self._guard, args=(name, fn), name=f"loop:{name}", daemon=True)
            t.start()
            self._loop_threads.append(t)
        self._on = True
        self.started_at = datetime.now()

    def _guard(self, name: str, fn) -> None:
        while not self._loop_stop.is_set():
            try:
                fn(self._loop_stop)
                return
            except Exception as exc:
                self.last_error = f"{name}: {type(exc).__name__}: {exc}"
                logger.exception("loop %s crashed; restarting in 10 s", name)
                self._loop_stop.wait(10)

    def stop(self) -> None:
        if not self._on:
            return
        self._loop_stop.set()
        self.jobs.stop()
        for c in self.consumers:
            c.stop()
        for t in self._loop_threads:
            t.join(30)
        for hook in self.on_stop:
            try:
                hook()
            except Exception:
                logger.exception("stop hook failed in %s", self.name)
        self._on = False

    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["consumers"] = [c.snapshot() for c in self.consumers]
        snap["jobs"] = self.jobs.snapshot()
        snap["loops"] = [n for n, _ in self.loops]
        return snap


class Service:
    """Build a service: routers, roles, readiness checks. Then `app()`."""

    def __init__(self, name: str, *, version: str = "1.0.0"):
        self.name = name
        self.cfg = get_config()
        self.spec = self.cfg.service(name)
        self.version = version
        self.started_at = datetime.now()
        self.api = ApiRole()
        self.roles: dict[str, Role] = {"api": self.api}
        self._routers: list[tuple[object, str]] = []
        self._startup: list[Callable[[], None]] = []
        self._ready_checks: list[tuple[str, Callable[[], object]]] = []
        self._shutdown = threading.Event()
        self._server = None   # uvicorn.Server, set by run()

    # -- composition -----------------------------------------------------
    def include(self, router, prefix: str = "") -> None:
        self._routers.append((router, prefix))

    def worker(self, name: str, description: str = "") -> WorkerRole:
        spec = self.spec.role(name)
        role = WorkerRole(name, self.name, description or (spec.description if spec else ""))
        self.roles[name] = role
        return role

    def add_role(self, role: Role) -> Role:
        self.roles[role.name] = role
        return role

    def on_startup(self, fn: Callable[[], None]) -> None:
        self._startup.append(fn)

    def ready_check(self, name: str, fn: Callable[[], object]) -> None:
        self._ready_checks.append((name, fn))

    # -- state -----------------------------------------------------------
    def health(self) -> dict:
        roles = {n: r.snapshot() for n, r in self.roles.items()}
        degraded = any(r.get("last_error") for r in roles.values()) or any(
            c.get("last_error") for r in roles.values() for c in r.get("consumers", []))
        return {
            "service": self.name, "title": self.spec.title, "version": self.version,
            "status": "degraded" if degraded else "ok", "pid": os.getpid(),
            "port": self.spec.port, "started_at": self.started_at.isoformat(),
            "uptime_s": int((datetime.now() - self.started_at).total_seconds()),
            "roles": roles,
        }

    def ready(self) -> dict:
        from nexgen.core.db import connect
        checks: dict[str, object] = {}
        ok = True
        try:
            with connect(self.name if self.name in self.cfg.schema_keys else "events") as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:
            ok, checks["database"] = False, f"unreachable: {exc}"
        for name, fn in self._ready_checks:
            try:
                checks[name] = fn() or "ok"
            except Exception as exc:
                ok, checks[name] = False, f"{type(exc).__name__}: {exc}"
        return {"service": self.name, "ready": ok, "checks": checks}

    def _role(self, name: str) -> Role:
        if name not in self.roles:
            raise HTTPException(404, f"{self.name} has no role {name!r}")
        return self.roles[name]

    def _find_job(self, job: str):
        for r in self.roles.values():
            if isinstance(r, WorkerRole) and job in r.jobs.jobs:
                return r
        raise HTTPException(404, f"{self.name} has no job {job!r}")

    def _find_consumer(self, name: str) -> Consumer:
        for r in self.roles.values():
            if isinstance(r, WorkerRole):
                for c in r.consumers:
                    if c.name == name or c.name.endswith("." + name):
                        return c
        raise HTTPException(404, f"{self.name} has no consumer {name!r}")

    def start_roles(self) -> None:
        overrides = _role_overrides()
        for name, role in self.roles.items():
            spec = self.spec.role(name)
            want = overrides.get(name, spec.autostart if spec else True)
            if want:
                try:
                    role.start()
                except Exception as exc:
                    role.last_error = f"start failed: {exc}"
                    logger.exception("role %s failed to start", name)

    def stop_roles(self) -> None:
        for name, role in reversed(list(self.roles.items())):
            try:
                role.stop()
            except Exception:
                logger.exception("role %s failed to stop", name)

    # -- the app ---------------------------------------------------------
    def app(self) -> FastAPI:
        svc = self

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            for fn in svc._startup:
                try:
                    fn()
                except Exception:
                    logger.exception("startup hook failed")
            threading.Thread(target=svc.start_roles, name="start-roles", daemon=True).start()
            yield
            svc.stop_roles()

        app = FastAPI(title=f"NexGen Transport · {self.spec.title}", version=self.version,
                      lifespan=lifespan, docs_url="/docs")
        app.add_middleware(GZipMiddleware, minimum_size=1024)

        @app.middleware("http")
        async def api_switch(request: Request, call_next):
            path = request.url.path
            if not svc.api.running and not path.startswith(_CONTROL_PREFIXES):
                return JSONResponse({"error": "service_stopped", "service": svc.name,
                                     "detail": f"The {svc.spec.title} API is stopped. Start it from the developer page."},
                                    status_code=503)
            return await call_next(request)

        @app.get("/health")
        def health():
            return svc.health()

        @app.get("/ready")
        def ready():
            r = svc.ready()
            return JSONResponse(r, status_code=200 if r["ready"] else 503)

        @app.get("/internal/v1/roles")
        def roles():
            return svc.health()["roles"]

        @app.post("/internal/v1/roles/{role}/start")
        def start_role(role: str):
            r = svc._role(role)
            r.start()
            _save_role_override(svc.name, role, True)
            return r.snapshot()

        @app.post("/internal/v1/roles/{role}/stop")
        def stop_role(role: str):
            r = svc._role(role)
            r.stop()
            _save_role_override(svc.name, role, False)
            return r.snapshot()

        @app.post("/internal/v1/jobs/{job}/run")
        def run_job(job: str):
            role = svc._find_job(job)
            role.jobs.run_in_background(job, "manual")
            return {"started": job}

        @app.post("/internal/v1/consumers/{name}/retry")
        def retry(name: str):
            svc._find_consumer(name).retry_now()
            return {"retrying": name}

        @app.post("/internal/v1/consumers/{name}/skip")
        def skip(name: str, event_id: int):
            svc._find_consumer(name).skip(event_id)
            return {"skipping_to": event_id}

        @app.post("/internal/v1/shutdown")
        def shutdown():
            svc._shutdown.set()
            threading.Thread(target=svc._exit_soon, daemon=True).start()
            return {"stopping": svc.name}

        for router, prefix in self._routers:
            app.include_router(router, prefix=prefix)
        return app

    def _exit_soon(self) -> None:
        time.sleep(0.3)
        if self._server is not None:
            self._server.should_exit = True
        else:  # pragma: no cover
            os.kill(os.getpid(), signal.SIGTERM)

    def run(self) -> None:
        """Serve on the configured port until shut down."""
        import uvicorn

        setup_logging(self.name)
        config = uvicorn.Config(self.app(), host=self.cfg.host, port=self.spec.port,
                                log_level="warning", access_log=False, timeout_keep_alive=30)
        server = uvicorn.Server(config)
        self._server = server
        if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl-Break from the supervisor
            signal.signal(signal.SIGBREAK, lambda *_: setattr(server, "should_exit", True))
        _watch_supervisor(server)
        logger.info("%s listening on %s:%s", self.name, self.cfg.host, self.spec.port)
        server.run()


def _watch_supervisor(server) -> None:
    """Exit when the supervisor that started us is gone.

    Windows does not end child processes with their parent, so a supervisor
    killed from Task Manager would otherwise leave every service running,
    holding its port, with nobody watching it.
    """
    ppid = os.environ.get("NEXGEN_SUPERVISOR_PID")
    if not ppid:
        return

    def watch():
        import psutil
        while not server.should_exit:
            if not psutil.pid_exists(int(ppid)):
                logger.warning("supervisor %s is gone; shutting down", ppid)
                server.should_exit = True
                return
            time.sleep(5)

    threading.Thread(target=watch, name="supervisor-watch", daemon=True).start()


# -- role on/off survives a restart ---------------------------------------
def _role_overrides() -> dict[str, bool]:
    name = os.environ.get("NEXGEN_SERVICE", "")
    try:
        from nexgen.core.db import connect
        with connect("events") as conn, conn.cursor() as cur:
            cur.execute("SELECT s_role, b_enabled FROM role_state WHERE s_service=%s", (name,))
            return {r["s_role"]: bool(r["b_enabled"]) for r in cur.fetchall()}
    except Exception:
        return {}


def _save_role_override(service: str, role: str, enabled: bool) -> None:
    try:
        from nexgen.core.db import connect
        with connect("events") as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO role_state (s_service, s_role, b_enabled) VALUES (%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE b_enabled=VALUES(b_enabled), dt_changed=NOW()",
                        (service, role, int(enabled)))
            conn.commit()
    except Exception:
        logger.exception("could not persist role state for %s.%s", service, role)
