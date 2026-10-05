# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**NexGen Transport** — one product built from two applications that stay
untouched beside it: **Smart-Truck** (fleet analytics on the eTrans TTA feed,
ML models) and the **Geo-Fencing module** (polygon geofencing, trip phases,
routes against plans, live detection). Every working feature of both is
available here, as modules of one interface, one API and one database design.
There is no "Smart-Truck part" and "Geofencing part" in the product: the
navigation, the API and the tables are organised by function.

The architecture was approved on 2026-10-03 (docs/ARCHITECTURE.md). The
user's changes at approval are binding:

* **No login for now.** Every request acts as the default tenant; the tenant
  context is still threaded through so login can be added later.
* **GPS: accept and process everything.** Nothing is dropped on the way in.
  The pre-filter flags fixes, it never deletes them, and it can be switched
  off. Pre-filtered (OSRM-style) positions from a source land in the same
  `gps_fix` table, marked `c_source='F'`.
* **Do not remove any functionality** of either legacy application.
* **One readable font size** across the UI; no tiny text (no 10–11 px labels).
* **A developer page** shows every module's health and starts/stops each one.

Stack: Python 3.11+ (dev on 3.13) / FastAPI / PyMySQL / NumPy / APScheduler;
React 19 + TypeScript + Vite + Tailwind v4 + Leaflet + Recharts + three.js;
MySQL 8. One public port, **http://127.0.0.1:8100** (the gateway).

## Non-negotiable invariants

1. **The legacy databases are never written to.** `smart_truck` and
   `geofencing` are opened only through `nexgen.core.db.legacy()`, which runs
   `SET SESSION TRANSACTION READ ONLY`. They are the import source and the
   comparison baseline.
2. **A service writes only its own schema.** Each service owns one `nx_*`
   schema (database.yaml). Reads across services go through the owner's
   published `v1_*` views (or the consumer's local alias views over them,
   created by its `R__*.sql` migrations) — never another service's tables.
3. **Events are written in the same transaction as the change**
   (`nexgen.core.events.publish(conn, ...)`), carry ids/windows/touched keys
   only, and every consumer is idempotent.
4. **Accuracy beats speed** (inherited from Geo-Fencing). Engines are ported
   unchanged; every port must reproduce the legacy figures on the same input
   or document why not. Do not remove a stage because it is slow.
5. **Nothing is dropped from GPS.** `gps_fix` holds every fix; a second fix in
   the same second gets `i_seq` 1, 2, …; flagged fixes keep their flag.
6. **Nested fences are all reported; the smallest is primary. Never aggregate
   across fence scales. Cross-trip figures read the physical ledger.** (The
   Geo-Fencing invariants, unchanged — see its CLAUDE.md in the old repo.)
7. **Pages read the published run** unless `?run=` names another.
8. **Every number carries its confidence** (trail verdicts, gap seconds).
9. **No commercial map tiles.** Every map draws India's official geometry
   (`data/india_states.json`, `data/india_districts.json`).
10. **OSRM is optional and off by default.** Every stage has defined
    behaviour without it and records which it got.
11. **Client data stays out of git**: `Masters/`, `*.csv`, `*.xlsx`, `.env*`,
    `web/src/guide-data/`. Check `.gitignore` before adding a data file.
12. **Two config files**: `config/services.yaml` (services, routes,
    schedules, external APIs, engine settings) and `config/database.yaml`
    (connections). Per-client business settings in `config/tenants/`.
    Secrets only in `.env`, referenced as `${NAME}`. No settings in code.

## Commands

```bash
python -m nexgen run                 # migrations, every service, gateway on :8100
python -m nexgen run --only fleet,analytics
python -m nexgen serve fleet         # one service in this process (split mode)
python -m nexgen migrate             # apply pending migrations (all schemas)
python -m nexgen migrate --status
python -m nexgen status | start X | stop X | restart X
python -m nexgen shutdown            # stop every service and the gateway
python -m nexgen import-legacy       # copy smart_truck + geofencing in (read-only on them)
python -m nexgen reset               # DROPS NexGen's databases -- never without the user asking
python -m nexgen geo <command>       # the geofencing engine: run, publish, refresh, summarise, compare, ...
python -m pytest -q                  # integration tests skip without MySQL
python -m pytest tests/test_congestion.py::test_threshold_thin_baseline_and_capacity
npm --prefix web run dev             # :5200, proxies /api to :8100
npm --prefix web run typecheck       # tsc -b (there is no lint script)
npm --prefix web run build           # tsc -b && vite build -> web/dist (what the gateway serves)
```

`run.bat` / `stop.bat` do the same on Windows (`run.bat /build` rebuilds the
web app, `run.bat /setup` reinstalls everything). **Do not kill processes on
ports 8000, 8001, 8090 or 5173** — the user runs the legacy apps there — and
do not stop the user's own NexGen (8100-8107) to test a change: run yours on a
spare port instead.

`pytest.ini` turns every `DeprecationWarning` into an error, so deprecated
stdlib calls (e.g. `datetime.utcfromtimestamp`) fail the suite.

