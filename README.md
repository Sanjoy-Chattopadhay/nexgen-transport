# NexGen Transport

Fleet operations, geofencing, routes and analytics for Tata Steel's fleet, as
one product. Built from Smart-Truck and the Geo-Fencing module, which keep
running untouched beside it (their databases are only ever read).

## Start it (Windows)

```bat
run.bat
```

Then open **http://127.0.0.1:8100**.

The first run creates `.venv`, installs the Python and web dependencies and
builds the web app; later runs start in seconds. `run.bat /build` rebuilds the
web app after a change under `web\`, and `run.bat /setup` reinstalls
everything.

Stop it with **Ctrl+C** in that window (answer either way to Windows' "Terminate
batch job?") or with `stop.bat` from another window. Only NexGen's own
processes stop; the legacy apps on ports 8000, 8001 and 8090 are not touched.

### First time only

1. Create `.env` in this folder with your database login (and the TMS keys if
   NexGen should pull from the upstream API). The names are in
   [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
2. Copy the existing data in, once: `.venv\Scripts\python -m nexgen import-legacy`.
   It reads `smart_truck` and `geofencing` through read-only sessions.

## Start it (any OS)

You need Python 3.11+, Node 20+ and MySQL 8 (the server the legacy apps use).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
npm --prefix web install
npm --prefix web run build
.venv/bin/python -m nexgen run                 # http://127.0.0.1:8100
```

`python -m nexgen shutdown` stops it from another terminal.

## What is where

| Sidebar | What you get |
|---|---|
| Dashboard | the fleet dashboard, and the fleet day by day at its fences |
| Live map | where every truck is now, replayed or followed live |
| Analytics · Transporters · Compare · ML insights | the analysis reports, carrier league and best-per-lane, benchmarking, the nine models |
| Trips · Routes & lanes · Vehicles · Drivers · Partners | each with every view of it as tabs; a trip opens in a workspace with its summary, journey analysis and fence timeline side by side |
| Geofences · Alerts · Stops & hotspots · GPS network | fence master and activity, detention and delivery proof, states and tolls, alerts, stops, ping density, waypoints |
| Data sync · Data quality · Migration · How it works | the TMS lanes, how far the GPS can be trusted, uploads, and how every figure is computed |
| **Developer** | every service's health; start, stop and restart each service and each of its workers; jobs (run now), event consumers (retry, skip), logs, database and migrations, the effective configuration |

The consignor and zonal/local filters at the top of the sidebar apply to every
page that can honour them; a page that reads the whole fleet says so.

## Developing

```bash
npm --prefix web run dev       # http://127.0.0.1:5200, proxies /api to :8100
python -m pytest -q            # integration tests skip without MySQL
python -m nexgen status        # what is running
python -m nexgen migrate --status
```

## Documentation

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | services, data, events, the web app, the decisions behind them |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every setting, the two config files, tenants, secrets |
| [CLAUDE.md](CLAUDE.md) | invariants, commands and layout |
