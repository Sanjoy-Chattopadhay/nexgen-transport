"""Land received data in the raw layer and announce it.

Every path data enters by -- a TMS lane run, a backfill chunk, an uploaded
TTA export -- ends here. One call writes one batch: a `batch` row, one
`payload` row per trip record (the record and its GPS exactly as received,
zlib-compressed JSON) and the `ingest.batch.landed` event, all in one
transaction. Either the data and its announcement both exist or neither does.

Nothing is filtered or deduplicated here. Smart-Truck's loader did both inline
(INSERT IGNORE on a per-consignment key); NexGen keeps the raw record whole
and leaves normalising to the fleet processor, so a change to how records are
normalised can be replayed over the last 90 days of raw data.

`gps_kind` marks what the source delivers: 'R' raw device fixes, or 'F'
positions the source has already filtered (map-matched). Both land the same
way; the processor files 'F' fixes beside or in place of raw ones per
`fleet.gps.filtered_policy`.
"""

from __future__ import annotations

import json
import logging
import zlib
from datetime import date, datetime

from nexgen.core.events import publish
from nexgen.core.tenancy import current_tenant_id

logger = logging.getLogger(__name__)

COMPRESSION_LEVEL = 6


def _default(o):
    if isinstance(o, datetime):
        return o.isoformat(sep=" ")
    if isinstance(o, date):
        return o.isoformat()
    return str(o)


def encode(obj) -> bytes:
    return zlib.compress(json.dumps(obj, default=_default, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8"), COMPRESSION_LEVEL)


def decode(blob: bytes):
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def land_blocks(conn, blocks: list[dict], *, source: str, trip_class: str = "zonal",
                gps_kind: str = "R", default_cnr_id: int | None = None, trigger: str | None = None,
                window: tuple | None = None, sync_run_id: int | None = None,
                note: str | None = None, tenant_id: int | None = None) -> dict:
    """Write one batch of {vehicle, trip, gps[, gps_from, gps_to, gps_failed]} blocks.

    Returns Smart-Truck's ingest summary shape so the ported lane code and the
    ETL screen keep working; the counts are what was landed.
    """
    tid = int(tenant_id or current_tenant_id())
    frm, to = (window or (None, None))
    total_fixes = sum(len(b.get("gps") or []) for b in blocks)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO batch (i_tenant_id, s_source, s_trip_class, c_gps_kind, i_default_cnr_id, "
            "i_sync_run_id, s_trigger, dt_window_from, dt_window_to, i_trips, i_fixes, i_parts, s_note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tid, source, trip_class, gps_kind, default_cnr_id, sync_run_id, trigger, frm, to,
             len(blocks), total_fixes, len(blocks), note))
        batch_id = cur.lastrowid
        rows = []
        trip_nos = []
        for i, b in enumerate(blocks):
            trip = b.get("trip") or {}
            trip_no = trip.get("trip_no") or trip.get("i_trip_no")
            try:
                trip_no = int(float(trip_no)) if trip_no not in (None, "") else None
            except (TypeError, ValueError):
                trip_no = None
            if trip_no is not None:
                trip_nos.append(trip_no)
            gps = b.get("gps") or []
            rows.append((batch_id, i, trip_no, (b.get("vehicle") or None),
                         b.get("gps_from"), b.get("gps_to"), len(gps), int(bool(b.get("gps_failed"))),
                         encode({"vehicle": b.get("vehicle"), "trip": trip, "gps": gps,
                                 "gps_from": b.get("gps_from"), "gps_to": b.get("gps_to")})))
        for i in range(0, len(rows), 200):
            cur.executemany(
                "INSERT INTO payload (i_batch_id, i_part, i_trip_no, s_vehicle, dt_gps_from, dt_gps_to, "
                "i_fixes, b_gps_failed, b_body) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows[i:i + 200])
        publish(conn, "ingest.batch.landed",
                {"batch_id": batch_id, "source": source, "trip_class": trip_class,
                 "gps_kind": gps_kind, "trips": len(blocks), "fixes": total_fixes,
                 "trip_nos": trip_nos[:5000]},
                tenant_id=tid, source="ingestion")
    conn.commit()
    logger.info("landed batch %s from %s: %d trip(s), %d fix(es)", batch_id, source, len(blocks), total_fixes)
    return {"status": "ok", "batch_id": batch_id, "trips_upserted": len(blocks),
            "gps_inserted": total_fixes, "gps_skipped": 0, "legacy_trips_synced": 0, "errors": []}
