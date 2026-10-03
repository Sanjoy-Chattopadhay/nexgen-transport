"""Import client-supplied geofences.

Why this exists
---------------
Everything else in this module *derives* fences from the GPS trail, because
the eTrans feed ships node names and no geometry. That derivation is a
stand-in, not the answer: it can only ever be as good as the pings, and on a
lane whose trackers die at the origin it is actively wrong until the gazetteer
rescues it.

The client holds the real geofences. When they hand them over, they land here
and the derived ones step aside. Nothing downstream changes — the index, the
crossing detector, the detention and coverage reports all read `geofences` and
do not care where a row came from.

The contract
------------
Imported rows are written with `b_manual = 1` and `s_source = 'client'`, which
means `seed_from_anchors()` will never touch them again. That is the whole
point: once a real fence is known, no amount of re-deriving may overwrite it.

Accepted input
--------------
CSV or JSON, one fence per row/object. Column names are matched
case-insensitively and several spellings are accepted, because a client
export will not use our column names:

    name / node / location / s_name        -> the node name (required)
    lat / latitude / d_lat                 -> decimal degrees (required)
    lon / lng / long / longitude / d_long  -> decimal degrees (required)
    radius_m / radius / geofence_radius    -> metres (optional, default 3000)
    role                                   -> origin|destination|yard (optional)

`name` must match the trip rows' node name, since that is the only join
available -- there is no shared id between the feed's trips and its geofences.
Names are folded with `normalise_key`, so case and padding do not matter, but a
genuinely different spelling will not match and is reported as unmatched rather
than imported into a fence nothing can ever use.

Validation, and why it refuses rather than warns
------------------------------------------------
A geofence with a wrong sign or swapped lat/lon does not fail loudly, it
silently reports every truck as absent. So a row is rejected outright if the
coordinates are out of range, the radius is absurd, or the point falls outside
India's bounding box -- the last one catches swapped lat/lon, which is the
single most common defect in a hand-built location export and is otherwise
undetectable.
"""

from __future__ import annotations

import csv
import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from nexgen.shared.legacy_db import get_connection
from .index import haversine_m, normalise_key

logger = logging.getLogger(__name__)

# Generous box around India, including the islands. Its job is to catch
# swapped lat/lon (a Jamshedpur fence written 86.2, 22.8 lands in Somalia) and
# sign errors, not to police borders.
INDIA_BBOX = (6.0, 68.0, 37.5, 97.5)   # lat_min, lon_min, lat_max, lon_max

MIN_RADIUS_M = 50
MAX_RADIUS_M = 50_000
DEFAULT_RADIUS_M = 3000
VALID_ROLES = {"origin", "destination", "yard"}

# Aliases are written in canonical form -- lower case, letters and digits only
# (see `_canon`). "Location Name", "location_name" and "LOCATION-NAME" all fold
# to "locationname", so one entry covers every spelling of a header.
#
# Order matters: the first alias that matches a column wins, so the most
# specific spellings come first.
_ALIASES = {
    "name": ("name", "node", "nodename", "location", "locationname",
             "sname", "snodename", "destination", "destinationname",
             "consignee", "consigneename", "site", "sitename",
             "customer", "customername", "plant", "plantname",
             "geofencename", "fencename", "place", "placename"),
    "lat": ("lat", "latitude", "lattitude", "dlat", "geolat", "geofencelat",
            "centerlat", "centrelat", "centerlatitude", "y"),
    "lon": ("lon", "lng", "long", "longitude", "dlong", "geolon", "geolong",
            "geofencelon", "geofencelong", "centerlon", "centrelon",
            "centerlongitude", "x"),
    "radius_m": ("radiusm", "radius", "geofenceradius", "geofenceradiusm",
                 "radiusmeters", "radiusmetres", "radiusinmeters",
                 "radiusinmetres", "iradiusm", "fenceradius", "buffer",
                 "bufferm"),
    "role": ("role", "type", "srole", "fencetype", "locationtype",
             "geofencetype", "nodetype", "category"),
}

# The bare tokens a header may *end with* when no alias matched exactly. This
# is the last resort, so it is deliberately short: "Geo Fence Radius (m)" folds
# to "geofenceradiusm" and is caught above, but an export nobody predicted --
# "Unloading Point Latitude" -> "unloadingpointlatitude" -- still resolves.
# `name` is absent on purpose: almost any header ends in something name-like
# and a wrong guess there silently fences the wrong place.
_SUFFIX_TOKENS = {
    "lat": ("latitude", "lat"),
    "lon": ("longitude", "long", "lng", "lon"),
    "radius_m": ("radius",),
}


