# NexGen Transport

Fleet operations, geofencing, routes and analytics for Tata Steel's fleet, as
one product. Built from Smart-Truck and the Geo-Fencing module, which keep
running untouched beside it until the switch-over.

## Run it

You need Python 3.11+, Node 20+ and a MySQL 8 server (the same one the legacy
apps use).

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
npm --prefix web install
npm --prefix web run build
```

Create `.env` with your database login (and TMS keys if you will pull from the
upstream) — the names are listed in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
Then:

```bash
python -m nexgen run              # http://127.0.0.1:8100
python -m nexgen import-legacy    # first time: copy smart_truck + geofencing in (read-only on them)
```

On Windows, `run.bat` does the build-if-missing and start in one step.

The **Developer** page (`/developer`) shows every module's health and lets
you start, stop and restart each one.

## Documentation

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | services, data, events, the decisions behind them |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every setting, the two config files, tenants, secrets |
| [CLAUDE.md](CLAUDE.md) | invariants, commands and layout |
