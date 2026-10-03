# CLAUDE.md

Guidance for Claude Code when working in this repository.

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
python -m nexgen import-legacy       # copy smart_truck + geofencing in (read-only on them)
python -m nexgen geo <command>       # the geofencing engine's commands
python -m pytest -q                  # integration tests skip without MySQL
npm --prefix web run dev             # :5200, proxies /api to :8100
npm --prefix web run build           # tsc -b && vite build -> web/dist
```

`run.bat` / `stop.bat` do the same on Windows. **Do not kill processes on
ports 8000, 8001, 8090 or 5173** — the user runs the legacy apps there.

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
* Web: navigation lives in `web/src/core/nav.ts` — a new page joins a
  section (as a tab) rather than adding a sidebar entry. The geofence
  module's pages are under `/geo/...` and its API under `/api/v1/geo/...`.
  No text below `text-xs` (13 px) and no chart text below 12 px. Colours that
  CSS cannot reach come from `PALETTE` or `tc(hex)` in `core/theme.ts`, never
  a bare hex. No map tile layer (invariant 9).
* Commit and push after every update (the user reverts by commit).
