"""Storage format for a fitted trail: one compressed row per trip.

The fitted position of every ping is kept -- that is what makes a visit
auditable -- but not as one database row per ping. Written that way a full run
is five million rows into a clustered index, and on a MySQL server with stock
buffer settings the inserts throttle the whole database: measured on this
corpus, the run slowed to ~25 trips a minute with the CPU 70% idle, because
InnoDB was flushing at its default 200 pages a second and every read queued
behind it.

Every read of a fitted trail is "this trip, in order", so the natural unit is
the trip. A trail is a fixed-width numpy record array, zlib-compressed:
~51 bytes a ping before compression, and the receiver's position hold (80% of
consecutive standstill fixes repeat exactly) compresses well. A run becomes a
few thousand small blobs written sequentially.
"""

from __future__ import annotations

import calendar
import zlib
from datetime import datetime, timezone

import numpy as np

CODEC = "np-z1"

DTYPE = np.dtype([
    ("id", "<i8"), ("t", "<i8"),
    ("lat", "<f8"), ("lon", "<f8"),
    ("shift", "<f4"), ("snap", "<f4"), ("conf", "<f4"),
    ("stop", "<i4"), ("role", "u1"), ("fit", "u1"), ("reject", "u1"),
])

ROLES = ("", "still", "move", "reject")
FITS = ("", "raw", "median", "spike", "snapped")
REJECTS = ("", "duplicate", "teleport", "out_of_range", "null_fix", "out_of_order")


def _code(table: tuple, value) -> int:
    try:
        return table.index(value or "")
    except ValueError:
        return 0


def _epoch(ts: datetime) -> int:
    # Naive local time stored as if UTC: it round-trips exactly, and no
    # consumer of the trail needs a zone.
    return calendar.timegm(ts.timetuple())


def encode(ping_rows: list[tuple]) -> bytes:
    """`FitResult.ping_rows()` tuples -> compressed bytes.

    Row shape: (id, trip, ts, role, reject, stop, lat, lon, fit, shift, snap, conf).
    """
    arr = np.zeros(len(ping_rows), dtype=DTYPE)
    for i, r in enumerate(ping_rows):
        arr[i] = (
            r[0], _epoch(r[2]),
            np.nan if r[6] is None else r[6], np.nan if r[7] is None else r[7],
            np.nan if r[9] is None else r[9], np.nan if r[10] is None else r[10],
            np.nan if r[11] is None else r[11],
            -1 if r[5] is None else r[5],
            _code(ROLES, r[3]), _code(FITS, r[8]), _code(REJECTS, r[4]),
        )
    arr.sort(order=("t", "id"))
    return zlib.compress(arr.tobytes(), 6)


def decode(blob: bytes) -> np.ndarray:
    return np.frombuffer(zlib.decompress(blob), dtype=DTYPE)


def records(blob: bytes) -> list[dict]:
    """Decoded trail as plain dicts, in time order."""
    out = []
    for r in decode(blob):
        out.append({
            "id": int(r["id"]),
            "ts": datetime.fromtimestamp(int(r["t"]), tz=timezone.utc).replace(tzinfo=None),
            "lat": None if np.isnan(r["lat"]) else float(r["lat"]),
            "lon": None if np.isnan(r["lon"]) else float(r["lon"]),
            "shift_m": None if np.isnan(r["shift"]) else float(r["shift"]),
            "snap_m": None if np.isnan(r["snap"]) else float(r["snap"]),
            "conf": None if np.isnan(r["conf"]) else float(r["conf"]),
            "stop": None if r["stop"] < 0 else int(r["stop"]),
            "role": ROLES[r["role"]] or None,
            "fit": FITS[r["fit"]] or None,
            "reject": REJECTS[r["reject"]] or None,
        })
    return out
