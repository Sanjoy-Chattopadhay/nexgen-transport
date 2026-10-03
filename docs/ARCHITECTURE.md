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
| fleet | dimensions, trips, provider metrics, `gps_fix`, trails, stops, gaps, speed events, consignment groups (`nx_fleet`) | api, processor | ingestion's landing views |
| geofence | sites, fences, runs, crossings, visits, violations, physical ledger, phases, rollups, live state; Smart-Truck's circle fences and origin-exit results (`nx_geo`) | api, detector, live | fleet contract views |
| routing | plans, trip routes, deviations, cost, weather cache (`nx_route`) | api, worker | fleet + geofence contracts |
| analytics | KPI marts, every Smart-Truck analysis table, family-A compatibility marts, summaries (`nx_analytics`) | api, kpi | every owner's contract views |
| ml | models, predictions (`nx_ml`) | api, trainer | analytics contract views |

### Routing the legacy URLs

Every Smart-Truck URL keeps working: the gateway routes by the most specific
path pattern (services.yaml `routes`), so `/api/v1/tta/trips/7/weather` goes to
routing while `/api/v1/tta/trips/7` goes to analytics. Geo-Fencing's API moved
under `/api/v1/geo/*` (its paths collided with Smart-Truck's `/trips`,
`/vehicles`, `/drivers`, `/transporters`, `/routes`).

## Data

Three layers:

* **Raw** (ingestion): every source record as received — landing rows for
  processing (7 days after use) and compressed payloads (90 days) for replay.
* **Clean** (fleet, geofence, routing): normalised, deduplicated, fitted.
* **KPI** (analytics): precomputed figures the pages read, kept current by the
  kpi worker from events, proven nightly against a full recompute.

### GPS

One row per physical fix, keyed `(i_tenant_id, i_vehicle_id, dt_fix, i_seq)`,
partitioned by month, names replaced by ids (waypoint and status
dictionaries), coordinates as degrees × 10⁷ in 4-byte ints. Measured on
3 October 2026: today's two copies cost 717 bytes per physical fix; 12% of
Smart-Truck's rows repeat a fix for another consignment on the same truck, and
across all 562,808 repeats only the per-trip cumulative distance differs.

Nothing is dropped: a second fix in the same second gets `i_seq` 1, 2, …;
the pre-filter only sets `c_flag`; a source that delivers filtered positions
(`gps_kind: filtered`) lands them with `c_source='F'`, replacing or merging
with raw fixes of the same window per `fleet.gps.filtered_policy`.

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
| `ingest.batch.landed` | ingestion | fleet processor |
| `ingest.master.landed` | ingestion | geofence, fleet |
| `fleet.fixes.stored` | fleet | geofence live |
| `fleet.trips.changed` | fleet | analytics, routing, ml |
| `fleet.trails.ready` | fleet | geofence detector, routing, analytics |
| `fleet.dimensions.changed` | fleet | geofence, analytics, ml |
| `geo.fences.changed` | geofence | geofence detector, analytics |
| `geo.visits.changed` | geofence | analytics, routing |
| `geo.live.event` | geofence live | web live stream |
| `route.analysed` | routing | analytics |
| `ml.predictions.ready` | ml | analytics |
| `analytics.kpis.published` | analytics | caches, web |
| `platform.config.changed` | platform | every service |

## Developer page

`/developer` in the web app, backed by `/api/v1/dev/*` in the supervisor (so
it works with every service stopped): each service and role with state,
health, pid, memory/CPU, uptime and restarts; start / stop / restart per
service and per role; jobs with last runs and "run now"; event consumers with
lag, errors, retry and skip; logs; schemas, table sizes and migrations; the
effective configuration with secrets masked.