def _canon(header: object) -> str:
    """Fold a column header to letters and digits only.

    A client export does not use our column names. It writes "Location Name",
    "Geofence Radius (m)", "GEO_FENCE-LAT" or "Latitude ". Matching the exact
    string rejects every one of those -- measured on a realistic sample export,
    all 10 rows failed with "no recognisable name column" purely because the
    headers had spaces in them. Folding both sides to "locationname",
    "geofenceradiusm" and "geofencelat" makes the alias table match what people
    actually send.
    """
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


@dataclass
class ImportReport:
    inserted: int = 0
    updated: int = 0
    rejected: list[dict] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    columns: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "columns_detected": self.columns,
            "rejected_count": len(self.rejected),
            "rejected": self.rejected[:50],
            "unmatched_count": len(self.unmatched),
            "unmatched_node_names": self.unmatched[:50],
            "note": (
                "unmatched = the fence was imported but no trip uses that node "
                "name, so nothing will ever be measured against it. Check the "
                "spelling against tta_trips.s_dest_node_name."
            ),
        }


def _match_column(headers: list[str], field_name: str) -> str | None:
    """Return the original header that supplies `field_name`, or None.

    Two passes, most-confident first: an exact match on the canonical form,
    then -- for the coordinate and radius fields only -- a suffix match, so an
    unforeseen header like "Unloading Point Latitude" still resolves. Name is
    never suffix-matched; guessing wrong there fences the wrong place silently.
    """
    canon = {h: _canon(h) for h in headers}

    for alias in _ALIASES[field_name]:
        for original, folded in canon.items():
            if folded == alias:
                return original

    for token in _SUFFIX_TOKENS.get(field_name, ()):
        for original, folded in canon.items():
            if folded.endswith(token):
                return original
    return None


def detect_columns(rows: list[dict]) -> dict[str, str | None]:
    """Map our field names onto the client's headers.

    Returned to the caller and surfaced in the UI, because a silent guess about
    which column held the latitude is exactly the kind of thing that produces a
    confident, wrong answer. The operator sees the mapping before anything is
    written.
    """
    headers = [str(k) for r in rows[:1] for k in r.keys() if k is not None]
    return {f: _match_column(headers, f) for f in _ALIASES}


def _pick(row: dict, field_name: str, mapping: dict[str, str | None] | None = None):
    """Read one field out of a client row.

    `mapping` is computed once per file by `detect_columns`; when it is absent
    (a single ad-hoc row, e.g. from the API) the columns are matched per row.
    """
    if mapping is None:
        mapping = {field_name: _match_column(
            [str(k) for k in row.keys() if k is not None], field_name)}
    column = mapping.get(field_name)
    if column is None:
        return None
    value = row.get(column)
    if value is None or str(value).strip() == "":
        return None
    return value


def _validate(raw: dict, mapping: dict[str, str | None] | None = None) -> tuple[dict | None, str | None]:
    """Return (clean fence, None) or (None, reason)."""
    name = _pick(raw, "name", mapping)
    if not name:
        return None, "no recognisable name column"
    key = normalise_key(str(name))
    if not key:
        return None, "empty name"

    try:
        lat = float(_pick(raw, "lat", mapping))
        lon = float(_pick(raw, "lon", mapping))
    except (TypeError, ValueError):
        return None, "lat/lon missing or not numeric"

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, f"lat/lon out of range ({lat}, {lon})"

    lat_min, lon_min, lat_max, lon_max = INDIA_BBOX
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        swapped = (lat_min <= lon <= lat_max and lon_min <= lat <= lon_max)
        return None, (
            f"({lat}, {lon}) is outside India"
            + (" -- lat and lon look swapped" if swapped else "")
        )

    radius_raw = _pick(raw, "radius_m", mapping)
    try:
        radius = int(float(radius_raw)) if radius_raw is not None else DEFAULT_RADIUS_M
    except (TypeError, ValueError):
        return None, f"radius not numeric ({radius_raw!r})"
    if not (MIN_RADIUS_M <= radius <= MAX_RADIUS_M):
        return None, f"radius {radius} m outside [{MIN_RADIUS_M}, {MAX_RADIUS_M}]"

    role = str(_pick(raw, "role", mapping) or "destination").strip().lower()
    if role not in VALID_ROLES:
        return None, f"role {role!r} not one of {sorted(VALID_ROLES)}"

    return {
        "key": key, "name": str(name).strip(), "role": role,
        "lat": lat, "lon": lon, "radius_m": radius,
    }, None


