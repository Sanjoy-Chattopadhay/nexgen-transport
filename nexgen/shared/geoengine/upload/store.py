"""Uploads, their fixes, and one compressed document per pipeline step.

An artifact is stored as zlib-compressed JSON rather than as rows in a table
per step. There are two dozen steps, their shapes have nothing in common, and
every read of one is "give me that whole step" -- so a table per step would be
two dozen schemas for no query anyone will ever write. One blob per step is
also exactly what the download endpoints need to serve, which is why the page
and the CSV a user checks it against cannot drift apart: they are the same
bytes, rendered twice.

The raw parsed fixes *are* stored as rows, because they are re-read whenever
the analysis is run again under different settings, and because they are the
one thing that must survive independently of how it was interpreted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import zlib
from datetime import date, datetime
from decimal import Decimal

from nexgen.shared.geoengine.config import DetectorConfig, FitConfig, settings
from nexgen.shared.geoengine.db import geo_session
from nexgen.shared.geoengine.upload import verify
from nexgen.shared.geoengine.upload.parse import ParseError, parse
from nexgen.shared.geoengine.upload.trace import Artifact, Tracer

logger = logging.getLogger(__name__)

MAX_BYTES = 80 * 1024 * 1024


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat(sep=" ") if isinstance(o, datetime) else o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "item"):                    # numpy scalar
        return o.item()
    if hasattr(o, "tolist"):                  # numpy array
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def dumps(doc) -> bytes:
    return zlib.compress(json.dumps(doc, default=_default).encode("utf-8"), 6)


def loads(blob: bytes):
    return json.loads(zlib.decompress(blob).decode("utf-8"))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def create(data: bytes, filename: str, label: str | None = None) -> int:
    """Parse a file and record it. Returns the upload id.

    Parsing happens before anything is written, so a file that cannot be read
    leaves no half-made upload behind -- except a `failed` row when it parsed
    far enough to be worth reporting.
    """
    if len(data) > MAX_BYTES:
        raise ParseError(f"file is {len(data)/1e6:,.0f} MB; the limit is {MAX_BYTES/1e6:,.0f} MB")
    sha = hashlib.sha256(data).hexdigest()
    parsed = parse(data, filename)
    pings = parsed["pings"]

    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO geo_upload
                   (s_name, s_label, s_sha256, i_bytes, s_asset_id, i_source_trip,
                    s_status, i_rows_read, i_rows_parsed, dt_first_ping, dt_last_ping,
                    j_columns)
                   VALUES (%s,%s,%s,%s,%s,%s,'parsing',%s,%s,%s,%s,%s)""",
                (filename[:255], (label or "")[:255] or None, sha, len(data),
                 parsed["asset_id"], parsed["trip_no"],
                 parsed["rows_read"], len(pings),
                 min(p["ts"] for p in pings), max(p["ts"] for p in pings),
                 json.dumps({
                     "format": parsed["format"],
                     "headers": parsed["headers"],
                     "mapping": {k: v for k, v in parsed["columns"].items()},
                     "dayfirst": parsed["dayfirst"],
                     "header_row": parsed["header_row"],
                 }, default=_default)),
            )
            upload_id = cur.lastrowid
            cur.executemany(
                """INSERT INTO geo_upload_ping
                   (i_upload_id, i_seq, i_row, dt_message, d_lat, d_long, i_speed)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                [(upload_id, p["seq"], p["row"], p["ts"], round(p["lat"], 7),
                  round(p["lon"], 7), p["speed"]) for p in pings],
            )
        conn.commit()

    _write_artifact(upload_id, 0, _read_artifact(filename, sha, data, parsed))
    return upload_id


def _read_artifact(filename: str, sha: str, data: bytes, parsed: dict) -> Artifact:
    cols = parsed["columns"]
    mapping = [[field, cols[field]["header"], cols[field]["index"] + 1, cols[field]["how"]]
               for field in ("ts", "lat", "lon", "speed", "asset", "trip")
               if field in cols]
    unused = [h for i, h in enumerate(parsed["headers"])
              if i not in {c["index"] for c in cols.values()}]
    sample = parsed["pings"][:8]
    return Artifact(
        "read", "Step 0 · Reading the file",
        what=f"{parsed['rows_read']:,} data rows read from a .{parsed['format']} file, "
             f"and each column matched to what the engine needs: when, where, how fast.",
        why="Every fleet system spells these columns differently, so the header row is "
            "matched against a list of known spellings rather than a fixed layout being "
            "demanded. The mapping it chose is shown here first, before any number is "
            "computed from it — a pipeline that read the wrong column would produce a "
            "confident and completely wrong answer, and this is the one step where that "
            "is easy to catch by eye.\n\n"
            "Nothing is cleaned here. Rows are not sorted, de-duplicated or filtered; "
            "that is all pipeline work and has to be visible as such. The only rows "
            "refused at this stage are those whose date or coordinates will not read as "
            "values at all — reported by spreadsheet row number, with the offending "
            "cell, so they can be found in the file.",
        how="headers squashed to lowercase letters and digits, exact match first, then "
            "longest contained spelling · dates: ISO, Excel serial, unix epoch, or "
            "d/m/y — the day/month order settled over the whole column, not per row",
        stats={
            "file": filename,
            "sha-256": sha[:16] + "…",
            "size (bytes)": len(data),
            "header row": parsed["header_row"],
            "data rows": parsed["rows_read"],
            "fixes parsed": len(parsed["pings"]),
            "rows unreadable": len(parsed["refused"]),
            "date order read as": "day/month/year" if parsed["dayfirst"] else "month/day/year",
            "vehicle named in the file": parsed["asset_id"] or "—",
            "trip number in the file": parsed["trip_no"] or "—",
            "columns not used": ", ".join(unused[:12]) or "none",
        },
        note="The sha-256 is of the exact bytes uploaded. Re-uploading the same file "
             "gives the same digest, so a result can always be tied back to the file "
             "that produced it.",
        columns=["field", "column in the file", "position", "matched"],
        rows=mapping,
        extra={
            "refused": {
                "columns": ["row", "why", "timestamp cell", "lat cell", "lon cell"],
                "rows": [[r["row"], r["reason"], r["ts"], r["lat"], r["lon"]]
                         for r in parsed["refused"][:2000]],
            },
            "sample": {
                "columns": ["row", "timestamp", "lat", "lon", "speed"],
                "rows": [[p["row"], p["ts"].isoformat(sep=" "), round(p["lat"], 6),
                          round(p["lon"], 6), p["speed"]] for p in sample],
            },
        },
    )


def _write_artifact(upload_id: int, seq: int, art: Artifact) -> None:
    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO geo_upload_artifact
                   (i_upload_id, s_key, i_seq, s_title, i_rows, m_data)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE i_seq=VALUES(i_seq), s_title=VALUES(s_title),
                       i_rows=VALUES(i_rows), m_data=VALUES(m_data)""",
                (upload_id, art["key"], seq, art["title"][:160], len(art["rows"]),
                 dumps(art)),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse(upload_id: int, detector: DetectorConfig | None = None,
            fit: FitConfig | None = None, use_osrm: bool | None = None) -> dict:
    """Run the pipeline over a stored upload and write every step.

    Re-runnable: it reads the parsed fixes back out of the database, so a
    different set of settings needs no new upload and the two results can be
    compared against the same input.
    """
    from nexgen.shared.geoengine.osrm.client import OsrmClient
    from nexgen.shared.geoengine.store import build_index, max_tolerance_m

    det = detector or settings.detector
    fitcfg = fit or settings.fit
    t0 = time.perf_counter()

    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM geo_upload WHERE i_upload_id=%s", (upload_id,))
            head = cur.fetchone()
            if not head:
                raise LookupError(f"no upload {upload_id}")
            cur.execute(
                """SELECT i_seq, i_row, dt_message, d_lat, d_long, i_speed
                     FROM geo_upload_ping WHERE i_upload_id=%s ORDER BY i_seq""",
                (upload_id,))
            pings = [{"seq": r["i_seq"], "row": r["i_row"], "ts": r["dt_message"],
                      "lat": float(r["d_lat"]), "lon": float(r["d_long"]),
                      "speed": r["i_speed"]} for r in cur.fetchall()]

    if not pings:
        raise LookupError(f"upload {upload_id} has no fixes")

    osrm = None
    if use_osrm is not False and settings.osrm and settings.osrm.enabled:
        osrm = OsrmClient(settings.osrm)

    index = build_index(active_only=True, pad_m=max_tolerance_m())
    tracer = Tracer(pings, index, det, fitcfg, osrm)
    out = tracer.run()

    # Verification runs last, against the result the page will show.
    with geo_session() as conn:
        checks = verify.identities(out["trail"], out["fit"], out["result"], len(pings))
        idx_check = verify.compare_index(out["fit"], index, det, out["candidates"])
        try:
            oracle = verify.oracle(out["fit"], index, conn)
        except Exception as exc:                            # noqa: BLE001
            logger.warning("oracle check unavailable: %s", exc)
            oracle = {"checked": 0, "verdict": f"unavailable: {exc}", "rows": []}

    failed = [c for c in checks if not c["pass"]]
    tracer._add(Artifact(
        "verify", "Step 24 · Checking the answer against something that is not itself",
        what="The same facts re-derived three other ways: by brute force with no index, "
             "by MySQL's own geometry engine, and by arithmetic that must hold whatever "
             "the geometry says.",
        why="Every number above comes out of one engine, and agreement between an engine "
            "and itself is not evidence.\n\n"
            "**Brute force** tests every admissible fix against every active fence with "
            "no bounding box, no chunking and no tree — the definition of the answer the "
            "index exists to compute faster. The index's entire claim is that it has no "
            "false negatives: a point inside a polygon is inside that polygon's box, so "
            "the box is always reported, and the extra boxes it reports are removed by "
            "the polygon test. If that claim were ever false, the two sets would differ "
            "here.\n\n"
            "**MySQL ST_Contains** is a different implementation by different people, "
            "reading a separately stored copy of the geometry. The engine runs off the "
            "vertex table; the WKT column is derived from it; checking one against the "
            "other is what makes a bug in either visible. Both are planar SRID 0, which "
            "is why the comparison means anything.\n\n"
            "**The identities** cannot be satisfied by a plausible-looking wrong answer: "
            "fixes read must equal fixes used plus fixes refused, every dwell must equal "
            "the span of real fixes it was measured over, the distance must recompute "
            "from the stored positions, and entries and exits must pair.",
        how="brute force: points_in_ring over every fence, unfiltered · oracle: "
            "ST_Contains on the fixes nearest fence boundaries, where an off-by-one in "
            "edge handling would show · identities: arithmetic",
        stats={
            "identity checks": len(checks),
            "identity checks passed": len(checks) - len(failed),
            "index false negatives": idx_check["missed_by_the_index"],
            "index candidates the polygon test removed":
                idx_check["false_positives_the_polygon_test_removed"],
            "brute-force polygon tests": idx_check["brute_force"]["polygon_tests"],
            "brute-force seconds": idx_check["brute_force"]["seconds"],
            "MySQL probes": oracle.get("checked", 0),
            "MySQL agreed": oracle.get("agreed", 0),
            "MySQL disagreed": oracle.get("disagreed", 0),
        },
        note=("Every check passed." if not failed and not idx_check["missed_by_the_index"]
              and not oracle.get("disagreed") else
              "At least one check did not pass — the rows below say which."),
        columns=["check", "expected", "got", "result", "why it must hold"],
        rows=[[c["check"], str(c["expected"]), str(c["got"]),
               "pass" if c["pass"] else "FAIL", c["why"]] for c in checks],
        extra={"index": idx_check, "oracle": oracle},
    ))

    seconds = round(time.perf_counter() - t0, 3)
    s = out["result"].summary
    fitr = out["fit"]
    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM geo_upload_artifact WHERE i_upload_id=%s AND s_key<>'read'",
                        (upload_id,))
            for i, art in enumerate(tracer.artifacts, start=1):
                cur.execute(
                    """INSERT INTO geo_upload_artifact
                       (i_upload_id, s_key, i_seq, s_title, i_rows, m_data)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE i_seq=VALUES(i_seq), s_title=VALUES(s_title),
                           i_rows=VALUES(i_rows), m_data=VALUES(m_data)""",
                    (upload_id, art["key"], i, art["title"][:160], len(art["rows"]),
                     dumps(art)))
            cur.execute(
                """UPDATE geo_upload SET s_status='ready', s_error=NULL,
                       i_pings_used=%s, i_visits=%s, i_places=%s, i_distinct_sites=%s,
                       i_violations=%s, d_distance_km=%s, s_quality=%s, d_seconds=%s,
                       j_params=%s, dt_analysed=NOW(), s_asset_id=COALESCE(s_asset_id,%s)
                     WHERE i_upload_id=%s""",
                (s.get("pings_used", 0), len(out["result"].visits), len(out["places"]),
                 s.get("distinct_sites", 0), len(out["result"].violations),
                 round(fitr.distance_m / 1000, 3), fitr.quality, seconds,
                 json.dumps({**det.as_dict(), **fitcfg.as_dict(),
                             "osrm": fitr.osrm_status,
                             "fences_indexed": len(index),
                             "timings": tracer.timings}, default=_default),
                 out["result"].asset_id, upload_id))
        conn.commit()

    return {"upload_id": upload_id, "seconds": seconds, "steps": len(tracer.artifacts),
            "quality": fitr.quality, "visits": len(out["result"].visits),
            "places": len(out["places"])}


