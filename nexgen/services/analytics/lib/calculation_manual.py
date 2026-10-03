"""The calculation manual — every number on the platform, traced to its source.

Written because "where does this figure come from?" was being answered from
memory, and the answers were sometimes wrong. Two of them were wrong in ways
that changed decisions: `detention` was being described as plant time when it
measures the delivery point, and "alerts per trip" was being read as a count of
speeding incidents when the feed sends a count of GPS samples.

So this is not prose about the calculations. It is the calculation set as data,
served to the UI, in three layers:

  1. **The feed** — the JSON keys the upstream API actually sends, and the
     column each one lands in. Both lanes, because they use different names for
     the same thing and that is where half the confusion starts.
  2. **The derivations** — every computed column, with its formula, its inputs,
     its unit, and the rule that decides when it is NULL rather than zero.
  3. **A worked example** — one real trip from the database, carried through
     every derivation with its own numbers, so a reader can check the arithmetic
     instead of trusting it.

Layer 3 is the point. A formula in a document can be out of date; a formula
shown next to the value it produced for trip 28844567 cannot be, because it is
computed from the same frame the dashboards read.

`backend/app/tests/test_calculation_manual.py` fails if a column documented here
stops existing, or if the ingest mapper grows a field this manual does not
mention. The manual cannot silently drift from the code.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nexgen.services.analytics.lib import tta_dashboard as tta
from nexgen.services.analytics.lib.tta_dashboard import apply_filters, load_df
from nexgen.services.analytics.lib.transporter_insights import _COMMODITY_NOTE

# ----------------------------------------------------------------------
# Layer 1 — the feed
# ----------------------------------------------------------------------
# (db_column, json_keys, parser, note). `json_keys` is ordered: the ingest
# mapper takes the first key present, which is how one mapper serves two feeds
# that name the same field differently.

TRIP_FIELDS = [
    ("i_trip_no", ["trip_no", "i_trip_no"], "int",
     "Primary key. A record without one is skipped at ingest."),
    ("s_trip_class", ["(set by the loader)"], "literal",
     "Which upstream lane the record came from: zonal or local. Not in the "
     "feed — stamped at ingest, and the only thing that lets the two "
     "populations be told apart once they share a table."),
    ("s_cnr_name", ["consignor", "consigner_name", "s_cnr_name"], "text",
     "\"consigner_name\" (sic) is the local report's spelling."),
    ("s_asset_id", ["vehicle_no", "s_asset_id"], "text", "Vehicle registration."),
    ("s_asset_type", ["asset_type", "vehicle_type", "s_asset_type"], "text",
     "Free text. Bucketed into vehicle_category — see the derivations."),
    ("s_org_node_name", ["origin", "s_org_node_name"], "text", "Loading plant."),
    ("s_dest_node_name", ["destination", "s_dest_node_name"], "party name",
     "Delivery node. The local feed appends a master-data code in brackets "
     "(\"CRM BARA ( C036 )\"); it is stripped, or one node would file under two "
     "names as soon as both lanes ingest."),
    ("s_load_plant", ["load_plant", "s_load_plant"], "party name",
     "Local feed only."),
    ("dt_booking", ["booking_date", "dt_booking"], "datetime",
     "Plant entry. Start of the origin detention window."),
    ("dt_trip_start", ["dept_date", "dt_trip_start"], "datetime",
     "Gate-out. Ends origin detention, starts transit."),
    ("dt_trip_eta", ["eta", "eta_time", "dt_trip_eta"], "datetime",
     "The carrier's own promise. Planned transit is measured against it."),
    ("dt_trip_ata", ["ata", "ata_time", "dt_trip_ata"], "datetime",
     "Arrival at destination. Ends transit, starts destination detention."),
    ("dt_trip_end", ["trip_closing_dt", "dt_trip_end"], "datetime",
     "Administrative closure, NOT an arrival. A trip closed as \"NEW TRIP "
     "FOUND\" or \"TRIP CLOSE BY GRN\" is a void record; deriving journey "
     "metrics from this stamp invented arrivals for trips that never ran."),
    ("s_trans_name", ["transporter", "s_trans_name"], "party name",
     "Carrier. Bracketed code stripped, then folded case-insensitively at load "
     "— the two lanes disagree on capitalisation and MySQL's collation hides it."),
    ("s_cne_name", ["consignee", "s_cne_name"], "party name", "Customer."),
    ("c_trip_status", ["trip_status", "c_trip_status"], "normalised", "Open / Close."),
    ("s_close_reason", ["trip_closed_reason", "close_reason"], "text",
     "Why the trip was closed. Distinguishes a real delivery from a void record."),
    ("s_driver_name", ["driver_name", "s_driver_name"], "text", ""),
    ("s_invoice", ["invoice_no", "s_invoice"], "text", ""),
]

METRIC_FIELDS = [
    ("dt_ata_out", ["ata_out"], "datetime",
     "Gate-out at the delivery point. Ends destination detention."),
    ("s_delivery_status", ["delivery_status"], "text",
     "The provider's own verdict: \"On Time Delivery\" / \"Delay Delivery\". "
     "The primary input to on-time %, and NULL on the whole local lane."),
    ("i_delivery_delta_min", ["delivery_dur"], "signed minutes",
     "Parsed from text: \"Before By 1 Days 18:24\" -> -2544, "
     "\"Delayed By 02:10\" -> +130. Negative is early."),
    ("i_transit_time_min", ["transit_time"], "minutes",
     "Provider-computed. Equals dt_trip_ata - dt_trip_start."),
    ("i_detention_min", ["detention"], "minutes",
     "Time at the DESTINATION, arrival to gate-out. Verified row by row: "
     "equals dt_ata_out - dt_trip_ata. This is not plant time."),
    ("i_plant_vivo_min", ["plant_vivo"], "minutes",
     "Time at the ORIGIN plant. Verified row by row: equals "
     "dt_trip_start - dt_booking. Same quantity the UI also calls dispatch lead."),
    ("i_moving_time_min", ["total_moving_time"], "minutes",
     "Wheels turning. Denominator of average speed."),
    ("i_stoppage_time_min", ["total_stoppage_time"], "minutes", ""),
    ("d_distance_travelled_km", ["distance_travelled"], "float", ""),
    ("i_speed_violation", ["speed_voilation", "speed_violation"], "int",
     "Count of GPS SAMPLES above the limit, not of speeding events. The "
     "upstream key is misspelled; both spellings are accepted."),
    ("d_uptime_pct", ["up_time_per"], "percent", "Share of the journey with live GPS."),
    ("s_service_provider", ["service_provider"], "text",
     "Own / Market. Sent on the zonal lane only."),
    ("i_cne_pin", ["cne_pin", "consignee_pin"], "int",
     "Destination PIN. Drives geocoding and the destination state."),
    ("s_material_desc", ["material_desc"], "text",
     "Commodity. The feed sends \"NA\" on the zonal lane and nothing on the "
     "local one, so it is empty on every trip — which is why no "
     "commodity-level report can be built."),
    ("d_inv_qty", ["inv_qty"], "float", "Invoice quantity."),
    ("i_geo_id", ["geo_id"], "int", "Upstream geofence id for the destination."),
]

# How the text durations become numbers. Non-obvious enough to be worth stating.
PARSERS = [
    ("duration", "'1 Days 05:36' -> 1776 min · '14:29' -> 869 min · "
                 "'1 Day 4 Hours 16 Minutes 12 Seconds' -> 1696 min",
     "Seconds are truncated rather than rounded, so a duration never reports "
     "more elapsed time than actually passed."),
    ("delivery delta", "'Before By 1 Days 18:24' -> -2544 min · "
                       "'Delayed By 02:10' -> +130 min",
     "Sign carries the meaning: negative is early, positive is late."),
    ("party name", "'VIRK CARGO MOVERS ( 0000017041 )' -> 'VIRK CARGO MOVERS'",
     "Only a bracket whose contents look like a master-data code is stripped, "
     "so a genuine bracketed name survives."),
    ("NA-ish values", "'NA', 'N/A', '-', '', 'None' -> NULL",
     "An upstream placeholder is not data. Storing it would make an empty "
     "field look populated."),
]

# ----------------------------------------------------------------------
# Layer 2 — the derivations
# ----------------------------------------------------------------------
# Mirrors tta_dashboard.load_df in order. `formula` is what the code does,
# written so it can be checked against the code beside it.

DERIVATIONS = [
    {
        "key": "transit_hours", "label": "Transit time", "unit": "h",
        "group": "Duration",
        "formula": "i_transit_time_min / 60",
        "inputs": ["i_transit_time_min"],
        "null_rule": "<= 0 becomes NULL",
        "why": "A zero transit means the trip was force-closed without ever "
               "running. Keeping it as zero would drag every average down and "
               "make a void record look like an instant delivery.",
    },
    {
        "key": "detention_hours", "label": "Detention at destination", "unit": "h",
        "group": "Duration",
        "formula": "i_detention_min / 60   (= dt_ata_out - dt_trip_ata)",
        "inputs": ["i_detention_min"],
        "null_rule": "NULL when the feed omits it (the whole local lane)",
        "why": "Unloading wait at the customer's yard. Reported as a median and "
               "a breach rate rather than a mean: the median is 0 h against a "
               "4.4 h mean, so the average describes a trip that does not exist.",
    },
    {
        "key": "plant_vivo_hours", "label": "Detention at origin", "unit": "h",
        "group": "Duration",
        "formula": "i_plant_vivo_min / 60   (= dt_trip_start - dt_booking)",
        "inputs": ["i_plant_vivo_min"],
        "null_rule": "NULL when the feed omits it",
        "why": "Hold at your own loading plant. The same arithmetic as dispatch "
               "lead — the number cannot tell you whether the plant held the "
               "truck or the load was simply booked early.",
    },
    {
        "key": "works_detention_hours", "label": "Declared works detention",
        "unit": "h", "group": "Duration",
        "formula": "i_works_detention_min / 60   "
                   "(generated column: dt_trip_start - dt_booking)",
        "inputs": ["dt_booking", "dt_trip_start"],
        "null_rule": "negative becomes NULL",
        "why": "A stored generated column, so it is recomputed by MySQL on every "
               "write and cannot go stale behind a missed backfill.",
    },
    {
        "key": "geofence_tail_hours", "label": "Time inside the plant fence after gate-out",
        "unit": "h", "group": "Duration",
        "formula": "i_geofence_tail_min / 60   "
                   "(= dt_geofence_out - dt_trip_start)",
        "inputs": ["dt_trip_start", "dt_geofence_out"],
        "null_rule": "negative becomes NULL; only exists where the GPS trail "
                     "confirmed the exit",
        "why": "The gate-out stamp fires before the truck has physically cleared "
               "the works, so the declared figure is structurally optimistic. "
               "This measures by how much instead of asserting it.",
    },
    {
        "key": "run_hours", "label": "Moving time", "unit": "h", "group": "Duration",
        "formula": "i_moving_time_min / 60", "inputs": ["i_moving_time_min"],
        "null_rule": "NULL when absent",
        "why": "Denominator of average speed.",
    },
    {
        "key": "delivery_delta_hours", "label": "Early / late by", "unit": "h",
        "group": "Reliability",
        "formula": "i_delivery_delta_min / 60",
        "inputs": ["i_delivery_delta_min"],
        "null_rule": "NULL when the provider sent no delivery duration",
        "why": "Signed: negative is early. Size of the miss, which is a "
               "different question from how often it misses.",
    },
    {
        "key": "planned_transit_hours", "label": "Promised transit", "unit": "h",
        "group": "Reliability",
        "formula": "(dt_trip_eta - dt_trip_start) in hours",
        "inputs": ["dt_trip_eta", "dt_trip_start"],
        "null_rule": "<= 0 becomes NULL",
        "why": "The carrier's own quote. An ETA at or before departure is not a "
               "promise, it is a data error.",
    },
    {
        "key": "is_on_time", "label": "On time", "unit": "0/1",
        "group": "Reliability",
        "formula": "1 if delivery_status contains 'on time'; 0 if it contains "
                   "'delay'; otherwise, where a delivery delta exists, "
                   "1 if delta <= 0 else 0; otherwise NULL",
        "inputs": ["s_delivery_status", "i_delivery_delta_min"],
        "null_rule": "NULL when the provider classified nothing AND sent no "
                     "delta — 59% of trips",
        "why": "NULL is the important case. It means unmeasured, not late. "
               "Every on-time percentage on the platform is computed over the "
               "non-NULL subset, which is why each one is shown with the number "
               "of trips behind it.",
    },
    {
        "key": "avg_speed_kmph", "label": "Average speed", "unit": "km/h",
        "group": "Movement",
        "formula": "d_distance_travelled_km / run_hours",
        "inputs": ["d_distance_travelled_km", "i_moving_time_min"],
        "null_rule": f"kept only within 1 - {tta.SPEED_CAP_KMPH} km/h",
        "why": "Distance over MOVING time, not elapsed time, so a long halt does "
               "not read as slow driving. Values outside the band come from a "
               "bad odometer or a truncated moving time, not from a truck.",
    },
    {
        "key": "vehicle_category", "label": "Vehicle category", "unit": "",
        "group": "Categorical",
        "formula": "keyword match on s_asset_type -> TRAILER / HEAVY VEHICLE / "
                   "LIGHT VEHICLE / SPEC-OTHER / UNSPECIFIED",
        "inputs": ["s_asset_type"],
        "null_rule": "blank or 'None' becomes UNSPECIFIED, never dropped",
        "why": "The raw type is free text with hundreds of spellings. Buckets "
               "make it groupable; UNSPECIFIED keeps unlabelled trucks visible "
               "rather than quietly excluding them from every fleet mix.",
    },
    {
        "key": "own_market_stated", "label": "Ownership (as reported)", "unit": "",
        "group": "Categorical",
        "formula": "s_service_provider -> Own / Market; anything missing -> "
                   f"'{tta.NOT_STATED}'",
        "inputs": ["s_service_provider"],
        "null_rule": "never NULL — that is the point",
        "why": "The field is sent on the zonal lane only. Dropping the 1,399 "
               "unlabelled trips made a carrier with 164 unlabelled and 56 "
               "market trips read as 100% market, which the data does not "
               "support. Use this column, not own_market, for any mix.",
    },
    {
        "key": "transporter", "label": "Carrier name (folded)", "unit": "",
        "group": "Categorical",
        "formula": "strip the bracketed master-data code, then fold on a "
                   "case- and whitespace-insensitive key, adopting the "
                   "most frequent spelling",
        "inputs": ["s_trans_name"],
        "null_rule": "NULL only when the feed sent no carrier",
        "why": "The zonal feed upper-cases names and the local feed title-cases "
               "them. MySQL's collation folds case so the database counts one "
               "carrier; pandas does not, so it counted two. Consignees arrive "
               "up to four ways.",
    },
    {
        "key": "lane", "label": "Lane", "unit": "",
        "group": "Categorical",
        "formula": "origin + ' -> ' + destination",
        "inputs": ["s_org_node_name", "s_dest_node_name"],
        "null_rule": "missing either side becomes '?'",
        "why": "The unit a carrier is fairly compared on. Comparing carriers on "
               "overall averages mostly measures which lanes they hold.",
    },
    {
        "key": "dest_state", "label": "Destination state", "unit": "",
        "group": "Categorical",
        "formula": "first 3 digits of i_cne_pin -> state, via PIN3_STATE_RANGES",
        "inputs": ["i_cne_pin"],
        "null_rule": "NULL when the PIN is missing or out of range",
        "why": "From the PIN rather than the node name: PIN blocks follow state "
               "lines, and unlike a node name a PIN cannot be spelled three "
               "different ways.",
    },
]


def _fmt(v, digits=2):
    # pd.NaT is not a Timestamp instance, so without this it falls through to
    # str() and reaches the UI as the literal text "NaT".
    if v is None or v is pd.NaT or (isinstance(v, float)
                                    and (np.isnan(v) or np.isinf(v))):
        return None
    if isinstance(v, pd.Timestamp) and pd.isna(v):
        return None
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return round(float(v), digits)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


# ----------------------------------------------------------------------
# Layer 3 — a real trip, carried through every derivation
# ----------------------------------------------------------------------

# The example is only useful if the trip actually exercises the derivations, so
# pick one that has the fields most likely to be missing.
_EXAMPLE_REQUIRED = ["transit_hours", "detention_hours", "plant_vivo_hours",
                     "planned_transit_hours", "avg_speed_kmph", "is_on_time"]


def worked_example(df: pd.DataFrame) -> dict:
    """One real trip, with every derived value and the inputs it came from."""
    if df.empty:
        return {"available": False,
                "reason": "No trips in the selected window."}

    candidates = df.dropna(subset=_EXAMPLE_REQUIRED)
    if candidates.empty:
        # Fall back to the most complete row available, and say so — an example
        # with holes still shows the arithmetic, but the reader should know it
        # is not the fully-populated case.
        filled = df[_EXAMPLE_REQUIRED].notna().sum(axis=1)
        row = df.loc[filled.idxmax()]
        partial = True
    else:
        row = candidates.sort_values("dept_dt", ascending=False).iloc[0]
        partial = False

    def show(col):
        return _fmt(row.get(col))

    steps = []
    for d in DERIVATIONS:
        steps.append({
            "key": d["key"],
            "label": d["label"],
            "unit": d["unit"],
            "formula": d["formula"],
            "value": show(d["key"]),
            # the raw values that went in, so the arithmetic can be redone by eye
            "inputs": {k: show(_COLUMN_ALIAS.get(k, k)) for k in d["inputs"]},
        })

    return {
        "available": True,
        "partial": partial,
        "trip_id": _fmt(row.get("trip_id")),
        "lane": show("lane"),
        "transporter": show("transporter"),
        "trip_class": show("trip_class") or show("s_trip_class"),
        "raw": {
            "dt_booking": show("booking_dt"),
            "dt_trip_start": show("dept_dt"),
            "dt_trip_eta": show("eta_dt"),
            "dt_trip_ata": show("ata_dt"),
            "dt_ata_out": show("ata_out_dt"),
            "s_delivery_status": show("delivery_status"),
            "d_distance_travelled_km": show("distance_km"),
            "i_speed_violation": show("speed_violations"),
            "d_uptime_pct": show("gps_uptime"),
            "s_service_provider": show("own_market"),
        },
        "steps": steps,
    }


# A few derivation inputs are named for the DB column but land in the frame
# under a friendlier name. Mapped here so the worked example can show the value.
_COLUMN_ALIAS = {
    "i_transit_time_min": "transit_hours",
    "i_detention_min": "detention_hours",
    "i_plant_vivo_min": "plant_vivo_hours",
    "i_moving_time_min": "run_hours",
    "i_delivery_delta_min": "delivery_delta_hours",
    "i_works_detention_min": "works_detention_hours",
    "i_geofence_tail_min": "geofence_tail_hours",
    "dt_booking": "booking_dt",
    "dt_trip_start": "dept_dt",
    "dt_trip_eta": "eta_dt",
    "dt_trip_ata": "ata_dt",
    "dt_geofence_out": "geofence_tail_hours",
    "d_distance_travelled_km": "distance_km",
    "s_delivery_status": "delivery_status",
    "s_asset_type": "vehicle_type",
    "s_service_provider": "own_market",
    "s_trans_name": "transporter",
    "s_org_node_name": "origin",
    "s_dest_node_name": "destination",
    "i_cne_pin": "pin_code",
}


# ----------------------------------------------------------------------
# What is actually populated
# ----------------------------------------------------------------------

# Every figure downstream is measured over whatever subset of trips carries its
# input, so the fill rate is not a footnote — it is the denominator.
COVERAGE_COLUMNS = [
    ("is_on_time", "On-time verdict"),
    ("transit_hours", "Transit time"),
    ("planned_transit_hours", "Promised ETA"),
    ("plant_vivo_hours", "Detention at origin"),
    ("detention_hours", "Detention at destination"),
    ("geofence_tail_hours", "GPS-confirmed plant exit"),
    ("distance_km", "Distance"),
    ("avg_speed_kmph", "Average speed"),
    ("gps_uptime", "GPS uptime"),
    ("own_market", "Ownership (raw field)"),
]


def field_coverage(df: pd.DataFrame) -> dict:
    """Fill rate per input, overall and split by feed lane.

    The split is the whole story: the two lanes report almost disjoint metric
    sets, so a fleet-wide "41% coverage" is really "one lane reports this and
    the other never does".
    """
    if df.empty:
        return {"total": 0, "rows": [], "lanes": []}

    lanes = ([str(x) for x in df["trip_class"].dropna().unique()]
             if "trip_class" in df.columns else [])
    rows = []
    for col, label in COVERAGE_COLUMNS:
        if col not in df.columns:
            continue
        n = int(df[col].notna().sum())
        entry = {
            "column": col, "label": label,
            "measured": n, "total": int(len(df)),
            "pct": round(100.0 * n / len(df), 1),
            "by_lane": {},
        }
        for lane in lanes:
            part = df[df["trip_class"] == lane]
            if len(part):
                entry["by_lane"][lane] = {
                    "measured": int(part[col].notna().sum()),
                    "total": int(len(part)),
                    "pct": round(100.0 * part[col].notna().sum() / len(part), 1),
                }
        rows.append(entry)
    return {"total": int(len(df)), "rows": rows, "lanes": lanes}


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def manual(conn, f: dict | None = None) -> dict:
    """The whole manual, with the worked example drawn from the live data."""
    df = apply_filters(load_df(conn), f or {})
    return {
        "feed": {
            "trip_fields": [
                {"column": c, "json_keys": k, "parser": p, "note": n}
                for c, k, p, n in TRIP_FIELDS
            ],
            "metric_fields": [
                {"column": c, "json_keys": k, "parser": p, "note": n}
                for c, k, p, n in METRIC_FIELDS
            ],
            "parsers": [
                {"name": n, "examples": e, "note": w} for n, e, w in PARSERS
            ],
        },
        "derivations": DERIVATIONS,
        "example": worked_example(df),
        "coverage": field_coverage(df),
        # Asked for repeatedly and not derivable. Carried here as well as on the
        # lane report so the answer is in the manual, where someone looking for
        # the commodity calculation will actually go looking for it.
        "not_derivable": [
            {"name": "Commodity / material",
             "reason": _COMMODITY_NOTE["reason"],
             "needed": _COMMODITY_NOTE["needed"]},
        ],
    }
