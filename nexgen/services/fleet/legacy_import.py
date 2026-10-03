"""One-time import of Smart-Truck's trips and GPS into the normalised tables.

Server-side INSERT ... SELECT statements on the same MySQL server, the way
Geo-Fencing copied the feed: millions of rows never pass through Python, so
the import takes minutes. The legacy `smart_truck` database is only ever
read (every statement writes to nx_fleet and reads from smart_truck).

Steps, each idempotent (re-running tops up rather than duplicating):

  1. entities from the distinct values in tta_trips / tta_trip_gps
  2. trips, the upstream's figures, the source records
  3. GPS, month by month: one row per physical fix (INSERT IGNORE on the
     vehicle key keeps the first copy of a fix shared by several
     consignments), then per-trip overrides for the 291 fixes whose per-fix
     distance differs between copies, then each trip's window
  4. verify: every trip's copy, rebuilt from the one stored copy, must equal
     its legacy copy row for row (see verify_trip_copies)

Imported fixes carry i_batch_id 0, meaning "from the legacy smart_truck
database".
"""

from __future__ import annotations

import logging
import time

from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.tenancy import current_tenant_id

logger = logging.getLogger(__name__)

LEGACY_BATCH = 0


def _run(cur, label: str, sql: str, params=None, report: dict | None = None) -> int:
    t0 = time.perf_counter()
    cur.execute(sql, params)
    n = cur.rowcount
    secs = time.perf_counter() - t0
    logger.info("%-34s %9d rows  %6.1fs", label, n, secs)
    if report is not None:
        report[label] = {"rows": n, "seconds": round(secs, 1)}
    return n


