"""Parsing the eTrans TTA feed: value normalisers, the export-file parser and
the record -> row mappers.

Ported unchanged from Smart-Truck's backend/app/services/tta_ingestion.py
(lines 82-503), so a record parses to exactly the values Smart-Truck stored.
Used by ingestion (to read uploaded exports) and by the fleet processor (to
turn landed records into normalised rows). The writer half of that module is
replaced by the fleet processor, which stores the same values in the
normalised tables.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================
# VALUE NORMALIZERS
# ============================================

_NA = {"", "na", "n/a", "null", "none", "nan", "-"}

_DATE_FORMATS = (
    "%d-%m-%Y %H:%M:%S",   # 13-07-2026 00:23:00
    "%d-%b-%Y %H:%M:%S",   # 14-Jul-2026 05:58:50
    "%d/%m/%Y %H:%M:%S",   # 13/07/2026 00:25:17  (GPS pings)
    "%d-%m-%Y %H:%M",
    "%d-%b-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d-%m-%Y",
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
)


def clean(val):
    """None-out NA-ish values; strip strings."""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return None if s.lower() in _NA else s
    return val


def parse_dt(val):
    v = clean(val)
    if v is None or not isinstance(v, str):
        return v if isinstance(v, datetime) else None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    logger.warning("Unparseable datetime: %r", val)
    return None


def parse_int(val):
    v = clean(val)
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def parse_float(val):
    v = clean(val)
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


_DUR_RE = re.compile(
    r"(?:(\d+)\s*Days?\s*)?(\d{1,3}):(\d{2})(?::(\d{2}))?", re.IGNORECASE
)

# The local-report feed spells durations out in words instead of HH:MM —
# "1 Day 4 Hours 16 Minutes 12 Seconds", " 6 Hours 51 Minutes 18 Seconds".
# Every unit is optional and singular/plural both occur ("1 Second").
_DUR_WORDS_RE = re.compile(
    r"(?:(\d+)\s*Days?)?\s*(?:(\d+)\s*Hours?)?\s*"
    r"(?:(\d+)\s*Min(?:ute)?s?)?\s*(?:(\d+)\s*Sec(?:ond)?s?)?",
    re.IGNORECASE,
)


def parse_duration_min(val):
    """'1 Days 05:36' -> 1776 ; '14:29' -> 869 ;
    '1 Day 4 Hours 16 Minutes 12 Seconds' -> 1696 ; None-safe.

    Seconds are truncated, not rounded, so a duration never reports more
    elapsed time than actually passed.
    """
    v = clean(val)
    if v is None:
        return None
    m = _DUR_RE.search(str(v))
    if m:
        days = int(m.group(1) or 0)
        hours, minutes = int(m.group(2)), int(m.group(3))
        return days * 1440 + hours * 60 + minutes

    # Fall back to the spelled-out form. The regex is all-optional, so it also
    # matches the empty string — require at least one unit to have been found.
    w = _DUR_WORDS_RE.search(str(v))
    if not w or not any(w.groups()):
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in w.groups())
    return days * 1440 + hours * 60 + minutes + seconds // 60


def parse_delivery_delta_min(val):
    """'Before By 1 Days 18:24' -> -2544 ; 'Delayed By 02:10' -> +130."""
    v = clean(val)
    if v is None:
        return None
    minutes = parse_duration_min(v)
    if minutes is None:
        return None
    low = str(v).lower()
    if "before" in low or "early" in low:
        return -minutes
    return minutes


# A party name in the LOCAL report carries its master-data code inline:
#   "VIRK CARGO MOVERS ( 0000017041 ) "  ->  "VIRK CARGO MOVERS"
#   "CRM BARA ( C036 ) "                 ->  "CRM BARA"
# The zonal feed sends the same carrier WITHOUT the bracket (and puts the code
# in its own trans_code field), so leaving these in place would file one real
# carrier under two different names as soon as both lanes are ingesting — every
# per-transporter aggregate would silently split in half.
#
# Only a bracket whose contents look like a CODE is stripped: it must contain a
# digit and no spaces. That protects genuine name qualifiers such as
# "TATA STEEL-BMW (GAMHARIA)", which is a DIFFERENT consignor from
# "TATA STEEL-BMW" and must not collapse into it.
_PARTY_CODE_RE = re.compile(r"^(.*?)\s*\(\s*([^()\s]+)\s*\)\s*$")
_LOOKS_LIKE_CODE = re.compile(r"^[A-Za-z]{0,3}\d[A-Za-z0-9\-/]*$")


def strip_party_code(val) -> tuple[object, object]:
    """Split an inline master-data code off a party name -> (name, code).

    `code` is None when the value carries none, in which case `name` is the
    original value untouched.
    """
    v = clean(val)
    if v is None or not isinstance(v, str):
        return v, None
    m = _PARTY_CODE_RE.match(v)
    if not m:
        return v, None
    name, code = m.group(1).strip(), m.group(2).strip()
    if not name or not _LOOKS_LIKE_CODE.match(code):
        return v, None
    return name, code


def party_name(val):
    """The name half of strip_party_code — safe on values carrying no code."""
    return strip_party_code(val)[0]


# The local report has no trip-status FIELD — the filter lives only in the
# request — so the requested single-letter code is stamped onto each record.
# The zonal feed sends words ("Close"). Both land in the same column, and the
# KPI queries ask `LOWER(c_trip_status) LIKE '%close%'`, under which a bare "C"
# reads as NOT closed: every local trip would be counted as still active.
_STATUS_CODES = {"c": "Close", "o": "Open", "a": "Active", "p": "Pending"}


def normalize_trip_status(val):
    """Expand a single-letter status code onto the shared status vocabulary.

    Only bare one-character values are touched, so "Close" and every other
    spelled-out status the zonal feed sends passes through untouched.
    """
    v = clean(val)
    if isinstance(v, str) and len(v.strip()) == 1:
        return _STATUS_CODES.get(v.strip().lower(), v)
    return v


_STATUS_SPEED_RE = re.compile(r"moving\s+(\d+)", re.IGNORECASE)


def parse_status(status):
    """'Moving 51 km/h' -> (1, 51) ; 'Stopped' -> (0, None)."""
    s = clean(status)
    if s is None:
        return 0, None
    m = _STATUS_SPEED_RE.search(s)
    if m:
        return 1, int(m.group(1))
    return (1, None) if s.lower().startswith("moving") else (0, None)


# ============================================
# FILE PARSER (dynamic)
# ============================================

def _decode_json_at(text: str, idx: int):
    return json.JSONDecoder().raw_decode(text, idx)


def parse_tta_text(raw: str) -> list[dict]:
    """
    Parse a TTA export into [{"vehicle", "trip", "gps"}].
    Handles both the sectioned text format and pure JSON payloads.
    """
    raw = raw.lstrip("﻿")
    stripped = raw.strip()

    # --- Pure JSON payloads (the whole file is a single JSON value) ---
    # Only take this path when the entire file parses as JSON. Exports that
    # start with a bare trip object but then carry a "TRIP-GPS:" section (and
    # therefore trailing non-JSON data) fall through to the sectioned scanner.
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            records = data if isinstance(data, list) else [data]
            blocks = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                trip = rec.get("trip") or rec.get("tta") or rec.get("trip_tta_data") or rec
                gps = rec.get("gps") or rec.get("trip_gps_data") or rec.get("gps_data") or []
                if trip.get("trip_no") is None and trip.get("i_trip_no") is None:
                    continue
                blocks.append({
                    "vehicle": clean(rec.get("vehicle") or trip.get("vehicle_no")),
                    "trip": trip,
                    "gps": gps if isinstance(gps, list) else [],
                })
            return blocks

    # --- Sectioned / mixed text format ---
    # Rather than depend on section headers (which vary: "Trip TTA Data:",
    # "TRIP:", or sometimes absent entirely before a leading object), scan the
    # raw text for JSON containers directly. A `{` starts a trip record; an `[`
    # appearing before the next trip record is that trip's GPS array. Any
    # "Vehicle : <id>" seen before a trip object is attached to it.
    blocks = []
    vehicle_re = re.compile(r"Vehicle\s*:\s*(\S+)", re.IGNORECASE)

    pos, n = 0, len(raw)
    while pos < n:
        obj_start = raw.find("{", pos)
        if obj_start == -1:
            break

        vm_all = vehicle_re.findall(raw, pos, obj_start)
        vehicle = vm_all[-1] if vm_all else None

        trip, trip_end = _decode_json_at(raw, obj_start)
        if not isinstance(trip, dict):
            pos = trip_end
            continue

        # A GPS array belongs to this trip only if it appears before the next
        # trip object. (The `[` of the array precedes the `{` of its own pings.)
        gps = []
        next_obj = raw.find("{", trip_end)
        next_arr = raw.find("[", trip_end)
        if next_arr != -1 and (next_obj == -1 or next_arr < next_obj):
            decoded, gps_end = _decode_json_at(raw, next_arr)
            if isinstance(decoded, list):
                gps = decoded
                trip_end = gps_end

        blocks.append({
            "vehicle": vehicle or clean(trip.get("vehicle_no")),
            "trip": trip,
            "gps": gps,
        })
        pos = trip_end

    if not blocks:
        raise ValueError("No 'Trip TTA Data' blocks or JSON records found in file")
    return blocks


# ============================================
# TTA RECORD -> tta_trips COLUMN MAP
# ============================================

def map_trip_row(t: dict, trip_class: str = "zonal") -> dict:
    """Map a raw TTA record onto tta_trips columns (raw feed names supported too).

    `trip_class` records WHICH upstream lane the record came from ("zonal" |
    "local"). Both feeds share this mapper — the field names differ but the
    destination columns do not — so the tag is the only thing that lets the two
    populations be told apart once they are in the same table.
    """
    def g(*keys):
        for k in keys:
            if k in t and clean(t[k]) is not None:
                return t[k]
        return None

    # The local feed embeds the carrier's code in the name; the zonal feed sends
    # it separately as trans_code. Prefer the explicit field, fall back to the
    # one parsed out of the name, so i_trans_id is populated either way.
    trans_name, trans_code = strip_party_code(g("transporter", "s_trans_name"))

    return {
        "i_trip_no": parse_int(g("trip_no", "i_trip_no")),
        "i_cnr_id": parse_int(g("cnr_id", "i_cnr_id")),
        "s_trip_class": trip_class,
        # "consigner_name" (sic) is the local-report feed's spelling.
        "s_cnr_name": clean(g("consignor", "consigner_name", "s_cnr_name")),
        "s_asset_id": clean(g("vehicle_no", "s_asset_id")),
        "s_device_id": clean(g("device_id", "s_device_id")),
        "s_asset_type": clean(g("asset_type", "vehicle_type", "s_asset_type")),
        "c_trip_type": clean(g("c_trip_type")),
        "s_trip_type_desc": clean(g("trip_type", "s_trip_type_desc")),
        "i_org_node_no": parse_int(g("i_org_node_no")),
        "s_org_node_name": clean(g("origin", "s_org_node_name")),
        "i_dest_node_no": parse_int(g("i_dest_node_no")),
        "s_dest_node_name": party_name(g("destination", "s_dest_node_name")),
        "s_final_dest": clean(g("final_desc", "s_final_dest")),
        "s_load_plant": party_name(g("load_plant", "s_load_plant")),
        "dt_booking": parse_dt(g("booking_date", "dt_booking")),
        "dt_trip_start": parse_dt(g("dept_date", "dt_trip_start")),
        "dt_trip_eta": parse_dt(g("eta", "eta_time", "dt_trip_eta")),
        "dt_trip_ata": parse_dt(g("ata", "ata_time", "dt_trip_ata")),
        "dt_trip_end": parse_dt(g("trip_closing_dt", "dt_trip_end")),
        "i_trans_id": clean(g("trans_code", "i_trans_id")) or trans_code,
        "s_trans_name": trans_name,
        "s_cne_name": party_name(g("consignee", "s_cne_name")),
        "c_trip_status": normalize_trip_status(g("trip_status", "c_trip_status")),
        "s_close_reason": clean(g("trip_closed_reason", "close_reason", "s_close_reason")),
        "s_invoice": clean(g("invoice_no", "s_invoice")),
        "s_card_id": clean(g("card_no", "s_card_id")),
        "s_shipment_id": clean(g("lr_no", "s_shipment_id")),
        "s_event_code": clean(g("event_code", "s_event_code")),
        "s_gate_entry_no": clean(g("gate_entry_no", "s_gate_entry_no")),
        "s_driver_name": clean(g("driver_name", "s_driver_name")),
        "s_driver_mobile_no": clean(g("driver_no", "driver_mobile_no", "s_driver_mobile_no")),
        "i_route_id": parse_int(g("route_id", "i_route_id")),
        "s_created_by": clean(g("s_created_by")),
        "s_modified_by": clean(g("s_modified_by")),
    }


def map_metrics_row(t: dict, trip_no: int) -> dict:
    def g(*keys):
        for k in keys:
            if k in t and clean(t[k]) is not None:
                return t[k]
        return None

    return {
        "i_trip_no": trip_no,
        "i_sl_no": parse_int(g("sl_no")),
        "s_tag": clean(g("tag")),
        "s_store_entry_no": clean(g("store_entry_no")),
        "dt_ata_out": parse_dt(g("ata_out")),
        "dt_delivery": parse_dt(g("delivery_date")),
        "s_delivery_status": clean(g("delivery_status")),
        "s_delivery_dur": clean(g("delivery_dur")),
        "i_delivery_delta_min": parse_delivery_delta_min(g("delivery_dur")),
        "s_transit_time": clean(g("transit_time")),
        "i_transit_time_min": parse_duration_min(g("transit_time")),
        "s_detention": clean(g("detention")),
        "i_detention_min": parse_duration_min(g("detention")),
        "s_total_moving_time": clean(g("total_moving_time")),
        "i_moving_time_min": parse_duration_min(g("total_moving_time")),
        "s_total_stoppage_time": clean(g("total_stoppage_time")),
        "i_stoppage_time_min": parse_duration_min(g("total_stoppage_time")),
        "s_plant_vivo": clean(g("plant_vivo")),
        "i_plant_vivo_min": parse_duration_min(g("plant_vivo")),
        "d_distance_travelled_km": parse_float(g("distance_travelled")),
        "i_speed_violation": parse_int(g("speed_voilation", "speed_violation")),
        "d_uptime_pct": parse_float(g("up_time_per")),
        "s_service_provider": clean(g("service_provider")),
        "s_supplier_name": clean(g("supplier_name")),
        "s_cne_contact_no": clean(g("consignee_contact_no")),
        "i_cne_pin": parse_int(g("cne_pin", "consignee_pin")),
        "s_ship_to_address": clean(g("ship_to_address")),
        "i_geo_id": parse_int(g("geo_id")),
        "d_inv_qty": parse_float(g("inv_qty")),
        "s_material_desc": clean(g("material_desc")),
        "s_asset_make": clean(g("asset_make")),
        "s_asset_model": clean(g("asset_model")),
        "s_close_remarks": clean(g("close_remarks")),
        "s_fo_no": clean(g("fo_no")),
        "i_trip_seq": parse_int(g("trip_seq")),
        "s_tta_ex_nd": clean(g("tta_ex_nd")),
        "s_det_ex_nd": clean(g("det_ex_nd")),
        "raw_json": json.dumps(t, ensure_ascii=False, default=str),
    }


def map_gps_row(p: dict, trip_no: int) -> dict | None:
    lat = parse_float(p.get("r_lat"))
    lng = parse_float(p.get("r_long"))
    ts = parse_dt(p.get("r_message_dt"))
    if lat is None or lng is None or ts is None:
        return None
    is_moving, status_speed = parse_status(p.get("r_status"))
    return {
        "i_trip_no": trip_no,
        "s_asset_id": clean(p.get("r_asset_id")),
        "s_device_id": clean(p.get("r_device_id")),
        "i_entity_id": parse_int(p.get("r_entity_id")),
        "s_entity_name": clean(p.get("r_entity_name")),
        "dt_message": ts,
        "d_lat": lat,
        "d_long": lng,
        "i_speed": parse_int(p.get("r_speed")) or 0,
        "s_wpnt1": clean(p.get("r_wpnt1")),
        "i_wpnt1_mt": parse_int(p.get("r_wpnt1_mt")),
        "s_wpnt1_st_abbr": clean(p.get("r_wpnt1_st_abbr")),
        "s_wpnt2": clean(p.get("r_wpnt2")),
        "i_wpnt2_mt": parse_int(p.get("r_wpnt2_mt")),
        "s_wpnt2_st_abbr": clean(p.get("r_wpnt2_st_abbr")),
        "s_uom": clean(p.get("r_uom")),
        "i_dist": parse_int(p.get("r_dist")) or 0,
        "i_cdist": parse_int(p.get("r_cdist")) or 0,
        "s_status": clean(p.get("r_status")),
        "is_moving": is_moving,
        "i_status_speed_kmph": status_speed,
    }

