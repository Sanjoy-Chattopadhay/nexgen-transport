"""Command line entry point.

    python -m nexgen.shared.geoengine.cli init-db
    python -m nexgen.shared.geoengine.cli import-masters
    python -m nexgen.shared.geoengine.cli sync-fleet          # copy the GPS feed in
    python -m nexgen.shared.geoengine.cli build-map           # build the India base map
    python -m nexgen.shared.geoengine.cli live [--mode replay|tail] [--speed 120]
    python -m nexgen.shared.geoengine.cli run [--from D] [--to D] [--trips 1,2] [--limit N] [--workers N]
    python -m nexgen.shared.geoengine.cli report [--run N] [--html PATH] [--json PATH]
    python -m nexgen.shared.geoengine.cli trip <trip_no> [--run N]
    python -m nexgen.shared.geoengine.cli check [--samples N]
    python -m nexgen.shared.geoengine.cli serve

Every subcommand runs under a `__main__` guard, which on Windows is not
optional: the runner spawns worker processes, and without the guard each
worker re-executes the module and spawns its own pool.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from nexgen.shared.geoengine.config import ROOT as ROOT_DIR, settings


def _dt(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise SystemExit(f"cannot parse date: {value!r} (use YYYY-MM-DD[ HH:MM:SS])")


SCHEMA_FILES = ("schema_geofencing.sql", "schema_fleet.sql", "schema_pipeline.sql",
                "schema_upload.sql", "schema_ops.sql", "schema_routing.sql")


def cmd_init_db(args) -> int:
    from nexgen.shared.geoengine.db import apply_schema, ensure_database

    ensure_database()
    applied = 0
    for name in SCHEMA_FILES:
        applied += len(apply_schema(Path(__file__).parent.parent / "migrations" / name))
    print(f"database '{settings.geo_db_name}' ready; {applied} statements applied")
    return 0


def cmd_import_masters(args) -> int:
    from nexgen.shared.geoengine import store
    from nexgen.shared.geoengine.ingest.masters import import_masters

    summary = import_masters(masters_dir=args.dir, truncate=not args.append)
    store.invalidate()
    summary["inradius"] = store.ensure_inradius()
    summary["regions"] = store.ensure_regions(recompute=True)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def cmd_compute_regions(args) -> int:
    from nexgen.shared.geoengine import store

    print(json.dumps(store.ensure_regions(recompute=args.recompute), indent=2))
    return 0


def cmd_import_tolls(args) -> int:
    from nexgen.shared.geoengine.ingest.tolls import import_nh_fee_plazas

    print(json.dumps(import_nh_fee_plazas(), indent=2, default=str))
    return 0


def cmd_compute_inradius(args) -> int:
    from nexgen.shared.geoengine import store

    print(json.dumps(store.ensure_inradius(recompute=args.recompute), indent=2))
    return 0


def cmd_sync_fleet(args) -> int:
    from nexgen.shared.geoengine.ingest.fleet import sync

    out = sync(truncate=args.truncate, limit=args.limit, since=_dt(args.since))
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_sync_trips(args) -> int:
    from nexgen.shared.geoengine.ingest.fleet import sync_trips

    print(json.dumps(sync_trips(), indent=2, default=str))
    return 0


def cmd_build_map(args) -> int:
    from nexgen.shared.geoengine.geo.india import build

    out = build(args.source or (ROOT_DIR / "data" / "source_states"))
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_live(args) -> int:
    from nexgen.shared.geoengine.pipeline.live_runner import main as live_main

    return live_main(mode=args.mode, speed=args.speed, reset=args.reset,
                     poll_seconds=args.poll, window_hours=args.window,
                     start_at=_dt(args.start))


def cmd_run(args) -> int:
    from nexgen.shared.geoengine.pipeline import runner

    trips = None
    if args.trips:
        trips = [int(t) for t in args.trips.split(",") if t.strip()]

    started = datetime.now()

    def progress(done, total, totals):
        pct = 100.0 * done / total if total else 0
        el = (datetime.now() - started).total_seconds()
        rate = totals["read"] / el if el > 0 else 0
        eta = (total - done) / (done / el) if done and el > 0 else 0
        print(f"  {done}/{total} ({pct:5.1f}%)  "
              f"visits={totals['visits']:,} events={totals['events']:,} "
              f"spikes={totals['spikes']:,} stops={totals['stops']:,}  "
              f"{rate:,.0f} pings/s  eta {eta/60:.1f}m",
              flush=True)

    import dataclasses

    fit = dataclasses.replace(settings.fit, variant=args.variant) if args.variant else settings.fit
    osrm = settings.osrm
    if args.osrm_url is not None:
        osrm = dataclasses.replace(osrm, url=args.osrm_url or None)

    out = runner.run(
        trip_nos=trips,
        dt_from=_dt(args.dt_from),
        dt_to=_dt(args.dt_to),
        limit=args.limit,
        workers=args.workers,
        scope=args.scope,
        fit=fit,
        osrm=osrm,
        feed=args.feed,
        persist_fit=not args.no_fit_pings,
        publish=args.publish,
        progress=progress,
    )
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_publish(args) -> int:
    from nexgen.shared.geoengine.pipeline import runner

    runner.publish_run(args.run)
    print(f"run {args.run} is now the published run")
    return 0


def cmd_delete_run(args) -> int:
    from nexgen.shared.geoengine.pipeline import runner

    print(json.dumps(runner.delete_run(args.run), indent=2))
    return 0


def cmd_summarise(args) -> int:
    from nexgen.shared.geoengine.pipeline import runner, summaries

    run_id = args.run or runner.published_run_id()
    print(json.dumps(summaries.build(run_id), indent=2, default=str))
    return 0


def cmd_reprocess(args) -> int:
    from nexgen.shared.geoengine.pipeline import runner, summaries

    run_id = args.run or runner.published_run_id()
    trips = [int(t) for t in args.trips.split(",") if t.strip()]
    out = runner.reprocess(run_id, trips)
    if not args.no_summaries:
        out["summaries"] = summaries.build(run_id)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_refresh(args) -> int:
    from nexgen.shared.geoengine.pipeline import runner, scheduler, summaries

    if not (args.full or args.dry_run):
        # One incremental pass: only the trips with new fixes, and only the
        # vehicles, days and fences they touch (pipeline/incremental.py).
        out = scheduler.refresh_once(trigger="manual", sync_fleet=args.sync_fleet, run_id=args.run)
        if args.summarise and out.get("run_id"):
            out["summaries"] = summaries.build(out["run_id"])
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in ("ok", "idle") else 1

    out: dict = {}
    if args.sync_fleet:
        from nexgen.shared.geoengine.db import geo_session
        from nexgen.shared.geoengine.ingest import fleet

        with geo_session() as conn, conn.cursor() as cur:
            cur.execute("SELECT MAX(dt_message) t FROM geo_gps_ping")
            latest = cur.fetchone()["t"]
        # Fixes are keyed by source id, so re-reading an overlap is harmless;
        # the overlap catches fixes a device uploaded late.
        since = latest - timedelta(hours=args.lookback_hours) if latest else None
        out["sync"] = fleet.sync(since=since)
    out.update(runner.refresh(args.run, batch=args.batch, dry_run=args.dry_run))
    if not args.dry_run and (out["stale_trips"] or args.summarise):
        out["summaries"] = summaries.build(out["run_id"])
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_scheduler(args) -> int:
    from nexgen.shared.geoengine.pipeline import scheduler

    cfg = settings.scheduler
    return scheduler.main(every_minutes=args.every or cfg.every_minutes,
                          sync_fleet=args.sync_fleet or cfg.sync_fleet, once=args.once)


def cmd_build_phases(args) -> int:
    from nexgen.shared.geoengine.db import geo_session
    from nexgen.shared.geoengine.pipeline import phases, runner

    run_id = args.run or runner.published_run_id()
    trips = [int(t) for t in args.trips.split(",")] if args.trips else None
    with geo_session() as conn:
        n = phases.build(conn, run_id, trips)
    print(json.dumps({"run_id": run_id, "trip_phases": n}))
    return 0


def cmd_analyse_routes(args) -> int:
    from nexgen.shared.geoengine.db import geo_session
    from nexgen.shared.geoengine.pipeline import runner
    from nexgen.shared.geoengine.routing import analysis

    run_id = args.run or runner.published_run_id()
    trips = [int(t) for t in args.trips.split(",")] if args.trips else None
    with geo_session() as conn:
        out = analysis.analyse_trips(conn, run_id, trips, mode=args.mode)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_compare(args) -> int:
    from nexgen.shared.geoengine.pipeline.compare import compare

    out = compare(args.a, args.b, sample=args.sample)
    text = json.dumps(out, indent=2, default=str)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
        print(f"wrote {args.json}")
    else:
        print(text)
    return 0


def cmd_osrm_health(args) -> int:
    import dataclasses

    from nexgen.shared.geoengine.osrm.client import OsrmClient

    cfg = settings.osrm if not args.url else dataclasses.replace(settings.osrm, url=args.url)
    result = OsrmClient(cfg).health()
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("ok", "off") else 2


def cmd_report(args) -> int:
    from nexgen.shared.geoengine.reporting import reports

    run_id = args.run or reports.latest_run_id()
    if run_id is None:
        print("no completed runs; run `python -m nexgen.shared.geoengine.cli run` first")
        return 1

    data = reports.build(run_id)
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    if args.html:
        html = reports.render_html(data)
        Path(args.html).write_text(html, encoding="utf-8")
        print(f"wrote {args.html}")
    if not args.json and not args.html:
        print(reports.render_text(data))
    return 0


def cmd_trip(args) -> int:
    from nexgen.shared.geoengine.reporting import reports

    run_id = args.run or reports.latest_run_id()
    if run_id is None:
        print("no completed runs")
        return 1
    data = reports.trip_report(run_id, args.trip_no)
    if not data:
        print(f"trip {args.trip_no} not present in run {run_id}")
        return 1
    print(json.dumps(data, indent=2, default=str))
    return 0


def cmd_check(args) -> int:
    from nexgen.shared.geoengine.validate import cross_check

    result = cross_check(samples=args.samples)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["mismatches"] == 0 else 2


def cmd_export_trip(args) -> int:
    """Write a trip's GPS out as a spreadsheet, ready to upload back in."""
    from nexgen.shared.geoengine.db import geo_session
    from nexgen.shared.geoengine.upload import sample

    with geo_session() as conn:
        try:
            data, name, meta = sample.build(conn, args.km, args.trip)
        except LookupError as exc:
            raise SystemExit(str(exc)) from exc
    out = Path(args.out) if args.out else settings.out_dir / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(json.dumps({"file": str(out), "bytes": len(data), **{
        k: v for k, v in meta.items() if not k.startswith("_")}}, indent=2, default=str))
    return 0


