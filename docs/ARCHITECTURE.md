# NexGen Transport — architecture

Approved 2026-10-03 (the illustrated proposal:
https://claude.ai/artifact/TufPUhuvUj1AEdyN5trEBF). This file is the
maintained version; where the two differ, this one is current.

## What changed at approval

| Decision | Outcome |
|---|---|
| Services | seven: platform, ingestion, fleet, geofence, routing, analytics, ml |
| Events | transactional outbox in MySQL (`nx_events`); Redis/Kafka later behind the same interface |
| Maps | India's official geometry everywhere; no commercial tiles |
| Login | **none for now** — every request acts as the default tenant |
| GPS | **accept and process everything**; filtering is optional; pre-filtered input maps into the same table |
| Functionality | **nothing removed** from either legacy app |
| UI | **one readable font size**; a **developer page** to watch and start/stop every module |
| Retention | raw payloads 90 d; GPS fixes 180 d online then archived; geofence ledgers 2 y |
| History | fresh git history; the old repos keep theirs |
| Time | source wall clock (Asia/Kolkata), stored naive |

## Shape

```
browser ──▶ gateway :8100 ── /api/v1/dev/*  developer API (supervisor)
             │             ── /api/v1/*      routed by path to the owning service
             │             ── /*             the web app (web/dist)
             ▼
  platform 8101   ingestion 8102   fleet 8103   geofence 8104
  routing 8105    analytics 8106   ml 8107
             │ each writes only its own schema; events via nx_events outbox
             ▼
  MySQL 8: nx_platform nx_ref nx_ingest nx_fleet nx_geo nx_route nx_analytics nx_ml nx_events
           smart_truck, geofencing  (legacy, READ ONLY: import + comparison)
```

`python -m nexgen run` starts the supervisor: it applies migrations, launches
each service as its own process, restarts a crashed one (within limits), and
serves the gateway. A service's worker roles are threads inside it that start
and stop independently; the API role can be switched off too (routes answer
`503 service_stopped`). The supervisor persists what you asked for, so a
stopped module stays stopped across restarts.

## Services and what they own

| Service | Owns (schema) | Roles | Reads |
|---|---|---|---|
| platform | tenants, settings overrides + audit, reference data (`nx_platform`, `nx_ref`) | api, ops (backups, trims) | — |
| ingestion | connections, lanes, watermarks, sync runs, data gaps, batches, landing rows, raw payloads, uploads, imports (`nx_ingest`) | api, worker | TMS API, files, legacy DBs (read-only) |
| fleet | dimensions (vehicles, devices, drivers, transporters, consignees, locations, waypoints), trips, provider metrics, source records, `gps_fix`, per-trip GPS windows and distance overrides, processed batches (`nx_fleet`) | api, processor | ingestion's batch and payload views |
| geofence | the pre-filter and fit (fitted trails, stops, gaps), sites, fences, runs, crossings, visits, violations, physical ledger, phases, rollups, live state; Smart-Truck's circle fences and origin-exit results (`nx_geo`) | api, detector, live | fleet contract views |
| routing | plans, trip routes, deviations, cost (`nx_route`) | api, worker | fleet + geofence contracts |
| analytics | every Smart-Truck analysis table (network, waypoint, speed, hotspot aggregates; plant-delay and weather caches; settings), the historic trip store and its driver / vehicle / route / customer / day summaries (`nx_analytics`) | api, kpi | every owner's contract views |
| ml | models, predictions (`nx_ml`) | api, trainer | analytics contract views |

### Routing the legacy URLs

Every Smart-Truck URL keeps working: the gateway routes by the most specific
path pattern (services.yaml `routes`), so `/api/v1/tta/trips/7/weather` goes to
routing while `/api/v1/tta/trips/7` goes to analytics. Geo-Fencing's API moved
under `/api/v1/geo/*` (its paths collided with Smart-Truck's `/trips`,
`/vehicles`, `/drivers`, `/transporters`, `/routes`).

## Data

Three layers:

* **Raw** (ingestion): every source block as received — a batch row and one
  zlib-compressed payload per trip, kept 90 days so any batch can be replayed
  (`ingest.batch.landed` → the fleet processor).
* **Clean** (fleet, geofence, routing): normalised and deduplicated by fleet;
  pre-filtered and fitted by geofence, where the engine that needs it lives.
* **KPI** (analytics): the summaries and aggregates the pages read, kept
  current by the kpi worker from events (each changed trip's drivers,
  vehicles, routes, customers and days) and by its schedules. Dedicated
  `kpi_trip` / `kpi_day` marts with a nightly full-recompute proof are the
  next step; they are not built yet.

### GPS

One row per physical fix, keyed `(i_tenant_id, i_vehicle_id, dt_fix, i_seq)`,
partitioned by month, names replaced by ids (waypoint and status
dictionaries), coordinates as exact `DECIMAL(10,8)` / `DECIMAL(11,8)` — 3.4% of
the feed's values carry eight decimals, which a degrees × 10⁷ integer would
round. Measured on
3 October 2026: today's two copies cost 717 bytes per physical fix; 12% of
Smart-Truck's rows repeat a fix for another consignment on the same truck, and
across all 562,808 repeats only the per-trip cumulative distance differs.

Nothing is dropped: a second fix in the same second gets `i_seq` 1, 2, …;
the pre-filter only sets `c_flag`; a source that delivers filtered positions
(`gps_kind: filtered`) lands them with `c_source='F'`, replacing or merging
with raw fixes of the same window per `fleet.gps.filtered_policy`.

What differs between consignment copies of one fix is kept per trip in
`trip_fix_override`: the per-fix distance (the source restarts it at each
trip's first fix) and, where the source's cumulative-distance counter
restarted mid-trip, its cumulative figure (otherwise derived as the running
sum, which is what the source sends). Waypoint names are stored byte for byte
— the feed spells some both `Ramgarh` and `RAMGARH` — and compared
case-insensitively, as the feed's own columns are. With these, every one of
the 7,257 imported trips rebuilds row for row and value for value
(`verify_trip_copies`).

### Compatibility views

The analysis code of both legacy apps (~40k lines) reads tables by their
legacy names (`tta_trips`, `tta_trip_gps`, `geo_gps_ping`, …). Each owner
publishes `v1_*` views; each consumer's `R__*.sql` migration creates local
alias views with the legacy names over them, so that code runs unchanged on
the normalised storage. The legacy writers are replaced by the new pipeline
(ingestion → fleet processor), never pointed at a view.

## Events

| Event | From | Consumed by |
|---|---|---|
| `ingest.batch.landed` | ingestion (sync lanes, uploads, file ingest) | fleet processor |
| `fleet.trips.changed` | fleet processor | analytics kpi (historic store + summaries for those trips, caches) |
| `fleet.fixes.stored` | fleet processor | geofence detector (requests a refresh pass) |
| `geo.visits.changed` | geofence detector (each refresh pass) | routing worker (re-judges those journeys), analytics caches |
| `route.analysed` | routing worker | analytics caches |
| `platform.config.changed` | platform (tenant setting saved or reset) | none yet: services re-read tenant settings within 5 s |

Every consumer is idempotent and keeps its own offset in `nx_events`
(at-least-once delivery; an id gap is waited on for 60 s before it is passed).
Reserved for the modules that will publish them: `ml.predictions.ready` (the
analytics caches consumer already listens), `geo.fences.changed`,
`fleet.dimensions.changed`.

## Developer page

`/developer` in the web app, backed by `/api/v1/dev/*` in the supervisor (so
it works with every service stopped): each service and role with state,
health, pid, memory/CPU, uptime and restarts; start / stop / restart per
service and per role; jobs with last runs and "run now"; event consumers with
lag, errors, retry and skip; logs; schemas, table sizes and migrations; the
effective configuration with secrets masked.

## Web app

One React app (`web/`), served by the gateway from `web/dist`.

* `src/core/` — the shell: one sidebar organised by function (`nav.ts`),
  section tabs, the trip workspace, the developer page, theme and type scale.
* `src/modules/analytics/` — the fleet-analytics pages (dashboard, trips,
  analytics, transporters, ML, partners, network, data sync, manual), ported
  from Smart-Truck at their original paths.
* `src/modules/geofence/` — the fence pages (day by day, live map, fences,
  alerts, stops, fence timelines, routes against plans, uploads, data
  quality, method), ported from Geo-Fencing under `/geo/...`.

A section owns every view of its subject from both modules, as tabs (Vehicles:
the fleet view and the fence-activity view). A trip's three views — summary,
journey analysis, fences / phases / route — are tabs of one trip workspace,
and the trips opened in a browser tab stay open beside each other.

Every map draws India from the official state geometry
(`/api/v1/geo/map/states`); no tile layer anywhere. Body text is 14 px and the
smallest text 13 px (`core/index.css`); chart text is 12 px at least. Charts
take their colours from the theme (`PALETTE`, or `tc(hex)` for code written
against Tailwind's hex values), so `teal` and `classic` both apply everywhere;
switching theme reloads the page.