def load_rows(path: str | Path) -> list[dict]:
    """Read CSV or JSON into a list of raw dicts."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")   # -sig: Excel exports carry a BOM
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            # Accept {"geofences": [...]} as well as a bare list.
            for k in ("geofences", "fences", "data", "results"):
                if isinstance(data.get(k), list):
                    return data[k]
            return [data]
        return data
    return list(csv.DictReader(text.splitlines()))


def import_fences(rows: list[dict], conn=None, deactivate_derived: bool = False) -> dict:
    """Write client fences, replacing any derived fence for the same node.

    Args:
        deactivate_derived: also switch off every remaining derived fence, for
            the cutover where the client's set is authoritative and complete.
            Off by default -- a partial delivery must not blind the rest.
    """
    own = conn is None
    conn = conn or get_connection()
    rep = ImportReport()
    try:
        mapping = detect_columns(rows)
        rep.columns = mapping
        clean: list[dict] = []
        for i, raw in enumerate(rows, start=1):
            fence, why = _validate(raw, mapping)
            if why:
                rep.rejected.append({"row": i, "reason": why, "data": raw})
            else:
                clean.append(fence)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT UPPER(TRIM(s_dest_node_name)) k FROM tta_trips "
                "UNION SELECT DISTINCT UPPER(TRIM(s_org_node_name)) FROM tta_trips"
            )
            known = {r["k"] for r in cur.fetchall()}

            for f in clean:
                if f["key"] not in known:
                    rep.unmatched.append(f["name"])

                cur.execute(
                    "SELECT i_fence_id FROM geofences WHERE s_key=%s AND s_role=%s",
                    (f["key"], f["role"]),
                )
                if cur.fetchone():
                    cur.execute(
                        """UPDATE geofences
                              SET s_name=%s, d_lat=%s, d_long=%s, i_radius_m=%s,
                                  b_manual=1, b_active=1, s_source='client'
                            WHERE s_key=%s AND s_role=%s""",
                        (f["name"], f["lat"], f["lon"], f["radius_m"],
                         f["key"], f["role"]),
                    )
                    rep.updated += 1
                else:
                    cur.execute(
                        """INSERT INTO geofences
                             (s_key, s_name, s_role, d_lat, d_long, i_radius_m,
                              b_manual, b_active, s_source)
                           VALUES (%s,%s,%s,%s,%s,%s,1,1,'client')""",
                        (f["key"], f["name"], f["role"], f["lat"], f["lon"],
                         f["radius_m"]),
                    )
                    rep.inserted += 1

            if deactivate_derived:
                cur.execute(
                    "UPDATE geofences SET b_active=0 WHERE s_source <> 'client'"
                )
                logger.warning(
                    "deactivated every derived fence -- only client fences are live"
                )
        conn.commit()
        logger.info("geofence import: %s", rep.as_dict())
        return rep.as_dict()
    finally:
        if own:
            conn.close()


def import_file(path: str | Path, conn=None, deactivate_derived: bool = False) -> dict:
    return import_fences(load_rows(path), conn, deactivate_derived)



def _near_misses(name: str, known: set[str], limit: int = 3) -> list[str]:
    """Node names in the feed that this client name probably meant.

    The only join between a client fence and a trip is the node name, and the
    two sides are typed by different people: the client sends "PITHAMPUR", the
    feed carries "PITHAMPUR (M.P.)". Reporting that as an unexplained
    non-match leaves the operator to grep for it. Reporting it as "did you mean
    PITHAMPUR (M.P.)?" is the difference between a fence that works and a fence
    that is stored and never used.

    Substring hits come first -- a suffix like a state code is the common case
    and is a stronger signal than edit distance -- then fuzzy matches.
    """
    key = normalise_key(name)
    if not key:
        return []
    # s_dest_node_name is nullable, so `known` can carry a None.
    candidates = {k for k in known if k}
    contained = sorted(
        (k for k in candidates if key in k or k in key),
        key=lambda k: abs(len(k) - len(key)),
    )
    fuzzy = difflib.get_close_matches(key, candidates, n=limit, cutoff=0.72)
    out: list[str] = []
    for candidate in contained + fuzzy:
        if candidate not in out:
            out.append(candidate)
    return out[:limit]

def preview(rows: list[dict], conn=None) -> dict:
    """Validate a client file and report what importing it would do. Writes nothing.

    The import itself is safe -- bad rows are rejected, not written -- but
    "safe" is not the same as "understood". Before an operator commits the
    client's geometry they need to see three things this returns and the
    import report cannot:

      * which of their columns we read as what (`columns_detected`), because a
        silent guess about which column held the latitude is how you get a
        confident, wrong fence;
      * how far each fence sits from the fence it replaces (`shift_km`), which
        is the number that tells you whether the derived stand-in was any good;
      * which node names match no trip (`unmatched`), i.e. fences that will be
        stored and then never measured against anything.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        mapping = detect_columns(rows)
        accepted, rejected = [], []
        for i, raw in enumerate(rows, start=1):
            fence, why = _validate(raw, mapping)
            if why:
                rejected.append({"row": i, "reason": why, "data": raw})
            else:
                accepted.append({**fence, "row": i})

        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT UPPER(TRIM(s_dest_node_name)) k FROM tta_trips "
                "UNION SELECT DISTINCT UPPER(TRIM(s_org_node_name)) FROM tta_trips"
            )
            known = {r["k"] for r in cur.fetchall()}

            cur.execute(
                """SELECT UPPER(TRIM(s_dest_node_name)) k, COUNT(*) n
                     FROM tta_trips GROUP BY k
                   UNION ALL
                   SELECT UPPER(TRIM(s_org_node_name)) k, COUNT(*) n
                     FROM tta_trips GROUP BY k"""
            )
            trips_per_key: dict[str, int] = {}
            for r in cur.fetchall():
                trips_per_key[r["k"]] = trips_per_key.get(r["k"], 0) + (r["n"] or 0)

            cur.execute(
                "SELECT s_key, s_role, s_name, d_lat, d_long, i_radius_m, s_source "
                "FROM geofences WHERE b_active=1"
            )
            current = {(r["s_key"], r["s_role"]): r for r in cur.fetchall()}

        for f in accepted:
            existing = current.get((f["key"], f["role"]))
            f["matches_trips"] = f["key"] in known
            f["trips_affected"] = trips_per_key.get(f["key"], 0)
            f["did_you_mean"] = ([] if f["matches_trips"]
                                 else _near_misses(f["name"], known))
            if existing is None:
                f["action"] = "new"
                f["replaces_source"] = None
                f["shift_km"] = None
                f["radius_change_m"] = None
            else:
                f["action"] = "replaces"
                f["replaces_source"] = existing["s_source"]
                f["shift_km"] = round(
                    haversine_m(f["lat"], f["lon"],
                                float(existing["d_lat"]), float(existing["d_long"])) / 1000.0,
                    2,
                )
                f["radius_change_m"] = f["radius_m"] - int(existing["i_radius_m"])

        # A fence that moves a long way is the interesting one: either the
        # derived stand-in was badly wrong (the SANAND case, 1,417 km out) or
        # the client's row is. Either way an operator must look before writing.
        big_moves = sorted(
            (f for f in accepted if f["shift_km"] and f["shift_km"] > 25),
            key=lambda f: -f["shift_km"],
        )
        return {
            "columns_detected": mapping,
            "unreadable_fields": [k for k, v in mapping.items()
                                  if v is None and k in ("name", "lat", "lon")],
            "rows_read": len(rows),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
            "new_count": sum(1 for f in accepted if f["action"] == "new"),
            "replaces_count": sum(1 for f in accepted if f["action"] == "replaces"),
            "unmatched": [
                {"name": f["name"], "did_you_mean": f["did_you_mean"]}
                for f in accepted if not f["matches_trips"]
            ],
            "trips_affected": sum(f["trips_affected"] for f in accepted),
            "large_moves": big_moves[:25],
            "note": (
                "Nothing was written. `large_moves` are fences that would jump "
                ">25 km from the fence they replace -- check those before importing."
            ),
        }
    finally:
        if own:
            conn.close()