A git worktree has no `.env` (it is gitignored), so anything touching MySQL
fails there with "Access denied ... using password: NO". The config loader
reads `<repo>/.env` with `override=False`, so exported variables (or
`load_dotenv` on the main checkout's `.env` before importing `nexgen`) work.
Code that imports the shared legacy engines outside a running service needs
`NEXGEN_SERVICE=<service>` (or `legacy_db.use_schema()`) to pick its schema.

## Layout

| Path | What |
|---|---|
| `config/` | `services.yaml`, `database.yaml`, `tenants/` |
| `nexgen/core/` | config loader, db, migrate, events (outbox), jobs, service runtime, tenancy, logs |
| `nexgen/supervisor/` | process manager, gateway (route table + streaming proxy + static web), developer API |
| `nexgen/services/<name>/` | one package per service; `service.py: build() -> Service` |
| `nexgen/shared/` | domain code used by more than one service (pure logic + SQL against contract views) |
| `migrations/<schema>/` | `NNNN_*.sql` versioned (never edited once applied), `R__*.sql` views re-applied every run |
| `web/` | the one React app: `src/core` (shell) + `src/modules/*` |
| `data/` | official India geometry, NH fee plazas (committed) |
| `docs/` | architecture, data model, configuration, contracts, runbook |

Services: platform 8101, ingestion 8102, fleet 8103, geofence 8104,
routing 8105, analytics 8106, ml 8107. Each is one process; its worker roles
are threads that start/stop independently (`/internal/v1/roles/...`).

## How the pieces fit

* **A service** is `nexgen/services/<name>/service.py: build()`, returning a
  `core.service.Service`: `svc.include(router)` for its API, and
  `svc.worker("name")` roles holding `.loop(...)` threads, `.consumer(name,
  [event types], handler)` outbox subscriptions and `.jobs.add(..., cron=)`
  schedules. The supervisor runs each service as its own process.
* **Routing**: the gateway sends a request to the service whose
  `services.yaml` `routes` pattern matches most specifically.
  `tests/test_gateway_coverage.py` fails when a service serves a path no
  route reaches — a new router needs a routes entry unless an existing
  wildcard covers it (`/api/v1/geo/*` does for geofence).
* **Legacy engines run unchanged** on the new storage: Geo-Fencing's engine
  (`shared/geoengine`) and Smart-Truck's analysis code read their old table
  names, which each consumer's `R__aliases.sql` defines as views over the
  owners' `v1_*` views. The geofence engine is also hosted by routing, so
  schema choice comes from `legacy_db.current_schema_key()`, not a constant.
* **Geofence results** belong to a *run* (`geo_run`); pages read the
  published one via `api/v1/common.resolve_run`. Two ledgers: per-trip
  (`geo_visit`, `geo_violation`, `geo_stop`) for one consignment's view, and
  physical (`geo_pvisit`, `geo_palert`, `geo_pstop`, shares in
  `geo_trip_share`) for anything across trips (pipeline/physical.py). Fence
  scale is by area (`store.scale_of`: micro < 1 ha, site < 1 km², campus
  < 100 km², regional). The fence master draws many places more than once,
  sometimes as identical polygons under several site ids.
* **Caching**: geofence and routing GET responses are cached per *data
  version* (`geoengine/api/cache.py` + the `version_cache` middleware; JSON
  only). A new `/api/v1/geo/` endpoint is cached automatically and goes stale
  only when the data version moves; in-process models built from a run
  (e.g. `api/v1/plants.get_model`) key on the same version.
* **Proof** has three backends, one rule — the server recounts the tile's
  figure from the records it returns, and the two must agree:
  fleet pages: `/api/v1/proof/{dataset}` (`services/analytics/proof.py`),
  shown by `ProofGrid` + `KPICard proof=`; geofence pages:
  `/api/v1/geo/drill/{dataset}` (`api/v1/drill.py`), shown by `KPIGrid` +
  `KPI drill=`; plants & congestion: `/api/v1/geo/plants/proof/{dataset}`,
  through `ProofPanel` with `ProofSpec.endpoint`.
* **Web**: the geofence module calls the API with `fetch` (`lib/api.ts`,
  `useApi`, which refetches when the published figures change); the
  analytics module uses axios `backendApi`, whose interceptors add the
  consignor and trip-class filters. `PALETTE` is the live theme — read it at
  render time, never copy it into a constant.

## Conventions

* `from __future__ import annotations` everywhere. Module docstrings carry
  the *reasoning* (why this exists, what the alternative lost, the measured
  number behind a default) — match that.
* Column prefixes: `i_` int · `s_` string · `d_` decimal/double · `dt_`
  datetime · `b_` boolean · `c_` code · `j_` JSON · `g_` geometry. Indexes
  `idx_*`, spatial `sidx_*`. Every client-owned row starts with `i_tenant_id`.
* Times are the source wall clock (Asia/Kolkata), stored naive.
* A schema change is a new numbered file in `migrations/<schema>/`;
  expand → backfill → contract, never one destructive step.
* A new write path that changes published figures must publish its event,
  so analytics recomputes and caches drop.
* **Every calculated figure carries its proof** (the user's standing rule,
  2026-10-04): the reader can open it and see what is counted (every filter
  that applied and every one that did not), the arithmetic with the window's
  own numbers, what was left out and why, the records themselves (paged and
  CSV), and the figure recounted from those records beside the tile. Fleet
  pages: a `proof` on KPICard inside a `ProofGrid` (web/src/core/proof),
  backed by a dataset in nexgen/services/analytics/proof.py counted exactly
  like its tile. Geofence pages: their drill datasets. A figure without a
  proof is unfinished.
* Web: navigation lives in `web/src/core/nav.ts` — a new page joins a
  section (as a tab) rather than adding a sidebar entry. The geofence
  module's pages are under `/geo/...` and its API under `/api/v1/geo/...`.
  No text below `text-xs` (13 px) and no chart text below 12 px. Colours that
  CSS cannot reach come from `PALETTE` or `tc(hex)` in `core/theme.ts`, never
  a bare hex. No map tile layer (invariant 9).
* Commit and push after every update (the user reverts by commit).
