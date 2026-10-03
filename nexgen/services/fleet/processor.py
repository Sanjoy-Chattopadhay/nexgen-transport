"""The fleet processor: raw batches -> normalised trips, entities and GPS.

This is the data-preprocessing stage. It consumes `ingest.batch.landed`,
reads the batch's payloads through ingestion's published views, and writes:

  * entities (consignor, vehicle, device, transporter, consignee, places,
    driver) keyed on the exact values the source sent;
  * the trip, the upstream's own figures for it (trip_provider_metric) and its
    full source record (trip_source_record);
  * every GPS fix once per vehicle in gps_fix, and the trip's window onto its
    truck's fixes (trip_gps_window).

Records are mapped with Smart-Truck's own parsers (nexgen.shared.feed.tta),
so every value is exactly what Smart-Truck stored.

GPS rules (decided 2026-10-03: accept and process everything):
  * A fix already stored for the truck at that second with the same reading
    is the same physical fix arriving again for another consignment: it is
    not stored twice. If this consignment's copy carries a different per-fix
    distance (the source restarts it at a trip's first fix), that one value is
    kept as a per-trip override, so the trip's copy reproduces exactly.
  * A *different* reading in an occupied second is a second fix: it is stored
    with i_seq 1, 2, ... Nothing is dropped.
  * A batch whose source filters (map-matches) its GPS lands its fixes with
    c_source='F'. Under `fleet.gps.filtered_policy: replace` they replace the
    raw fixes of the same truck over the same span; under `merge` they sit
    beside them.
  * A ping with no time or no position cannot be placed and is counted as
    unusable; it stays in the raw payload.
  * A trip's cumulative distance is derived as the running sum of its per-fix
    distances, which is what the source sends -- until its counter restarts
    mid-trip. Where the source's figure differs, it is kept per fix
    (trip_fix_override.i_cdist_m), so the trip reads exactly what was sent.

Every batch ends with `fleet.trips.changed` and `fleet.fixes.stored`, written in
the same transaction as the processed_batch row, so geofence, routing and
analytics learn exactly which trips and which spans of which trucks changed.
Re-processing a batch is safe: every write is an upsert or a dedupe.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.events import Event, publish
from nexgen.services.fleet import dims
from nexgen.services.ingestion.landing import decode
from nexgen.shared.feed.tta import map_gps_row, map_metrics_row, map_trip_row, parse_dt

logger = logging.getLogger(__name__)

Q8 = Decimal("0.00000001")
PART_CHUNK = 50

TRIP_UPDATE_COLS = [
    "s_trip_class", "i_cnr_id", "s_cnr_name", "i_vehicle_id", "i_device_id", "s_asset_type", "c_trip_type",
    "s_trip_type_desc", "i_origin_id", "i_dest_id", "i_final_dest_id", "i_load_plant_id", "dt_booking",
    "dt_trip_start", "dt_trip_eta", "dt_trip_ata", "dt_trip_end", "i_transporter_id", "i_consignee_id",
    "c_trip_status", "s_close_reason", "s_invoice", "s_card_id", "s_shipment_id", "s_event_code",
    "s_gate_entry_no", "i_driver_id", "i_route_id", "s_created_by", "s_modified_by", "i_batch_id",
]
METRIC_COLS = [
    "i_sl_no", "s_tag", "s_store_entry_no", "dt_ata_out", "dt_delivery", "s_delivery_status", "s_delivery_dur",
    "i_delivery_delta_min", "s_transit_time", "i_transit_time_min", "s_detention", "i_detention_min",
    "s_total_moving_time", "i_moving_time_min", "s_total_stoppage_time", "i_stoppage_time_min",
    "s_plant_vivo", "i_plant_vivo_min", "d_distance_travelled_km", "i_speed_violation", "d_uptime_pct",
    "s_service_provider", "s_supplier_name", "s_cne_contact_no", "i_cne_pin", "s_ship_to_address",
    "i_geo_id", "d_inv_qty", "s_material_desc", "s_asset_make", "s_asset_model", "s_close_remarks",
    "s_fo_no", "i_trip_seq", "s_tta_ex_nd", "s_det_ex_nd",
]
FIX_COLS = ["i_tenant_id", "i_vehicle_id", "dt_fix", "i_seq", "d_lat", "d_lon", "i_speed", "i_status_id",
            "i_wp1_id", "i_wp1_m", "i_wp2_id", "i_wp2_m", "i_dist_m", "i_device_id", "i_entity_id",
            "c_uom", "c_source", "i_batch_id"]
# Positions in FIX_COLS that identify a physical reading. i_dist_m (12) is
# left out: it is per consignment (the source restarts it at a trip's start).
_READING = (4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15)


def _reading(v: list) -> tuple:
    return tuple(v[i] for i in _READING)


def _q8(value) -> Decimal:
    return Decimal(repr(value) if isinstance(value, float) else str(value)).quantize(Q8, ROUND_HALF_UP)


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return parse_dt(value)


def _filtered_policy() -> str:
    return str(get_config().setting("fleet", "gps.filtered_policy", "replace")).lower()


# ---------------------------------------------------------------------------
# one trip record
# ---------------------------------------------------------------------------
def _apply_trip(cur, tenant: int, batch: dict, trip_raw: dict, stats: Counter) -> dict | None:
    row = map_trip_row(trip_raw, trip_class=batch["s_trip_class"] or "zonal")
    trip_no = row["i_trip_no"]
    if trip_no is None:
        stats["records_without_trip_no"] += 1
        return None
    if row["i_cnr_id"] is None:
        row["i_cnr_id"] = dims.consignor_by_name(cur, tenant, row["s_cnr_name"]) or batch.get("i_default_cnr_id")
    if row["i_cnr_id"] is not None:
        dims.consignor(cur, tenant, row["i_cnr_id"], row["s_cnr_name"])
    rec = {
        "s_trip_class": row["s_trip_class"],
        "i_cnr_id": row["i_cnr_id"],
        "s_cnr_name": row["s_cnr_name"],
        "i_vehicle_id": dims.vehicle(cur, tenant, row["s_asset_id"]),
        "i_device_id": dims.device(cur, tenant, row["s_device_id"]),
        "s_asset_type": row["s_asset_type"],
        "c_trip_type": row["c_trip_type"],
        "s_trip_type_desc": row["s_trip_type_desc"],
        "i_origin_id": dims.location(cur, tenant, row["i_org_node_no"], row["s_org_node_name"]),
        "i_dest_id": dims.location(cur, tenant, row["i_dest_node_no"], row["s_dest_node_name"]),
        "i_final_dest_id": dims.location(cur, tenant, None, row["s_final_dest"]),
        "i_load_plant_id": dims.location(cur, tenant, None, row["s_load_plant"]),
        "dt_booking": row["dt_booking"],
        "dt_trip_start": row["dt_trip_start"],
        "dt_trip_eta": row["dt_trip_eta"],
        "dt_trip_ata": row["dt_trip_ata"],
        "dt_trip_end": row["dt_trip_end"],
        "i_transporter_id": dims.transporter(cur, tenant, row["i_trans_id"], row["s_trans_name"]),
        "i_consignee_id": dims.consignee(cur, tenant, row["s_cne_name"]),
        "c_trip_status": row["c_trip_status"],
        "s_close_reason": row["s_close_reason"],
        "s_invoice": row["s_invoice"],
        "s_card_id": row["s_card_id"],
        "s_shipment_id": row["s_shipment_id"],
        "s_event_code": row["s_event_code"],
        "s_gate_entry_no": row["s_gate_entry_no"],
        "i_driver_id": dims.driver(cur, tenant, row["s_driver_name"], row["s_driver_mobile_no"]),
        "i_route_id": row["i_route_id"],
        "s_created_by": row["s_created_by"],
        "s_modified_by": row["s_modified_by"],
        "i_batch_id": batch["i_batch_id"],
    }
    cols = ["i_tenant_id", "i_trip_no"] + TRIP_UPDATE_COLS
    cur.execute(
        f"INSERT INTO trip ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(f'{c}=VALUES({c})' for c in TRIP_UPDATE_COLS)}",
        [tenant, trip_no] + [rec[c] for c in TRIP_UPDATE_COLS])

    metrics = map_metrics_row(trip_raw, trip_no)
    mcols = ["i_tenant_id", "i_trip_no"] + METRIC_COLS
    cur.execute(
        f"INSERT INTO trip_provider_metric ({', '.join(mcols)}) VALUES ({', '.join(['%s'] * len(mcols))}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(f'{c}=VALUES({c})' for c in METRIC_COLS)}",
        [tenant, trip_no] + [metrics.get(c) for c in METRIC_COLS])
    cur.execute(
        "INSERT INTO trip_source_record (i_tenant_id, i_trip_no, j_record, i_batch_id) VALUES (%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE j_record=VALUES(j_record), i_batch_id=VALUES(i_batch_id), dt_received=NOW()",
        (tenant, trip_no, metrics["raw_json"], batch["i_batch_id"]))
    stats["trips"] += 1
    return {"trip_no": trip_no, "vehicle_id": rec["i_vehicle_id"], "asset": row["s_asset_id"]}


# ---------------------------------------------------------------------------
# GPS for one trip record
# ---------------------------------------------------------------------------
def _fix_values(cur, tenant: int, vehicle_id: int, r: dict, batch: dict, source: str) -> list:
    status_id = dims.status(cur, r["s_status"], r["is_moving"], r["i_status_speed_kmph"])
    dims.entity(cur, tenant, r["i_entity_id"], r["s_entity_name"])
    return [tenant, vehicle_id, r["dt_message"], 0, _q8(r["d_lat"]), _q8(r["d_long"]), r["i_speed"],
            status_id, dims.waypoint(cur, r["s_wpnt1"], r["s_wpnt1_st_abbr"]), r["i_wpnt1_mt"],
            dims.waypoint(cur, r["s_wpnt2"], r["s_wpnt2_st_abbr"]), r["i_wpnt2_mt"], r["i_dist"],
            dims.device(cur, tenant, r["s_device_id"]), r["i_entity_id"], r["s_uom"], source,
            batch["i_batch_id"]]


def _store_fixes(cur, tenant: int, vehicle_id: int, items: list[tuple[list, int | None]], trip_no: int,
                 source: str, stats: Counter) -> tuple[int, datetime | None, datetime | None, list]:
    """Insert the fixes not already stored for the truck.

    `items` are (gps_fix row, the source's cumulative distance or None).
    Returns (new, lo, hi, placed): placed is (dt_fix, i_seq, source cdist) for
    every incoming fix, new or already stored, for _keep_source_cdist.
    """
    if not items:
        return 0, None, None, []
    items.sort(key=lambda it: it[0][2])
    lo, hi = items[0][0][2], items[-1][0][2]
    if source == "F" and _filtered_policy() == "replace":
        cur.execute("DELETE FROM gps_fix WHERE i_tenant_id=%s AND i_vehicle_id=%s AND dt_fix BETWEEN %s AND %s "
                    "AND c_source='R'", (tenant, vehicle_id, lo, hi))
        stats["raw_fixes_replaced"] += cur.rowcount
    cur.execute(f"SELECT {', '.join(FIX_COLS)} FROM gps_fix WHERE i_tenant_id=%s AND i_vehicle_id=%s "
                "AND dt_fix BETWEEN %s AND %s", (tenant, vehicle_id, lo, hi))
    by_second: dict[datetime, list[list]] = defaultdict(list)
    for e in cur.fetchall():
        by_second[e["dt_fix"]].append([e[c] for c in FIX_COLS])
    new_rows, overrides, placed = [], [], []
    for v, cdist in items:
        same = None
        for existing in by_second[v[2]]:
            if _reading(existing) == _reading(v):
                same = existing
                break
        if same is not None:
            stats["fixes_duplicate"] += 1
            if same[12] != v[12]:
                overrides.append((tenant, trip_no, v[2], same[3], v[12]))
            placed.append((v[2], same[3], cdist))
            continue
        v[3] = max((x[3] for x in by_second[v[2]]), default=-1) + 1
        if v[3] > 0:
            stats["fixes_same_second"] += 1
        by_second[v[2]].append(v)
        new_rows.append(v)
        placed.append((v[2], v[3], cdist))
    if new_rows:
        sql = (f"INSERT INTO gps_fix ({', '.join(FIX_COLS)}) VALUES ({', '.join(['%s'] * len(FIX_COLS))})")
        for i in range(0, len(new_rows), 2000):
            cur.executemany(sql, new_rows[i:i + 2000])
    if overrides:
        cur.executemany("INSERT INTO trip_fix_override (i_tenant_id, i_trip_no, dt_fix, i_seq, i_dist_m) "
                        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE i_dist_m=VALUES(i_dist_m)", overrides)
        stats["trip_overrides"] += len(overrides)
    stats["fixes_new"] += len(new_rows)
    if new_rows:
        cur.execute("UPDATE vehicle SET i_fixes=i_fixes+%s, dt_first_fix=LEAST(COALESCE(dt_first_fix,%s),%s), "
                    "dt_last_fix=GREATEST(COALESCE(dt_last_fix,%s),%s) WHERE i_vehicle_id=%s",
                    (len(new_rows), lo, lo, hi, hi, vehicle_id))
    return len(new_rows), lo, hi, placed


def _keep_source_cdist(cur, tenant: int, trip_no: int, placed: list) -> int:
    """Keep the source's cumulative distance where it is not the derived one.

    The trip's copy is read back through the contract view (this
    transaction's own writes included) and its running sum of per-fix
    distances compared, fix by fix, with what the source sent for the fixes
    in this block. A disagreement is stored; an agreement clears an earlier
    exception, so a re-sent, corrected block heals the trip. Returns the
    number of exceptions written.
    """
    src = {(dt, seq): int(c) for dt, seq, c in placed if c is not None}
    if not src:
        return 0
    # Exceptions already held, so agreeing fixes clear only those (one
    # statement per exception, not one per fix of the block).
    cur.execute("SELECT dt_fix, i_seq FROM trip_fix_override WHERE i_tenant_id=%s AND i_trip_no=%s "
                "AND i_cdist_m IS NOT NULL", (tenant, trip_no))
    held = {(r["dt_fix"], r["i_seq"]) for r in cur.fetchall()}
    cur.execute("SELECT dt_message, i_seq, i_dist FROM v1_tta_trip_gps WHERE i_tenant_id=%s AND i_trip_no=%s "
                "ORDER BY dt_message, i_seq", (tenant, trip_no))
    running, keep, agree = 0, [], []
    for r in cur.fetchall():
        running += int(r["i_dist"] or 0)
        key = (r["dt_message"], r["i_seq"])
        if key in src:
            row = (tenant, trip_no, key[0], key[1])
            if src[key] != running:
                keep.append(row + (src[key],))
            elif key in held:
                agree.append(row)
    if keep:
        cur.executemany("INSERT INTO trip_fix_override (i_tenant_id, i_trip_no, dt_fix, i_seq, i_cdist_m) "
                        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE i_cdist_m=VALUES(i_cdist_m)", keep)
    if agree:
        cur.executemany("UPDATE trip_fix_override SET i_cdist_m=NULL WHERE i_tenant_id=%s AND i_trip_no=%s "
                        "AND dt_fix=%s AND i_seq=%s", agree)
    return len(keep)


def _update_window(cur, tenant: int, trip_no: int, vehicle_id: int, frm, to) -> int:
    cur.execute("INSERT INTO trip_gps_window (i_tenant_id, i_trip_no, i_vehicle_id, dt_from, dt_to) "
                "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE i_vehicle_id=VALUES(i_vehicle_id), "
                "dt_from=LEAST(dt_from, VALUES(dt_from)), dt_to=GREATEST(dt_to, VALUES(dt_to))",
                (tenant, trip_no, vehicle_id, frm, to))
    cur.execute("SELECT w.dt_from, w.dt_to, MIN(f.dt_fix) a, MAX(f.dt_fix) b, COUNT(f.dt_fix) n "
                "FROM trip_gps_window w LEFT JOIN gps_fix f ON f.i_tenant_id=w.i_tenant_id "
                "AND f.i_vehicle_id=w.i_vehicle_id AND f.dt_fix BETWEEN w.dt_from AND w.dt_to "
                "WHERE w.i_tenant_id=%s AND w.i_trip_no=%s GROUP BY w.dt_from, w.dt_to", (tenant, trip_no))
    agg = cur.fetchone() or {}
    cur.execute("UPDATE trip_gps_window SET dt_first_fix=%s, dt_last_fix=%s WHERE i_tenant_id=%s AND i_trip_no=%s",
                (agg.get("a"), agg.get("b"), tenant, trip_no))
    n = int(agg.get("n") or 0)
    cur.execute("UPDATE trip SET i_gps_ping_count=%s WHERE i_tenant_id=%s AND i_trip_no=%s", (n, tenant, trip_no))
    return n


def _apply_block(conn, tenant: int, batch: dict, block: dict, stats: Counter, touched: dict) -> None:
    source = "F" if (batch.get("c_gps_kind") or "R") == "F" else "R"
    with conn.cursor() as cur:
        trip = _apply_trip(cur, tenant, batch, block.get("trip") or {}, stats)
        if trip is None:
            return
        trip_no = trip["trip_no"]
        touched["trips"].add(trip_no)
        pings = block.get("gps") or []
        stats["fixes_in"] += len(pings)
        per_vehicle: dict[int, list[tuple[list, int | None]]] = defaultdict(list)
        for p in pings:
            r = map_gps_row(p, trip_no)
            if r is None:
                stats["fixes_unusable"] += 1
                continue
            asset = r["s_asset_id"] or trip["asset"]
            vid = dims.vehicle(cur, tenant, asset) if asset else trip["vehicle_id"]
            if vid is None:
                stats["fixes_without_vehicle"] += 1
                continue
            if trip["vehicle_id"] and vid != trip["vehicle_id"]:
                stats["fixes_other_vehicle"] += 1
            # The source's own cumulative distance, when it sent one.
            cdist = r["i_cdist"] if p.get("r_cdist") not in (None, "") else None
            per_vehicle[vid].append((_fix_values(cur, tenant, vid, r, batch, source), cdist))
        new_total, lo_all, hi_all = 0, None, None
        placed_by_vehicle: dict[int, list] = {}
        for vid, rows in per_vehicle.items():
            new, lo, hi, placed_by_vehicle[vid] = _store_fixes(cur, tenant, vid, rows, trip_no, source, stats)
            new_total += new
            if lo is not None:
                win = touched["vehicles"].setdefault(vid, [lo, hi, 0])
                win[0], win[1], win[2] = min(win[0], lo), max(win[1], hi), win[2] + new
            if vid == trip["vehicle_id"] or trip["vehicle_id"] is None:
                lo_all = lo if lo_all is None else min(lo_all, lo)
                hi_all = hi if hi_all is None else max(hi_all, hi)
        window_vehicle = trip["vehicle_id"] or (next(iter(per_vehicle)) if per_vehicle else None)
        frm, to = _dt(block.get("gps_from")), _dt(block.get("gps_to"))
        if block.get("gps_failed"):
            frm = to = None   # nothing was fetched; the lane re-pulls this trip
        if frm is None or to is None:
            frm, to = lo_all, hi_all
        elif lo_all is not None:
            frm, to = min(frm, lo_all), max(to, hi_all)
        if window_vehicle is not None and frm is not None and to is not None:
            _update_window(cur, tenant, trip_no, window_vehicle, frm, to)
            stats["trip_cdist_kept"] += _keep_source_cdist(cur, tenant, trip_no,
                                                           placed_by_vehicle.get(window_vehicle, []))
        if new_total:
            cur.execute("INSERT INTO fix_batch_trip (i_batch_id, i_tenant_id, i_trip_no, i_vehicle_id, "
                        "i_new_fixes, dt_min, dt_max) VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                        "i_new_fixes=i_new_fixes+VALUES(i_new_fixes), dt_min=LEAST(dt_min, VALUES(dt_min)), "
                        "dt_max=GREATEST(dt_max, VALUES(dt_max))",
                        (batch["i_batch_id"], tenant, trip_no, window_vehicle, new_total, lo_all, hi_all))
            touched["gps_trips"].add(trip_no)


def process_batch(batch_id: int) -> dict:
    cfg = get_config()
    ingest = cfg.schema("ingestion")
    t0 = time.perf_counter()
    stats: Counter = Counter()
    touched = {"trips": set(), "gps_trips": set(), "vehicles": {}}
    with connect("fleet") as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{ingest}`.v1_batch WHERE i_batch_id=%s", (batch_id,))
            batch = cur.fetchone()
            cur.execute("SELECT s_status FROM processed_batch WHERE i_batch_id=%s", (batch_id,))
            done = cur.fetchone()
        conn.commit()
        if batch is None:
            raise RuntimeError(f"batch {batch_id} is not in the raw layer")
        tenant = int(batch["i_tenant_id"])
        part = -1
        while True:
            with conn.cursor() as cur:
                cur.execute(f"SELECT i_part, b_body FROM `{ingest}`.v1_payload WHERE i_batch_id=%s AND i_part>%s "
                            "ORDER BY i_part LIMIT %s", (batch_id, part, PART_CHUNK))
                rows = cur.fetchall()
            if not rows:
                break
            for r in rows:
                part = r["i_part"]
                block = decode(r["b_body"])
                try:
                    _apply_block(conn, tenant, batch, block, stats, touched)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        secs = time.perf_counter() - t0
        vehicles = [{"vehicle_id": v, "from": w[0], "to": w[1], "new": w[2]} for v, w in touched["vehicles"].items()]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO processed_batch (i_batch_id, i_tenant_id, s_status, i_trips, i_fixes_in, i_fixes_new, "
                "i_fixes_dup, i_fixes_seq, d_seconds) VALUES (%s,%s,'ok',%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE s_status='ok', i_trips=VALUES(i_trips), i_fixes_in=VALUES(i_fixes_in), "
                "i_fixes_new=VALUES(i_fixes_new), i_fixes_dup=VALUES(i_fixes_dup), "
                "i_fixes_seq=VALUES(i_fixes_seq), d_seconds=VALUES(d_seconds), s_error=NULL, dt_processed=NOW()",
                (batch_id, tenant, stats["trips"], stats["fixes_in"], stats["fixes_new"],
                 stats["fixes_duplicate"], stats["fixes_same_second"], secs))
            if touched["trips"]:
                publish(conn, "fleet.trips.changed", {"batch_id": batch_id, "trip_nos": sorted(touched["trips"])},
                        tenant_id=tenant, source="fleet")
            if vehicles:
                publish(conn, "fleet.fixes.stored", {"batch_id": batch_id, "trip_nos": sorted(touched["gps_trips"]),
                                                     "vehicles": vehicles}, tenant_id=tenant, source="fleet")
        conn.commit()
    summary = {"batch_id": batch_id, "reprocessed": bool(done), "seconds": round(secs, 2), **stats}
    logger.info("batch %s processed: %s", batch_id, summary)
    return summary


def handle(events: list[Event]) -> None:
    """Consumer entry point for ingest.batch.landed."""
    for ev in events:
        process_batch(int(ev.payload["batch_id"]))
