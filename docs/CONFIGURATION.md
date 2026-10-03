# Configuration

Everything NexGen Transport can be told lives in four places, each for one
kind of thing:

| File | Holds | In git |
|---|---|---|
| `config/services.yaml` | every service and role, ports, routes, schedules, external APIs, engine settings | yes |
| `config/database.yaml` | every database connection, schema names, legacy sources | yes |
| `config/tenants/*.yaml` | per-client business settings over `_default.yaml` | yes |
| `.env` | secrets and machine-specific values only | **never** |

The YAML files reference `.env` values as `${NAME}` or `${NAME:-default}`.
A value that is exactly one placeholder is typed after substitution
(`"${DB_PORT:-3306}"` is the number 3306).

## .env

No `.env*` file is ever committed (Smart-Truck's history shows why). Create
`.env` in the repository root with the names you need:

| Name | Needed for | Default |
|---|---|---|
| `DB_HOST`, `DB_PORT` | the MySQL server | `localhost`, `3306` |
| `DB_USER`, `DB_PASSWORD` | the MySQL login used by every service in development | `root`, empty |
| `TMS_API_BASE_URL`, `TMS_USERNAME`, `TMS_AUTH_KEY` | pulling trips and GPS from eTrans | empty: lanes stay idle |
| `TMS_AUTH_URL`, `TMS_ENTITY_IDS`, other `TMS_*` | upstream details (see services.yaml `integrations.tms`) | Smart-Truck's defaults |
| `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_*` | narratives on trip analysis | empty: rule-based text |
| `ML_API_AUTH_ENABLED`, `ML_API_KEYS` | subscription keys on the ML API for outside callers | off |
| `OSRM_URL`, `OSRM_PROFILE` | road snapping and OSRM routing | empty: off |
| `GPS_RETENTION_DAYS` | days of GPS kept online | 180 |
| `MYSQL_BIN_DIR` | where `mysqldump` lives, for backups | MySQL 8.0's default on Windows |

The first setup copied the database login and the TMS and Azure keys from
`smart-truck/.env`.

## The TMS lanes start switched off

`integrations.tms.lanes.*.enabled` is `false` in services.yaml. While the
legacy Smart-Truck app may still be pulling the same feed, two pullers would
double the load on the upstream. Switch a lane on from the Ingestion page
when NexGen takes over.

## Client settings (tenants)

`config/tenants/_default.yaml` holds what every client starts with: modules,
branding, feature flags, thresholds, cost rates, labels. A client's file
(`tata-steel.yaml`) overrides any of it, and changes made on the Admin page
override the file. Every change is versioned and audited in `nx_platform`,
reaches every service within seconds, and never touches another client.

Engine settings that decide accuracy (GPS fit, geofence detector) stay
system-wide in services.yaml, as measured on the 5.1M-fix corpus.

## Production database users

In development one MySQL user serves every schema. In production give each
service its own user with rights on its own schema only, plus `SELECT` on the
`v1_*` views it consumes, and list them under `credentials` in
database.yaml:

```yaml
credentials:
  default: { user: "${DB_USER}", password: "${DB_PASSWORD}" }
  fleet:   { user: nx_fleet,     password: "${DB_PASS_FLEET}" }
```
