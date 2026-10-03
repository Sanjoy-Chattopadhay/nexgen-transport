"""The edge: one public port for the web app and every API.

Requests to /api/v1/* are passed to the service that owns the path (see
routes.py) and streamed back, so a large export or a server-sent event stream
is never held in memory here. /api/v1/dev/* is answered by the supervisor
itself (the developer page). Anything else is the built web app, with
unknown paths falling back to index.html so client-side routes deep-link.

A path whose service is stopped is answered at once with
`503 service_stopped`, naming the service, instead of a connection error: the
interface can then say "Geofencing is stopped" rather than "something broke".
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from nexgen.core.config import get_config
from nexgen.supervisor.process import Supervisor
from nexgen.supervisor.routes import build_table, resolve

logger = logging.getLogger(__name__)

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
        "trailers", "transfer-encoding", "upgrade", "host"}


def create_app(supervisor: Supervisor) -> FastAPI:
    cfg = get_config()
    table = build_table()
    dist = cfg.path(cfg.run.get("web_dist", "web/dist"))
    client_holder: dict[str, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client_holder["c"] = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=900.0, write=900.0, pool=10.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))
        yield
        await client_holder["c"].aclose()

    app = FastAPI(title="NexGen Transport", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.supervisor = supervisor
    app.state.route_table = table

    from nexgen.supervisor.devapi import router as dev_router
    app.include_router(dev_router)

    @app.get("/health")
    def health():
        snap = supervisor.snapshot()
        down = [s["name"] for s in snap["services"] if s["desired"] == "running" and s["state"] != "running"]
        return {"status": "ok" if not down else "degraded", "not_running": down,
                "services": {s["name"]: s["state"] for s in snap["services"]}}

    @app.api_route("/api/v1/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def proxy(rest: str, request: Request):
        path = request.url.path
        # Read per request: the developer page's configuration reload swaps it.
        route = resolve(app.state.route_table, path)
        if route is None:
            return JSONResponse({"error": "not_found", "detail": f"No service answers {path}."}, status_code=404)
        svc = supervisor.services.get(route.service)
        if svc is None:
            return JSONResponse({"error": "not_found", "detail": f"Unknown service {route.service}."}, status_code=404)
        if svc.desired == "stopped" or not svc.alive():
            state = "stopped" if svc.desired == "stopped" else svc.state
            return JSONResponse(
                {"error": "service_stopped" if state == "stopped" else "service_unavailable",
                 "service": svc.name, "title": svc.spec.title, "state": state,
                 "detail": f"{svc.spec.title} is {state}. Start it from the developer page."},
                status_code=503)
        url = f"{svc.url}{path}"
        if request.url.query:
            url += "?" + request.url.query
        headers = [(k, v) for k, v in request.headers.items()
                   if k.lower() not in _HOP and k.lower() != "accept-encoding"]
        headers.append(("x-forwarded-host", request.headers.get("host", "")))
        # Pass the caller's own Accept-Encoding through. Without this httpx adds
        # "gzip" by default and a caller that cannot decompress gets gzip bytes.
        headers.append(("accept-encoding", request.headers.get("accept-encoding", "identity")))
        client = client_holder["c"]
        has_body = request.method not in ("GET", "HEAD", "OPTIONS") or \
            request.headers.get("content-length") not in (None, "0")
        upstream = client.build_request(request.method, url, headers=headers,
                                        content=request.stream() if has_body else None)
        try:
            resp = await client.send(upstream, stream=True)
        except httpx.ConnectError:
            return JSONResponse({"error": "service_unavailable", "service": svc.name, "title": svc.spec.title,
                                 "state": svc.state,
                                 "detail": f"{svc.spec.title} is not answering yet."}, status_code=503)
        except httpx.TimeoutException:
            return JSONResponse({"error": "upstream_timeout", "service": svc.name,
                                 "detail": f"{svc.spec.title} took too long to answer."}, status_code=504)
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP}
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out_headers,
                                 background=BackgroundTask(resp.aclose))

    # -- the web app ------------------------------------------------------
    @app.get("/{full_path:path}", include_in_schema=False)
    def web(full_path: str):
        index = dist / "index.html"
        if not dist.exists() or not index.exists():
            return Response(
                "<h1>NexGen Transport</h1><p>The web app is not built yet. Run "
                "<code>npm --prefix web install</code> and <code>npm --prefix web run build</code>, "
                "or use the dev server: <code>npm --prefix web run dev</code>.</p>",
                media_type="text/html")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and _inside(candidate, dist):
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
                if "/assets/" in candidate.as_posix() else {}
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    # No compression here: every service already gzips its own answers, and
    # compressing a stream that is already gzip would corrupt it.
    return app


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False
