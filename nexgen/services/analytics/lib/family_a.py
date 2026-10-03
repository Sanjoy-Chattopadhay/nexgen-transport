"""The historic trip store (Smart-Truck's "family A" tables), kept in step
with the TTA trips.

Smart-Truck mirrored every ingested TTA trip into drivers / vehicles /
locations / customers / trips (`_sync_legacy_trip`) and then refreshed the
summaries the Dashboard, Drivers, Vehicles and Routes pages read, for exactly
the entities the ingest touched. NexGen does the same from the fleet
service's `fleet.trips.changed` event, with Smart-Truck's own functions
(copied unchanged below from backend/app/services/tta_ingestion.py), reading
the trips through the legacy-shaped views.
"""

from __future__ import annotations

import logging

from nexgen.services.analytics.lib.trip_metrics import eta_outcome, journey_metrics

logger = logging.getLogger(__name__)

UNKNOWN_DRIVER = "Unknown"


def _lookup_or_insert(cur, select_sql, select_params, insert_sql, insert_params):
    cur.execute(select_sql, select_params)
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute(insert_sql, insert_params)
    return cur.lastrowid


def _sync_legacy_trip(cur, trip_row: dict, metrics_row: dict) -> dict:
    """Mirror one TTA trip into drivers/vehicles/locations/customers/trips.

    Returns the set of legacy entities this trip touches (driver/vehicle/route/
    customer/date) — INCLUDING the trip's prior values when it already existed —
    so the caller can incrementally refresh exactly those summary rows without
    losing data on a re-sync that moves the trip.
    """
    # --- Dimensions ---
    driver_name = trip_row["s_driver_name"] or UNKNOWN_DRIVER
    mobile = trip_row["s_driver_mobile_no"]
    if mobile is None:
        driver_id = _lookup_or_insert(
            cur,
            "SELECT id FROM drivers WHERE name=%s AND mobile1 IS NULL", (driver_name,),
            "INSERT INTO drivers (name, mobile1) VALUES (%s, %s)", (driver_name, None),
        )
    else:
        driver_id = _lookup_or_insert(
            cur,
            "SELECT id FROM drivers WHERE name=%s AND mobile1=%s", (driver_name, mobile),
            "INSERT INTO drivers (name, mobile1) VALUES (%s, %s)", (driver_name, mobile),
        )

    vehicle_id = None
    if trip_row["s_asset_id"]:
        vehicle_id = _lookup_or_insert(
            cur,
            "SELECT id FROM vehicles WHERE asset_id=%s", (trip_row["s_asset_id"],),
            "INSERT INTO vehicles (asset_id, asset_type) VALUES (%s, %s)",
            (trip_row["s_asset_id"], trip_row["s_asset_type"]),
        )

    def _location_id(name):
        if not name:
            return None
        return _lookup_or_insert(
            cur,
            "SELECT id FROM locations WHERE name=%s", (name,),
            "INSERT INTO locations (name) VALUES (%s)", (name,),
        )

    origin_id = _location_id(trip_row["s_org_node_name"])
    dest_id = _location_id(trip_row["s_dest_node_name"])

    customer_id = None
    if trip_row["s_cne_name"]:
        customer_id = _lookup_or_insert(
            cur,
            "SELECT id FROM customers WHERE cust_login_id=%s", (trip_row["s_cne_name"],),
            "INSERT INTO customers (cne_name, cust_login_id) VALUES (%s, %s)",
            (trip_row["s_cne_name"], trip_row["s_cne_name"]),
        )

    # --- Computed analytics (same rules as the CSV migration) ---
    trip_start = trip_row["dt_trip_start"]
    trip_eta = trip_row["dt_trip_eta"]
    ata_in = trip_row["dt_trip_ata"]
    # trip_end IS the actual arrival, per this schema's documented mapping.
    # It used to fall back to dt_trip_end (the administrative closing stamp),
    # which made every journey metric below describe a trip that never ran.
    # The closing stamp is not lost: it stays on tta_trips.dt_trip_end, and
    # its reason on trips.trip_close_remark.
    trip_end = ata_in
    eta_data_status = "available" if ata_in is not None else "eta_data_unavailable"

    trip_km = metrics_row.get("d_distance_travelled_km")

    # Both derived from the actual arrival only — see trip_metrics. The same
    # no-arrival condition already sets eta_data_status='eta_data_unavailable'.
    duration, avg_speed = journey_metrics(trip_start, ata_in, trip_km)
    eta_met, eta_delay = eta_outcome(ata_in, trip_eta)

    row = {
        "dispatch_entry_no": str(trip_row["i_trip_no"]),
        "driver_id": driver_id,
        "vehicle_id": vehicle_id,
        "origin_id": origin_id,
        "destination_id": dest_id,
        "customer_id": customer_id,
        "trip_start": trip_start,
        "trip_end": trip_end,
        # The administrative closure, kept whole and separate from the arrival.
        "trip_closed_at": trip_row["dt_trip_end"],
        "trip_eta": trip_eta,
        "ata_in": ata_in,
        "ata_out": metrics_row.get("dt_ata_out"),
        "trip_km": trip_km,
        "total_dist": trip_km,
        "trip_duration_minutes": duration,
        "eta_met": eta_met,
        "eta_delay_minutes": eta_delay,
        "avg_speed_kmph": avg_speed,
        "eta_data_status": eta_data_status,
        "trip_status": trip_row["c_trip_status"],
        "trip_close_remark": trip_row["s_close_reason"],
        "material_desc": metrics_row.get("s_material_desc"),
        "invoice_no": trip_row["s_invoice"],
        "ref_no": trip_row["s_shipment_id"],
        "trip_seq": metrics_row.get("i_trip_seq"),
        "device_id": trip_row["s_device_id"],
        "cnr_id": trip_row["i_cnr_id"],
        "created_by": trip_row["s_created_by"],
    }
    # Capture prior FKs BEFORE the upsert, so a re-sync that MOVES this trip
    # (different driver/vehicle/route/date) also refreshes the entity it left.
    cur.execute(
        "SELECT driver_id, vehicle_id, origin_id, destination_id, customer_id, "
        "DATE(trip_start) AS d FROM trips WHERE dispatch_entry_no=%s",
        (row["dispatch_entry_no"],),
    )
    prev = cur.fetchone()

    _upsert(cur, "trips", row, key="dispatch_entry_no")

    affected = {
        "driver_ids": {driver_id},
        "vehicle_ids": {vehicle_id},
        "customer_ids": {customer_id},
        "routes": {(origin_id, dest_id)},
        "dates": {trip_start.date() if trip_start else None},
    }
    if prev:
        affected["driver_ids"].add(prev["driver_id"])
        affected["vehicle_ids"].add(prev["vehicle_id"])
        affected["customer_ids"].add(prev["customer_id"])
        affected["routes"].add((prev["origin_id"], prev["destination_id"]))
        affected["dates"].add(prev["d"])
    return affected