def import_smart_truck(limit_trips: int | None = None) -> dict:
    cfg = get_config()
    st = f"`{cfg.legacy_database('smart_truck')}`"
    tid = current_tenant_id()
    report: dict = {"tenant_id": tid, "source": st.strip("`")}
    trip_filter = ""
    if limit_trips:
        trip_filter = f" AND t.i_trip_no IN (SELECT i_trip_no FROM (SELECT i_trip_no FROM {st}.tta_trips ORDER BY i_trip_no LIMIT {int(limit_trips)}) lim)"
    with connect("fleet", read_timeout=7200, write_timeout=7200) as conn:
        with conn.cursor() as cur:
            cur.execute("SET SESSION innodb_lock_wait_timeout = 600")
            # -- 1. entities --------------------------------------------------
            _run(cur, "consignor", f"""
                INSERT INTO consignor (i_tenant_id, i_cnr_id, s_cnr_name, dt_created, dt_modified)
                SELECT {tid}, i_cnr_id, s_cnr_name, dt_created, dt_modified FROM {st}.consignors
                ON DUPLICATE KEY UPDATE s_cnr_name=VALUES(s_cnr_name)""", report=report)
            _run(cur, "vehicle", f"""
                INSERT IGNORE INTO vehicle (i_tenant_id, s_asset_no)
                SELECT DISTINCT {tid}, s_asset_id FROM (
                    SELECT s_asset_id FROM {st}.tta_trips WHERE s_asset_id IS NOT NULL
                    UNION SELECT DISTINCT s_asset_id FROM {st}.tta_trip_gps WHERE s_asset_id IS NOT NULL) a""",
                 report=report)
            _run(cur, "device", f"""
                INSERT IGNORE INTO device (i_tenant_id, s_device_no)
                SELECT DISTINCT {tid}, s_device_id FROM (
                    SELECT s_device_id FROM {st}.tta_trips WHERE s_device_id IS NOT NULL
                    UNION SELECT DISTINCT s_device_id FROM {st}.tta_trip_gps WHERE s_device_id IS NOT NULL) d""",
                 report=report)
            _run(cur, "transporter", f"""
                INSERT IGNORE INTO transporter (i_tenant_id, s_code, s_name)
                SELECT DISTINCT {tid}, IFNULL(i_trans_id, ''), IFNULL(s_trans_name, '') FROM {st}.tta_trips
                WHERE i_trans_id IS NOT NULL OR s_trans_name IS NOT NULL""", report=report)
            _run(cur, "consignee", f"""
                INSERT IGNORE INTO consignee (i_tenant_id, s_name)
                SELECT DISTINCT {tid}, s_cne_name FROM {st}.tta_trips WHERE s_cne_name IS NOT NULL""", report=report)
            _run(cur, "location", f"""
                INSERT IGNORE INTO location (i_tenant_id, i_node_no, s_name)
                SELECT DISTINCT {tid}, n, s FROM (
                    SELECT IFNULL(i_org_node_no, 0) n, IFNULL(s_org_node_name, '') s FROM {st}.tta_trips
                        WHERE i_org_node_no IS NOT NULL OR s_org_node_name IS NOT NULL
                    UNION SELECT IFNULL(i_dest_node_no, 0), IFNULL(s_dest_node_name, '') FROM {st}.tta_trips
                        WHERE i_dest_node_no IS NOT NULL OR s_dest_node_name IS NOT NULL
                    UNION SELECT 0, s_final_dest FROM {st}.tta_trips WHERE s_final_dest IS NOT NULL
                    UNION SELECT 0, s_load_plant FROM {st}.tta_trips WHERE s_load_plant IS NOT NULL) l""",
                 report=report)
            _run(cur, "driver", f"""
                INSERT IGNORE INTO driver (i_tenant_id, s_name, s_mobile)
                SELECT DISTINCT {tid}, IFNULL(s_driver_name, ''), IFNULL(s_driver_mobile_no, '') FROM {st}.tta_trips
                WHERE s_driver_name IS NOT NULL OR s_driver_mobile_no IS NOT NULL""", report=report)
            _run(cur, "gps_entity", f"""
                INSERT IGNORE INTO gps_entity (i_tenant_id, i_entity_id, s_entity_name)
                SELECT {tid}, i_entity_id, MAX(s_entity_name) FROM {st}.tta_trip_gps
                WHERE i_entity_id IS NOT NULL GROUP BY i_entity_id""", report=report)
            _run(cur, "ref_waypoint", f"""
                INSERT IGNORE INTO ref_waypoint (s_name, s_state_abbr)
                SELECT DISTINCT n, s FROM (
                    SELECT s_wpnt1 n, IFNULL(s_wpnt1_st_abbr, '') s FROM {st}.tta_trip_gps WHERE s_wpnt1 IS NOT NULL
                    UNION SELECT s_wpnt2, IFNULL(s_wpnt2_st_abbr, '') FROM {st}.tta_trip_gps WHERE s_wpnt2 IS NOT NULL) w""",
                 report=report)
            _run(cur, "ref_gps_status", f"""
                INSERT IGNORE INTO ref_gps_status (s_status, b_moving, i_speed_kmph)
                SELECT s_status, MAX(is_moving), MAX(i_status_speed_kmph) FROM {st}.tta_trip_gps
                WHERE s_status IS NOT NULL GROUP BY s_status""", report=report)
            conn.commit()

            # -- 2. trips -----------------------------------------------------
            _run(cur, "trip", f"""
                INSERT INTO trip (i_tenant_id, i_trip_no, s_trip_class, i_cnr_id, s_cnr_name, i_vehicle_id,
                    i_device_id, s_asset_type, c_trip_type, s_trip_type_desc, i_origin_id, i_dest_id,
                    i_final_dest_id, i_load_plant_id, dt_booking, dt_trip_start, dt_trip_eta, dt_trip_ata,
                    dt_trip_end, i_transporter_id, i_consignee_id, c_trip_status, s_close_reason, s_invoice,
                    s_card_id, s_shipment_id, s_event_code, s_gate_entry_no, i_driver_id, i_route_id,
                    s_created_by, s_modified_by, i_gps_ping_count, i_batch_id, dt_created, dt_modified)
                SELECT {tid}, t.i_trip_no, t.s_trip_class, t.i_cnr_id, t.s_cnr_name, v.i_vehicle_id,
                    dv.i_device_id, t.s_asset_type, t.c_trip_type, t.s_trip_type_desc, o.i_location_id,
                    de.i_location_id, fd.i_location_id, lp.i_location_id, t.dt_booking, t.dt_trip_start,
                    t.dt_trip_eta, t.dt_trip_ata, t.dt_trip_end, tr.i_transporter_id, ce.i_consignee_id,
                    t.c_trip_status, t.s_close_reason, t.s_invoice, t.s_card_id, t.s_shipment_id, t.s_event_code,
                    t.s_gate_entry_no, dr.i_driver_id, t.i_route_id, t.s_created_by, t.s_modified_by,
                    t.i_gps_ping_count, {LEGACY_BATCH}, t.dt_created, t.dt_modified
                FROM {st}.tta_trips t
                LEFT JOIN vehicle v ON v.i_tenant_id={tid} AND v.s_asset_no = t.s_asset_id
                LEFT JOIN device dv ON dv.i_tenant_id={tid} AND dv.s_device_no = t.s_device_id
                LEFT JOIN location o ON o.i_tenant_id={tid} AND o.i_node_no = IFNULL(t.i_org_node_no, 0)
                     AND o.s_name = IFNULL(t.s_org_node_name, '')
                     AND (t.i_org_node_no IS NOT NULL OR t.s_org_node_name IS NOT NULL)
                LEFT JOIN location de ON de.i_tenant_id={tid} AND de.i_node_no = IFNULL(t.i_dest_node_no, 0)
                     AND de.s_name = IFNULL(t.s_dest_node_name, '')
                     AND (t.i_dest_node_no IS NOT NULL OR t.s_dest_node_name IS NOT NULL)
                LEFT JOIN location fd ON fd.i_tenant_id={tid} AND fd.i_node_no = 0 AND fd.s_name = t.s_final_dest
                LEFT JOIN location lp ON lp.i_tenant_id={tid} AND lp.i_node_no = 0 AND lp.s_name = t.s_load_plant
                LEFT JOIN transporter tr ON tr.i_tenant_id={tid} AND tr.s_code = IFNULL(t.i_trans_id, '')
                     AND tr.s_name = IFNULL(t.s_trans_name, '')
                     AND (t.i_trans_id IS NOT NULL OR t.s_trans_name IS NOT NULL)
                LEFT JOIN consignee ce ON ce.i_tenant_id={tid} AND ce.s_name = t.s_cne_name
                LEFT JOIN driver dr ON dr.i_tenant_id={tid} AND dr.s_name = IFNULL(t.s_driver_name, '')
                     AND dr.s_mobile = IFNULL(t.s_driver_mobile_no, '')
                     AND (t.s_driver_name IS NOT NULL OR t.s_driver_mobile_no IS NOT NULL)
                WHERE 1=1 {trip_filter}
                ON DUPLICATE KEY UPDATE s_trip_class=VALUES(s_trip_class), i_cnr_id=VALUES(i_cnr_id),
                    s_cnr_name=VALUES(s_cnr_name), i_vehicle_id=VALUES(i_vehicle_id), i_device_id=VALUES(i_device_id),
                    c_trip_status=VALUES(c_trip_status), dt_trip_ata=VALUES(dt_trip_ata), dt_trip_end=VALUES(dt_trip_end),
                    i_gps_ping_count=VALUES(i_gps_ping_count), dt_modified=VALUES(dt_modified)""", report=report)
            metric_cols = ("i_sl_no, s_tag, s_store_entry_no, dt_ata_out, dt_delivery, s_delivery_status, "
                           "s_delivery_dur, i_delivery_delta_min, s_transit_time, i_transit_time_min, s_detention, "
                           "i_detention_min, s_total_moving_time, i_moving_time_min, s_total_stoppage_time, "
                           "i_stoppage_time_min, s_plant_vivo, i_plant_vivo_min, d_distance_travelled_km, "
                           "i_speed_violation, d_uptime_pct, s_service_provider, s_supplier_name, s_cne_contact_no, "
                           "i_cne_pin, s_ship_to_address, i_geo_id, d_inv_qty, s_material_desc, s_asset_make, "
                           "s_asset_model, s_close_remarks, s_fo_no, i_trip_seq, s_tta_ex_nd, s_det_ex_nd, dt_created")
            _run(cur, "trip_provider_metric", f"""
                INSERT IGNORE INTO trip_provider_metric (i_tenant_id, i_trip_no, {metric_cols})
                SELECT {tid}, t.i_trip_no, {', '.join('t.' + c.strip() for c in metric_cols.split(','))}
                FROM {st}.tta_trip_metrics t WHERE 1=1 {trip_filter}""", report=report)
            _run(cur, "trip_source_record", f"""
                INSERT IGNORE INTO trip_source_record (i_tenant_id, i_trip_no, j_record, i_batch_id, dt_received)
                SELECT {tid}, t.i_trip_no, t.raw_json, {LEGACY_BATCH}, IFNULL(t.dt_created, NOW())
                FROM {st}.tta_trip_metrics t WHERE t.raw_json IS NOT NULL {trip_filter}""", report=report)
            conn.commit()

            # -- 3. GPS, month by month -------------------------------------
            cur.execute(f"SELECT MIN(dt_message) a, MAX(dt_message) b FROM {st}.tta_trip_gps")
            span = cur.fetchone()
            gps_rows = 0
            if span and span["a"]:
                month = span["a"].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                while month <= span["b"]:
                    nxt = (month.replace(year=month.year + 1, month=1) if month.month == 12
                           else month.replace(month=month.month + 1))
                    gps_rows += _run(cur, f"gps_fix {month:%Y-%m}", f"""
                        INSERT IGNORE INTO gps_fix (i_tenant_id, i_vehicle_id, dt_fix, i_seq, d_lat, d_lon, i_speed,
                            i_status_id, i_wp1_id, i_wp1_m, i_wp2_id, i_wp2_m, i_dist_m, i_device_id, i_entity_id,
                            c_uom, c_source, i_batch_id)
                        SELECT {tid}, v.i_vehicle_id, g.dt_message, 0, g.d_lat, g.d_long, g.i_speed, s.i_status_id,
                            w1.i_waypoint_id, g.i_wpnt1_mt, w2.i_waypoint_id, g.i_wpnt2_mt, g.i_dist, dv.i_device_id,
                            g.i_entity_id, g.s_uom, 'R', {LEGACY_BATCH}
                        FROM {st}.tta_trip_gps g
                        JOIN {st}.tta_trips t ON t.i_trip_no = g.i_trip_no
                        JOIN vehicle v ON v.i_tenant_id={tid} AND v.s_asset_no = g.s_asset_id
                        LEFT JOIN device dv ON dv.i_tenant_id={tid} AND dv.s_device_no = g.s_device_id
                        LEFT JOIN ref_gps_status s ON s.s_status = g.s_status
                        LEFT JOIN ref_waypoint w1 ON w1.s_name = g.s_wpnt1 AND w1.s_state_abbr = IFNULL(g.s_wpnt1_st_abbr, '')
                        LEFT JOIN ref_waypoint w2 ON w2.s_name = g.s_wpnt2 AND w2.s_state_abbr = IFNULL(g.s_wpnt2_st_abbr, '')
                        WHERE g.dt_message >= %s AND g.dt_message < %s {trip_filter}
                        ORDER BY g.i_trip_no, g.dt_message""", (month, nxt), report=report)
                    conn.commit()
                    month = nxt
            report["gps_fix_rows"] = gps_rows

            # Per-trip distance where a trip's copy differs from the stored fix.
            _run(cur, "trip_fix_override", f"""
                INSERT IGNORE INTO trip_fix_override (i_tenant_id, i_trip_no, dt_fix, i_seq, i_dist_m)
                SELECT {tid}, g.i_trip_no, g.dt_message, 0, g.i_dist
                FROM {st}.tta_trip_gps g
                JOIN {st}.tta_trips t ON t.i_trip_no = g.i_trip_no
                JOIN vehicle v ON v.i_tenant_id={tid} AND v.s_asset_no = g.s_asset_id
                JOIN gps_fix f ON f.i_tenant_id={tid} AND f.i_vehicle_id = v.i_vehicle_id
                     AND f.dt_fix = g.dt_message AND f.i_seq = 0
                WHERE NOT (f.i_dist_m <=> g.i_dist) {trip_filter}""", report=report)
            conn.commit()

            # Each trip's window: the span of the copy it had.
            _run(cur, "trip_gps_window", f"""
                INSERT INTO trip_gps_window (i_tenant_id, i_trip_no, i_vehicle_id, dt_from, dt_to,
                    dt_first_fix, dt_last_fix)
                SELECT {tid}, g.i_trip_no, MIN(v.i_vehicle_id), MIN(g.dt_message), MAX(g.dt_message),
                       MIN(g.dt_message), MAX(g.dt_message)
                FROM {st}.tta_trip_gps g
                JOIN {st}.tta_trips t ON t.i_trip_no = g.i_trip_no
                JOIN vehicle v ON v.i_tenant_id={tid} AND v.s_asset_no = g.s_asset_id
                WHERE 1=1 {trip_filter}
                GROUP BY g.i_trip_no
                ON DUPLICATE KEY UPDATE dt_from=LEAST(dt_from, VALUES(dt_from)), dt_to=GREATEST(dt_to, VALUES(dt_to)),
                    dt_first_fix=LEAST(COALESCE(dt_first_fix, VALUES(dt_first_fix)), VALUES(dt_first_fix)),
                    dt_last_fix=GREATEST(COALESCE(dt_last_fix, VALUES(dt_last_fix)), VALUES(dt_last_fix))""",
                 report=report)
            _run(cur, "vehicle fix stats", f"""
                UPDATE vehicle v JOIN (
                    SELECT i_vehicle_id, MIN(dt_fix) a, MAX(dt_fix) b, COUNT(*) n FROM gps_fix
                    WHERE i_tenant_id={tid} GROUP BY i_vehicle_id) f ON f.i_vehicle_id = v.i_vehicle_id
                SET v.dt_first_fix = f.a, v.dt_last_fix = f.b, v.i_fixes = f.n""", report=report)
            conn.commit()
    return report