def cmd_analyse_upload(args) -> int:
    """Read a spreadsheet of GPS and run the whole pipeline over it."""
    from nexgen.shared.geoengine.upload import store as upload_store

    if args.file:
        data = Path(args.file).read_bytes()
        upload_id = upload_store.create(data, Path(args.file).name, args.label)
        print(f"upload {upload_id} parsed from {args.file}")
    else:
        upload_id = args.upload
        if not upload_id:
            raise SystemExit("give a --file to upload, or an --upload id to re-analyse")
    print(json.dumps(upload_store.analyse(upload_id), indent=2, default=str))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("nexgen.shared.geoengine.api.app:app", host=args.host or settings.api_host,
                port=args.port or settings.api_port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="geofencing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="create the database and apply the schema").set_defaults(fn=cmd_init_db)

    sf = sub.add_parser("sync-fleet", help="copy the GPS feed from the source DB")
    sf.add_argument("--truncate", action="store_true", help="replace instead of top up")
    sf.add_argument("--limit", type=int, default=None)
    sf.add_argument("--since", default=None, help="only fixes at or after this time")
    sf.set_defaults(fn=cmd_sync_fleet)

    sub.add_parser("sync-trips", help="copy the trip master (vehicle, driver, transporter, lane)"
                   ).set_defaults(fn=cmd_sync_trips)

    bm = sub.add_parser("build-map", help="build the India base map layers")
    bm.add_argument("--source", default=None, help="directory of per-state GeoJSON")
    bm.set_defaults(fn=cmd_build_map)

    lv = sub.add_parser("live", help="run the live streaming detector")
    lv.add_argument("--mode", default="replay", choices=("replay", "tail"))
    lv.add_argument("--speed", type=float, default=120.0,
                    help="replay multiplier; 120 = one hour of feed per 30 s")
    lv.add_argument("--reset", action="store_true",
                    help="clear live state and start from the beginning")
    lv.add_argument("--poll", type=float, default=2.0)
    lv.add_argument("--window", type=float, default=None,
                    help="replay only the last N hours of the feed")
    lv.add_argument("--start", default=None,
                    help="replay from this feed timestamp")
    lv.set_defaults(fn=cmd_live)

    q = sub.add_parser("import-masters", help="load the geofence CSVs")
    q.add_argument("--dir", default=None, help="masters directory (default from .env)")
    q.add_argument("--append", action="store_true", help="do not clear the existing master first")
    q.set_defaults(fn=cmd_import_masters)

    cr = sub.add_parser("compute-regions", help="record the state and district of every fence")
    cr.add_argument("--recompute", action="store_true", help="also fences that already have one")
    cr.set_defaults(fn=cmd_compute_regions)

    it = sub.add_parser("import-tolls", help="load the National Highway toll plaza list")
    it.set_defaults(fn=cmd_import_tolls)

    ir = sub.add_parser("compute-inradius",
                        help="measure each fence's inscribed radius (drives the adaptive band)")
    ir.add_argument("--recompute", action="store_true")
    ir.set_defaults(fn=cmd_compute_inradius)

    r = sub.add_parser("run", help="evaluate trips against the master")
    r.add_argument("--from", dest="dt_from", default=None)
    r.add_argument("--to", dest="dt_to", default=None)
    r.add_argument("--trips", default=None, help="comma-separated trip numbers")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--workers", type=int, default=None)
    r.add_argument("--scope", default=None)
    r.add_argument("--variant", choices=("fitted", "raw"), default=None,
                   help="detect on fitted positions (default) or on cleaned raw fixes")
    r.add_argument("--feed", choices=("geo", "src"), default="geo",
                   help="read this module's copy of the feed (default) or the source DB")
    r.add_argument("--osrm-url", default=None,
                   help="override OSRM_URL for this run; empty string disables OSRM")
    r.add_argument("--no-fit-pings", action="store_true",
                   help="do not store the per-ping fitted trail")
    r.add_argument("--publish", action="store_true",
                   help="make this the run the application reads, once it finishes")
    r.set_defaults(fn=cmd_run)

    pb = sub.add_parser("publish", help="make a finished run the one the application reads")
    pb.add_argument("run", type=int)
    pb.set_defaults(fn=cmd_publish)

    dr = sub.add_parser("delete-run", help="remove an unpublished run and everything it wrote")
    dr.add_argument("run", type=int)
    dr.set_defaults(fn=cmd_delete_run)

    sm = sub.add_parser("summarise", help="rebuild a run's rollup tables")
    sm.add_argument("--run", type=int, default=None)
    sm.set_defaults(fn=cmd_summarise)

    rp = sub.add_parser("reprocess", help="re-evaluate trips into an existing run")
    rp.add_argument("--trips", required=True, help="comma-separated trip numbers")
    rp.add_argument("--run", type=int, default=None, help="default: the published run")
    rp.add_argument("--no-summaries", action="store_true")
    rp.set_defaults(fn=cmd_reprocess)

    rf = sub.add_parser("refresh", help="bring the published run up to date with the feed: one incremental "
                                        "pass of the scheduler (--full: re-evaluate every stale trip and "
                                        "rebuild every summary)")
    rf.add_argument("--full", action="store_true",
                    help="compare the whole feed with the run and rebuild every summary, as before the "
                         "incremental refresh existed")
    rf.add_argument("--run", type=int, default=None, help="default: the published run")
    rf.add_argument("--sync-fleet", action="store_true",
                    help="first copy new fixes and trip records from the source database")
    rf.add_argument("--lookback-hours", type=float, default=24.0,
                    help="with --sync-fleet: re-read this much before the newest fix held")
    rf.add_argument("--batch", type=int, default=200, help="trips re-evaluated per batch")
    rf.add_argument("--summarise", action="store_true", help="rebuild summaries even if nothing changed")
    rf.add_argument("--dry-run", action="store_true", help="only list the trips that would be re-evaluated")
    rf.set_defaults(fn=cmd_refresh)

    sc = sub.add_parser("scheduler", help="keep the published run current: a refresh every few minutes, "
                                          "re-evaluating only what changed")
    sc.add_argument("--every", type=float, default=None, help="minutes between passes (default 15, "
                                                              "REFRESH_EVERY_MINUTES)")
    sc.add_argument("--sync-fleet", action="store_true",
                    help="copy new fixes and trip records from the fleet system on each pass")
    sc.add_argument("--once", action="store_true", help="one pass and exit (for cron / Task Scheduler)")
    sc.set_defaults(fn=cmd_scheduler)

    bp = sub.add_parser("build-phases", help="rebuild each trip's loading / transit / unloading number line")
    bp.add_argument("--run", type=int, default=None)
    bp.add_argument("--trips", default=None, help="comma-separated trip numbers (default: all)")
    bp.set_defaults(fn=cmd_build_phases)

    ar = sub.add_parser("analyse-routes", help="each loaded trip against its planned route: distance, "
                                               "deviations, reroutes, cost")
    ar.add_argument("--run", type=int, default=None)
    ar.add_argument("--trips", default=None, help="comma-separated trip numbers (default: all)")
    ar.add_argument("--mode", choices=("learned", "osrm"), default=None,
                    help="plan source (default: osrm when OSRM_URL is set, else learned)")
    ar.set_defaults(fn=cmd_analyse_routes)

    cp = sub.add_parser("compare", help="diff two runs over their common trips (e.g. raw vs fitted)")
    cp.add_argument("a", type=int)
    cp.add_argument("b", type=int)
    cp.add_argument("--sample", type=int, default=25)
    cp.add_argument("--json", default=None)
    cp.set_defaults(fn=cmd_compare)

    oh = sub.add_parser("osrm-health", help="check the configured OSRM server")
    oh.add_argument("--url", default=None)
    oh.set_defaults(fn=cmd_osrm_health)

    s = sub.add_parser("report", help="build the report for a run")
    s.add_argument("--run", type=int, default=None)
    s.add_argument("--html", default=None)
    s.add_argument("--json", default=None)
    s.set_defaults(fn=cmd_report)

    t = sub.add_parser("trip", help="one trip's detail")
    t.add_argument("trip_no", type=int)
    t.add_argument("--run", type=int, default=None)
    t.set_defaults(fn=cmd_trip)

    c = sub.add_parser("check", help="cross-check the engine against MySQL ST_Contains")
    c.add_argument("--samples", type=int, default=20000)
    c.set_defaults(fn=cmd_check)

    et = sub.add_parser("export-trip", help="write a trip's GPS out as a spreadsheet")
    et.add_argument("--trip", type=int, default=None, help="a trip number; omit to pick one")
    et.add_argument("--km", type=float, default=500.0, help="how long a trip to pick")
    et.add_argument("--out", default=None, help="where to write it (default: out/)")
    et.set_defaults(fn=cmd_export_trip)

    au = sub.add_parser("analyse-upload",
                        help="run the pipeline over a spreadsheet of GPS, step by step")
    au.add_argument("--file", default=None, help="the .xlsx / .csv / .json to read")
    au.add_argument("--upload", type=int, default=None, help="re-analyse an existing upload")
    au.add_argument("--label", default=None)
    au.set_defaults(fn=cmd_analyse_upload)

    v = sub.add_parser("serve", help="run the HTTP API")
    v.add_argument("--host", default=None)
    v.add_argument("--port", type=int, default=None)
    v.set_defaults(fn=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