def _upsert(cur, table: str, row: dict, key: str):
    cols = list(row.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c}=VALUES({c})" for c in cols if c != key)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    cur.execute(sql, [row[c] for c in cols])


def sync_trips(conn, trip_nos: list[int]) -> dict:
    """Mirror the given trips into the historic store and refresh the
    summaries they touch (Smart-Truck's ingest steps 4 and 7)."""
    affected = {"driver_ids": set(), "vehicle_ids": set(), "customer_ids": set(), "routes": set(), "dates": set()}
    synced, errors = 0, []
    for i in range(0, len(trip_nos), 500):
        chunk = trip_nos[i:i + 500]
        marks = ",".join(["%s"] * len(chunk))
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM tta_trips WHERE i_trip_no IN ({marks})", chunk)
            trips = {r["i_trip_no"]: r for r in cur.fetchall()}
            cur.execute(f"SELECT * FROM tta_trip_metrics WHERE i_trip_no IN ({marks})", chunk)
            metrics = {r["i_trip_no"]: r for r in cur.fetchall()}
            for no in chunk:
                trip = trips.get(no)
                if trip is None:
                    continue
                try:
                    touched = _sync_legacy_trip(cur, trip, metrics.get(no) or {})
                    for k, vals in touched.items():
                        affected[k].update(v for v in vals if v)
                    synced += 1
                except Exception as exc:  # noqa: BLE001 -- one bad trip must not stop the rest
                    errors.append(f"trip {no}: {exc}")
                    logger.exception("historic-store sync failed for trip %s", no)
        conn.commit()
    summaries = None
    if synced:
        from nexgen.services.analytics.lib.data_migration import refresh_summaries_incremental
        summaries = refresh_summaries_incremental(conn, affected)
    return {"legacy_trips_synced": synced, "errors": errors[:20], "summaries": summaries}