def mark_failed(upload_id: int, error: str) -> None:
    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE geo_upload SET s_status='failed', s_error=%s "
                        "WHERE i_upload_id=%s", (str(error)[:500], upload_id))
        conn.commit()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def head(conn, upload_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM geo_upload WHERE i_upload_id=%s", (upload_id,))
        row = cur.fetchone()
    if row:
        for k in ("j_params", "j_columns"):
            if isinstance(row.get(k), (str, bytes)):
                try:
                    row[k] = json.loads(row[k])
                except ValueError:
                    row[k] = None
        if row.get("d_distance_km") is not None:
            row["d_distance_km"] = float(row["d_distance_km"])
    return row


def artifact(conn, upload_id: int, key: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT m_data FROM geo_upload_artifact WHERE i_upload_id=%s AND s_key=%s",
                    (upload_id, key))
        row = cur.fetchone()
    return loads(row["m_data"]) if row else None


def artifacts(conn, upload_id: int, with_rows: bool = True,
              max_rows: int | None = None) -> list[dict]:
    """Every step in page order.

    `max_rows` truncates the row block of each step for the overview request --
    the page shows the first page of each table and fetches or downloads the
    rest per step, so one response does not have to carry a 4,500-row trail
    twenty times over.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT s_key, i_seq, s_title, i_rows, m_data
                         FROM geo_upload_artifact WHERE i_upload_id=%s ORDER BY i_seq""",
                    (upload_id,))
        rows = cur.fetchall()
    out = []
    for r in rows:
        doc = loads(r["m_data"])
        doc["total_rows"] = r["i_rows"]
        if not with_rows:
            doc["rows"] = []
        elif max_rows is not None and len(doc.get("rows") or []) > max_rows:
            doc["rows"] = doc["rows"][:max_rows]
            doc["truncated"] = True
        out.append(doc)
    return out


def listing(conn, limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT i_upload_id, s_name, s_label, s_status, s_error, s_asset_id,
                      i_source_trip, i_rows_parsed, i_pings_used, i_visits, i_places,
                      i_distinct_sites, i_violations, d_distance_km, s_quality,
                      dt_first_ping, dt_last_ping, dt_uploaded, d_seconds
                 FROM geo_upload ORDER BY i_upload_id DESC LIMIT %s""", (limit,))
        rows = cur.fetchall()
    for r in rows:
        for k in ("d_distance_km", "d_seconds"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return rows


def delete(upload_id: int) -> bool:
    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM geo_upload WHERE i_upload_id=%s", (upload_id,))
            gone = cur.rowcount
        conn.commit()
    return bool(gone)
