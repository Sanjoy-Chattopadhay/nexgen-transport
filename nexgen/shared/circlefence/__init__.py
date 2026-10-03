"""Geofence module.

Self-contained: nothing outside this package needs to know how fences are
indexed or how crossings are debounced. The public surface is

    build_index()            -> a query-ready GeofenceIndex
    seed_from_anchors()      -> populate fence definitions from GPS anchors
    ensure_schema()          -> create the tables/columns (idempotent)
    backfill.run()           -> stamp dt_geofence_out + the crossing ledger
    detention.*              -> works-detention metrics built on the stamp
    gps_quality.*            -> the GPS-coverage reports

See `index.py` for the indexing scheme and `detection.py` for the
jitter-handling rules.
"""

from .index import Geofence, GeofenceIndex, haversine_m, normalise_key
from .detection import Ping, Crossing, ExitResult, crossings, first_sustained_exit
from .store import build_index, ensure_schema, load_fences, seed_from_anchors

__all__ = [
    "Geofence", "GeofenceIndex", "haversine_m", "normalise_key",
    "Ping", "Crossing", "ExitResult", "crossings", "first_sustained_exit",
    "build_index", "ensure_schema", "load_fences", "seed_from_anchors",
]
