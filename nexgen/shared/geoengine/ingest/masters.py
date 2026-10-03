"""Load the eTrans geofence master into this module's database.

Three files arrive together:

    TBL_SITE_GEO        the site master     -- 4,957 rows, i_site_id unique
    TBL_SITE_GEO_DTLS   the vertices        -- 65,998 rows, ordered by i_seq
    tbl_site_polygon    a denormalised snapshot of the ring per site

Which one is authoritative
--------------------------
`TBL_SITE_GEO_DTLS` is, because it is the normalised form and carries the
per-vertex `c_is_active` flag. `tbl_site_polygon` is a materialised snapshot
(every row stamped with the same `dt_created`) and is already stale in at
least one place -- site 7630's snapshot disagrees with its own vertices. It is
therefore loaded as a *cross-check*, not as input: every compiled ring is
compared against it and disagreements are recorded rather than silently
resolved.

Which vertices form the ring
----------------------------
Active ones -- except that 359 sites have every vertex flagged `N` while still
carrying a single coherent ring, and the source system's own snapshot job
ignores the flag and uses them. Reading `c_is_active='N'` as "deleted" would
therefore drop 359 real fences that eTrans itself considers live. The rule
that matches the source system's behaviour, and is what is implemented here:

    ring = active vertices        if there are at least 3
           all vertices           if there are at least 3
           (rejected)             otherwise

Sites taking the fallback are flagged `all_vertices_inactive` on the fence row
so a report can disclose which verdicts rest on it. Whether a fence is *used*
is a separate question, answered by the site-level active flag, not this one.

Nothing is repaired
-------------------
A ring with a vertex outside the plausible coordinate range is rejected, not
clamped -- one bad vertex turns a 300 m works into a bounding box spanning a
hemisphere, which would poison the spatial index for every other fence. A
self-intersecting ring is loaded but flagged. Both land in `geo_import_reject`
with a reason, so "why is site X not detected" is a query, not a debugging
session.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from nexgen.shared.geoengine import geometry as G
from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import geo_session

logger = logging.getLogger(__name__)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# Plausible extent for Indian operations, with generous margin. A vertex
# outside this is corrupt, not merely remote.
#
# The western bound is deliberately loose. The client maintains two RESTRICTED
# fences named ARABIAN SEA at 65.75E, which is offshore -- they are not bad
# data, they are sanity fences: a truck reported inside one has a broken
# receiver, and that is worth detecting. A tighter bound would silently
# discard the very fences designed to catch bad GPS.
LAT_MIN, LAT_MAX = 5.0, 38.5
LON_MIN, LON_MAX = 60.0, 98.5

_POINT_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")

# Site types that carry a rule beyond "we want to know when a truck is here".
_CATEGORY = {
    "RED ZONE SITE": "restricted",
    "RESTRICTED SITE": "restricted",
    "HIGH RISK ZONE": "high_risk",
}

_ORACLE_DATE_FORMATS = ("%d-%b-%y %H:%M:%S.%f", "%d-%b-%Y %H:%M:%S.%f",
                        "%d-%b-%y %H:%M:%S", "%d-%b-%Y %H:%M:%S")


def _parse_dt(raw: str | None):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _ORACLE_DATE_FORMATS:
        try:
            return datetime.strptime(raw.upper(), fmt)
        except ValueError:
            continue
    return None


def _parse_int(raw, default=None):
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        return list(csv.DictReader(fh))


class Rejects:
    """Accumulates refusals so they are written once, not row by row."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def add(self, entity: str, site_id, reason: str, detail: str = "") -> None:
        self.rows.append((entity, site_id, reason, str(detail)[:500]))

    def __len__(self) -> int:
        return len(self.rows)

    def by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, _, reason, _ in self.rows:
            out[reason] = out.get(reason, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Ring construction
# ---------------------------------------------------------------------------

def _valid_coords(ring: list[tuple[float, float]]) -> str | None:
    for lat, lon in ring:
        if not (LAT_MIN <= lat <= LAT_MAX):
            return f"latitude {lat} outside [{LAT_MIN},{LAT_MAX}]"
        if not (LON_MIN <= lon <= LON_MAX):
            return f"longitude {lon} outside [{LON_MIN},{LON_MAX}]"
    return None


def build_rings(vertex_rows: list[dict], rejects: Rejects) -> dict[int, dict]:
    """Group vertex rows into one ring per site, applying the fallback rule."""
    active: dict[int, list] = {}
    every: dict[int, list] = {}

    for r in vertex_rows:
        sid = _parse_int(r.get("i_site_id"))
        seq = _parse_int(r.get("i_seq"))
        if sid is None or seq is None:
            rejects.add("vertex", sid, "unparseable_key", str(r)[:200])
            continue
        try:
            lat = float(r["i_lat"])
            lon = float(r["i_long"])
        except (KeyError, TypeError, ValueError):
            rejects.add("vertex", sid, "unparseable_coord", str(r)[:200])
            continue
        every.setdefault(sid, []).append((seq, lat, lon))
        if (r.get("c_is_active") or "").strip().upper() == "Y":
            active.setdefault(sid, []).append((seq, lat, lon))

    rings: dict[int, dict] = {}
    for sid in every:
        for source, pool in (("active", active.get(sid, [])), ("all_vertices_inactive", every[sid])):
            pool = sorted(pool)
            ring = G.clean_ring([(lat, lon) for _seq, lat, lon in pool])
            if len(ring) >= 3:
                rings[sid] = {"ring": ring, "source": source,
                              "raw_count": len(pool)}
                break
        else:
            n_act = len(active.get(sid, []))
            rejects.add("fence", sid, "degenerate_ring",
                        f"active={n_act} total={len(every[sid])} -- fewer than 3 distinct vertices")
    return rings


def read_snapshot(path: Path) -> dict[int, list[tuple[float, float]]]:
    """The denormalised polygon file, parsed for cross-checking only."""
    out: dict[int, list[tuple[float, float]]] = {}
    for r in _read_csv(path):
        sid = _parse_int(r.get("i_site_id"))
        if sid is None:
            continue
        pts = _POINT_RE.findall(r.get("s_lat_long") or "")
        out[sid] = [(float(a), float(b)) for a, b in pts]
    return out


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------

def import_masters(masters_dir: Path | None = None, truncate: bool = True) -> dict:
    """Load all three files. Returns the summary that is also stored on the run."""
    md = Path(masters_dir or settings.masters_dir)
    site_path = md / settings.site_csv
    dtls_path = md / settings.site_dtls_csv
    snap_path = md / settings.site_polygon_csv

    for p in (site_path, dtls_path):
        if not p.exists():
            raise FileNotFoundError(p)

    rejects = Rejects()
    started = datetime.now()

    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO geo_import_run (dt_started, s_source, s_status) VALUES (%s,%s,'running')",
                (started, str(md)),
            )
            import_id = cur.lastrowid
        conn.commit()

    try:
        # -- sites ----------------------------------------------------------
        site_rows = _read_csv(site_path)
        sites: dict[int, dict] = {}
        for r in site_rows:
            sid = _parse_int(r.get("i_site_id"))
            if sid is None:
                rejects.add("site", None, "unparseable_site_id", str(r)[:200])
                continue
            if sid in sites:
                rejects.add("site", sid, "duplicate_site_id", "")
                continue
            stype = (r.get("s_type") or "").strip() or None
            sites[sid] = {
                "i_site_id": sid,
                "i_entity_id": _parse_int(r.get("i_entity_id")),
                "s_site_name": (r.get("s_site_name") or "").strip()[:255] or f"SITE {sid}",
                "s_type": stype,
                "s_category": _CATEGORY.get((stype or "").upper(), "normal"),
                "i_max_speed": _parse_int(r.get("i_max_speed")),
                "i_tolerance": _parse_int(r.get("i_tolerance"), 0) or 0,
                "b_active": 1 if (r.get("c_is_active") or "").strip().upper() == "Y" else 0,
                "dt_created": _parse_dt(r.get("dt_created")),
                "s_created_by": (r.get("s_created_by") or "").strip()[:64] or None,
                "dt_modified": _parse_dt(r.get("dt_modified")),
                "s_modified_by": (r.get("s_modified_by") or "").strip()[:64] or None,
            }

        # -- vertices -> rings ------------------------------------------------
        vertex_rows = _read_csv(dtls_path)
        rings = build_rings(vertex_rows, rejects)

        # Geometry for a site the master never declared cannot be trusted or
        # named; it is reported, not invented into existence.
        orphans = sorted(set(rings) - set(sites))
        for sid in orphans:
            rejects.add("fence", sid, "orphan_geometry", "no row in the site master")
            rings.pop(sid, None)

        snapshot = read_snapshot(snap_path) if snap_path.exists() else {}

        # -- compile ----------------------------------------------------------
        fences: list[dict] = []
        vertices_out: list[tuple] = []
        for sid, info in rings.items():
            site = sites[sid]
            ring = info["ring"]

            bad = _valid_coords(ring)
            if bad:
                rejects.add("fence", sid, "coordinate_out_of_range", bad)
                continue

            lat = np.array([p[0] for p in ring], dtype=np.float64)
            lon = np.array([p[1] for p in ring], dtype=np.float64)
            clat, clon = G.ring_centroid(lat, lon)
            frame = G.LocalFrame(clat, clon)
            rx, ry = frame.to_m_arr(lat, lon)
            area = G.ring_area_m2(rx, ry)
            perim = G.ring_perimeter_m(rx, ry)

            if area <= 0.0:
                rejects.add("fence", sid, "zero_area",
                            f"{len(ring)} collinear vertices")
                continue

            notes: list[str] = []
            if info["source"] != "active":
                notes.append("all_vertices_inactive")

            crossing = G.find_self_intersection(rx, ry)
            if crossing:
                notes.append(f"self_intersects_edges_{crossing[0]}_{crossing[1]}")
                rejects.add("fence", sid, "self_intersecting",
                            f"edges {crossing[0]} and {crossing[1]} cross -- loaded but flagged")

            snap = snapshot.get(sid)
            if snap is not None:
                snap_clean = G.clean_ring(snap)
                if len(snap_clean) != len(ring):
                    notes.append("snapshot_vertex_count_differs")
                    rejects.add("fence", sid, "snapshot_mismatch",
                                f"vertices {len(ring)} vs snapshot {len(snap_clean)}")
                elif any(abs(a[0] - b[0]) > 1e-7 or abs(a[1] - b[1]) > 1e-7
                         for a, b in zip(ring, snap_clean)):
                    notes.append("snapshot_coords_differ")
                    rejects.add("fence", sid, "snapshot_mismatch",
                                "same vertex count, coordinates differ")

            fences.append({
                "i_site_id": sid,
                "s_site_name": site["s_site_name"],
                "s_type": site["s_type"],
                "s_category": site["s_category"],
                "i_max_speed": site["i_max_speed"],
                "i_tolerance": site["i_tolerance"],
                "b_active": site["b_active"],
                "i_vertices": len(ring),
                "d_min_lat": float(lat.min()), "d_max_lat": float(lat.max()),
                "d_min_long": float(lon.min()), "d_max_long": float(lon.max()),
                "d_centroid_lat": clat, "d_centroid_long": clon,
                "d_area_sqm": area, "d_perimeter_m": perim,
                "b_self_intersecting": 1 if crossing else 0,
                "s_geom_notes": (";".join(notes)[:255] or None),
                "wkt": G.ring_to_wkt(lat, lon),
            })
            for i, (la, lo) in enumerate(ring, start=1):
                vertices_out.append((sid, i, la, lo))

        for sid in sorted(set(sites) - set(rings) - {f["i_site_id"] for f in fences}):
            if sid not in rings:
                rejects.add("site", sid, "no_geometry", "site master row has no vertices")

        summary = _write(import_id, sites, vertices_out, fences, rejects, truncate)
        summary["orphan_geometry_sites"] = orphans

        with geo_session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE geo_import_run
                          SET dt_finished=%s, i_sites_read=%s, i_sites_loaded=%s,
                              i_vertices_read=%s, i_vertices_loaded=%s,
                              i_fences_built=%s, i_rejected=%s,
                              s_status='ok', j_summary=%s
                        WHERE i_import_id=%s""",
                    (datetime.now(), len(site_rows), len(sites), len(vertex_rows),
                     len(vertices_out), len(fences), len(rejects),
                     json.dumps(summary, default=str), import_id),
                )
            conn.commit()

        summary["import_id"] = import_id
        return summary

    except Exception as exc:
        with geo_session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE geo_import_run SET dt_finished=%s, s_status='failed', s_error=%s WHERE i_import_id=%s",
                    (datetime.now(), f"{type(exc).__name__}: {exc}"[:4000], import_id),
                )
            conn.commit()
        raise


def _write(import_id: int, sites: dict, vertices: list, fences: list,
           rejects: Rejects, truncate: bool) -> dict:
    """Persist the compiled master. One transaction, so a failure leaves the
    previous master intact rather than a half-replaced one."""
    with geo_session() as conn:
        with conn.cursor() as cur:
            if truncate:
                cur.execute("DELETE FROM geo_fence")
                cur.execute("DELETE FROM geo_site_vertex")
                cur.execute("DELETE FROM geo_site")

            cur.executemany(
                """INSERT INTO geo_site
                   (i_site_id,i_entity_id,s_site_name,s_type,s_category,i_max_speed,
                    i_tolerance,b_active,dt_created,s_created_by,dt_modified,s_modified_by)
                   VALUES (%(i_site_id)s,%(i_entity_id)s,%(s_site_name)s,%(s_type)s,
                           %(s_category)s,%(i_max_speed)s,%(i_tolerance)s,%(b_active)s,
                           %(dt_created)s,%(s_created_by)s,%(dt_modified)s,%(s_modified_by)s)
                   ON DUPLICATE KEY UPDATE s_site_name=VALUES(s_site_name)""",
                list(sites.values()),
            )

            for i in range(0, len(vertices), 5000):
                cur.executemany(
                    "INSERT INTO geo_site_vertex (i_site_id,i_seq,d_lat,d_long) VALUES (%s,%s,%s,%s)",
                    vertices[i:i + 5000],
                )

            built = 0
            for f in fences:
                try:
                    cur.execute(
                        """INSERT INTO geo_fence
                           (i_site_id,s_site_name,s_type,s_category,i_max_speed,i_tolerance,
                            b_active,i_vertices,d_min_lat,d_max_lat,d_min_long,d_max_long,
                            d_centroid_lat,d_centroid_long,d_area_sqm,d_perimeter_m,
                            b_self_intersecting,s_geom_notes,g_poly)
                           VALUES (%(i_site_id)s,%(s_site_name)s,%(s_type)s,%(s_category)s,
                                   %(i_max_speed)s,%(i_tolerance)s,%(b_active)s,%(i_vertices)s,
                                   %(d_min_lat)s,%(d_max_lat)s,%(d_min_long)s,%(d_max_long)s,
                                   %(d_centroid_lat)s,%(d_centroid_long)s,%(d_area_sqm)s,
                                   %(d_perimeter_m)s,%(b_self_intersecting)s,%(s_geom_notes)s,
                                   ST_GeomFromText(%(wkt)s, 0))""",
                        f,
                    )
                    built += 1
                except Exception as exc:
                    # MySQL refusing the WKT is itself a finding about the
                    # client's geometry, so it is recorded like any other.
                    rejects.add("fence", f["i_site_id"], "mysql_rejected_geometry",
                                f"{type(exc).__name__}: {exc}")

            if rejects.rows:
                cur.executemany(
                    """INSERT INTO geo_import_reject
                       (i_import_id,s_entity,i_site_id,s_reason,s_detail)
                       VALUES (%s,%s,%s,%s,%s)""",
                    [(import_id, e, s, r, d) for e, s, r, d in rejects.rows],
                )
        conn.commit()

    return {
        "sites_loaded": len(sites),
        "vertices_loaded": len(vertices),
        "fences_built": built,
        "rejected": len(rejects),
        "reject_reasons": rejects.by_reason(),
        "fences_active": sum(1 for f in fences if f["b_active"]),
        "fences_flagged": sum(1 for f in fences if f["s_geom_notes"]),
    }
