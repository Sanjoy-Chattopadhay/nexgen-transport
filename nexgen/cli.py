"""Command line: `python -m nexgen <command>`.

    run                       start everything: migrations, every service, the gateway
    serve <service>           run one service in this process (split mode / docker)
    migrate [--schema s] [--dry-run] [--status]
    status                    what is running (asks the gateway)
    start|stop|restart <svc>  steer one service (asks the gateway)
    import-legacy [...]       copy the two legacy databases in (read-only on them)
    geo <command> ...         the geofencing engine's own commands
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys

logger = logging.getLogger("nexgen")


def _load_service(name: str):
    module = importlib.import_module(f"nexgen.services.{name}.service")
    return module.build()


def cmd_serve(args) -> int:
    import os
    os.environ.setdefault("NEXGEN_SERVICE", args.service)
    svc = _load_service(args.service)
    svc.run()
    return 0


def cmd_run(args) -> int:
    import uvicorn

    from nexgen.core.config import get_config
    from nexgen.core.logs import setup_logging
    from nexgen.supervisor.gateway import create_app
    from nexgen.supervisor.process import Supervisor

    setup_logging("supervisor")
    cfg = get_config()
    if not args.no_migrate:
        from nexgen.core.migrate import migrate
        try:
            for row in migrate():
                if row["action"] == "applied":
                    logger.info("migration %s/%s applied in %s ms", row["schema"], row["file"], row["ms"])
        except Exception:
            logger.exception("migrations failed; fix them before starting (or pass --no-migrate)")
            return 1
    sup = Supervisor()
    if args.only:
        wanted = set(args.only.split(","))
        for name, svc in sup.services.items():
            if name not in wanted:
                svc.desired = "stopped"
    app = create_app(sup)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.gateway_port,
                                           log_level="warning", access_log=False))
    app.state.server = server
    sup.start_all()
    logger.info("NexGen Transport gateway on http://%s:%s", cfg.host, cfg.gateway_port)
    try:
        server.run()
    finally:
        logger.info("stopping services")
        sup.shutdown()
    return 0


def cmd_shutdown(args) -> int:
    _gateway("POST", "/api/v1/dev/shutdown")
    print("NexGen Transport is shutting down.")
    return 0


def cmd_migrate(args) -> int:
    from nexgen.core.logs import setup_logging
    from nexgen.core.migrate import migrate, status

    setup_logging("migrate")
    if args.status:
        print(json.dumps(status(), indent=2, default=str))
        return 0
    report = migrate([args.schema] if args.schema else None, dry_run=args.dry_run)
    for row in report:
        print(f"{row['schema']:<10} {row['action']:<12} {row['file']}"
              + (f"  ({row.get('ms')} ms)" if row.get("ms") is not None else ""))
    if not report:
        print("nothing to do")
    return 0


def _gateway(method: str, path: str):
    import httpx

    from nexgen.core.config import get_config
    cfg = get_config()
    url = f"http://{cfg.host}:{cfg.gateway_port}{path}"
    try:
        r = httpx.request(method, url, timeout=60.0)
    except httpx.ConnectError:
        print(f"NexGen Transport is not running (nothing on port {cfg.gateway_port}). "
              "Start it with: python -m nexgen run")
        sys.exit(2)
    r.raise_for_status()
    return r.json()


def cmd_status(args) -> int:
    data = _gateway("GET", "/api/v1/dev/overview")
    print(f"{'service':<11} {'state':<9} {'desired':<8} {'pid':>7} {'port':>5}  roles")
    for s in data["services"]:
        roles = ", ".join(f"{n}:{'on' if r.get('running') else 'off'}" for n, r in (s.get("roles") or {}).items())
        print(f"{s['name']:<11} {s['state']:<9} {s['desired']:<8} {s['pid'] or '-':>7} {s['port']:>5}  {roles}")
    return 0


def cmd_control(args) -> int:
    data = _gateway("POST", f"/api/v1/dev/services/{args.service}/{args.action}")
    print(f"{data['name']}: {data['state']}")
    return 0


def cmd_import_legacy(args) -> int:
    from nexgen.core.logs import setup_logging
    from nexgen.tools.legacy_import import run_import

    setup_logging("import-legacy")
    report = run_import(parts=args.parts.split(",") if args.parts else None, limit_trips=args.limit_trips)
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_geo(args) -> int:
    """The geofencing engine's own commands (Geo-Fencing's cli), run against
    the geofence service's schema: `python -m nexgen geo run --publish`."""
    import os
    os.environ.setdefault("NEXGEN_SERVICE", "geofence")
    from nexgen.shared.geoengine import cli as geo_cli
    return geo_cli.main(args.geo_args) or 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nexgen", description="NexGen Transport")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start everything")
    r.add_argument("--no-migrate", action="store_true", help="skip applying pending migrations")
    r.add_argument("--only", help="comma-separated services to start (others stay stopped)")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("serve", help="run one service in this process")
    s.add_argument("service")
    s.set_defaults(fn=cmd_serve)

    m = sub.add_parser("migrate", help="apply database migrations")
    m.add_argument("--schema")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--status", action="store_true")
    m.set_defaults(fn=cmd_migrate)

    st = sub.add_parser("status", help="what is running")
    st.set_defaults(fn=cmd_status)

    sd = sub.add_parser("shutdown", help="stop every service and the gateway")
    sd.set_defaults(fn=cmd_shutdown)

    for action in ("start", "stop", "restart"):
        a = sub.add_parser(action, help=f"{action} one service")
        a.add_argument("service")
        a.set_defaults(fn=cmd_control, action=action)

    il = sub.add_parser("import-legacy", help="copy smart_truck and geofencing in (read-only on them)")
    il.add_argument("--parts", help="comma-separated parts (default: all)")
    il.add_argument("--limit-trips", type=int, default=None, help="import only this many trips (trial runs)")
    il.set_defaults(fn=cmd_import_legacy)

    g = sub.add_parser("geo", help="geofencing engine commands", add_help=False)
    g.add_argument("geo_args", nargs=argparse.REMAINDER)
    g.set_defaults(fn=cmd_geo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)