def verify_trip_copies(sample: int | None = None) -> dict:
    """Compare every trip's legacy GPS copy with the one rebuilt from gps_fix.

    Per trip, both sides are reduced to a row count and an order-independent
    checksum of every legacy column the analysis code reads; a trip passes
    when both match. Returns the counts and the first differing trips.
    """
    cfg = get_config()
    st = f"`{cfg.legacy_database('smart_truck')}`"
    cols = ("dt_message, d_lat, d_long, i_speed, s_wpnt1, i_wpnt1_mt, s_wpnt1_st_abbr, s_wpnt2, i_wpnt2_mt, "
            "s_wpnt2_st_abbr, s_uom, i_dist, i_cdist, s_status, is_moving, i_status_speed_kmph, s_asset_id, "
            "s_device_id, i_entity_id, s_entity_name")
    # '~' stands for NULL, so a NULL and an empty string, or two shifted NULLs,
    # cannot produce the same text.
    parts = ", ".join(f"IFNULL({c}, '~')" for c in cols.split(", "))
    expr = f"SUM(CRC32(CONCAT_WS('|', {parts})))"
    limit = f"LIMIT {int(sample)}" if sample else ""
    with connect("fleet", read_timeout=7200) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT i_trip_no FROM {st}.tta_trips ORDER BY i_trip_no {limit}")
        trips = [r["i_trip_no"] for r in cur.fetchall()]
        if not trips:
            return {"trips": 0}
        lo, hi = min(trips), max(trips)
        cur.execute(f"SELECT i_trip_no, COUNT(*) n, {expr} c FROM {st}.tta_trip_gps "
                    f"WHERE i_trip_no BETWEEN %s AND %s GROUP BY i_trip_no", (lo, hi))
        legacy = {r["i_trip_no"]: (int(r["n"]), int(r["c"] or 0)) for r in cur.fetchall()}
        cur.execute(f"SELECT i_trip_no, COUNT(*) n, {expr} c FROM v1_tta_trip_gps_cdist "
                    f"WHERE i_trip_no BETWEEN %s AND %s GROUP BY i_trip_no", (lo, hi))
        ours = {r["i_trip_no"]: (int(r["n"]), int(r["c"] or 0)) for r in cur.fetchall()}
    same = more = fewer = differ = 0
    examples = []
    for t in trips:
        a, b = legacy.get(t, (0, 0)), ours.get(t, (0, 0))
        if a == b:
            same += 1
            continue
        if b[0] > a[0]:
            more += 1
        elif b[0] < a[0]:
            fewer += 1
        else:
            differ += 1
        if len(examples) < 20:
            examples.append({"trip_no": t, "legacy_rows": a[0], "nexgen_rows": b[0]})
    return {"trips": len(trips), "identical": same, "more_fixes": more, "fewer_fixes": fewer,
            "same_count_different_values": differ, "examples": examples}
