"""A real trip out of the feed, as a spreadsheet to upload.

The point of the upload page is that somebody can check the engine end to end,
and checking needs a trip long enough to have something in it: a few hundred
kilometres, both ends inside real facilities, some standstill scatter, a spike
or two, a hole in the trail. `pick` finds one; `workbook` writes it.

The sheet is written in the plainest possible layout -- one header row, one
fix per row, the columns named as a fleet export names them -- because it is
also the worked example of what an upload should look like. The pipeline's own
parser has to be able to read it without any special case, and `tests` assert
that round trip.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# A trip worth demonstrating on: long enough to cross states, dense enough to
# show the standstill handling, with at least one facility at each end.
PICK_SQL = """
SELECT s.i_trip_no, s.s_asset_id, s.d_distance_km, s.i_pings_read, s.i_places,
       s.i_visits, s.i_spikes, s.i_stops, s.s_quality,
       m.s_origin, m.s_destination, m.s_trans_name, m.s_driver_name
  FROM geo_trip_summary s
  LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
 WHERE s.i_run_id = %s
   AND s.d_distance_km BETWEEN %s AND %s
   AND s.s_quality = 'good'
   AND s.i_places >= 4
   AND s.i_pings_read BETWEEN 800 AND 20000
 ORDER BY ABS(s.d_distance_km - %s), s.i_places DESC
 LIMIT 1
"""


def pick(conn, target_km: float = 500.0, spread: float = 60.0,
         run_id: int | None = None) -> dict | None:
    """The trip closest to `target_km` that is worth showing."""
    with conn.cursor() as cur:
        if run_id is None:
            cur.execute("""SELECT i_run_id FROM geo_run WHERE s_status='ok'
                            ORDER BY b_published DESC, i_run_id DESC LIMIT 1""")
            row = cur.fetchone()
            if not row:
                return None
            run_id = row["i_run_id"]
        cur.execute(PICK_SQL, (run_id, target_km - spread, target_km + spread, target_km))
        trip = cur.fetchone()
    if trip and trip.get("d_distance_km") is not None:
        trip["d_distance_km"] = float(trip["d_distance_km"])
    return trip


def fixes(conn, trip_no: int) -> list[dict]:
    """Every raw fix for a trip, in time order, straight off this module's own
    copy of the feed. Nothing is cleaned -- the file is meant to carry the
    device's output, warts included, because the warts are what the pipeline
    page exists to explain."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT dt_message, d_lat, d_long, i_speed, s_asset_id, i_trip_no
                 FROM geo_gps_ping WHERE i_trip_no=%s ORDER BY dt_message, id""",
            (trip_no,))
        return list(cur.fetchall())


HEADERS = ["Trip No", "Vehicle No", "GPS DateTime", "Latitude", "Longitude", "Speed"]


def workbook(rows: list[dict], meta: dict | None = None) -> bytes:
    """The fixes as a .xlsx, in the layout the parser documents as canonical."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "GPS"
    ws.append(HEADERS)
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    for i, _ in enumerate(HEADERS, start=1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([
            r.get("i_trip_no"), r.get("s_asset_id"),
            r["dt_message"] if isinstance(r["dt_message"], datetime) else r["dt_message"],
            float(r["d_lat"]), float(r["d_long"]), r.get("i_speed"),
        ])
    for i, w in enumerate((12, 14, 20, 13, 13, 8), start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        for c in row:
            c.number_format = "yyyy-mm-dd hh:mm:ss"
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=5):
        for c in row:
            c.number_format = "0.000000"
    ws.freeze_panes = "A2"

    if meta:
        info = wb.create_sheet("About")
        info.append(["What this file is"])
        info.append([])
        for k, v in meta.items():
            info.append([str(k), "" if v is None else str(v)])
        info.column_dimensions["A"].width = 28
        info.column_dimensions["B"].width = 52
        info.cell(row=1, column=1).font = Font(bold=True, size=13)
        # The second sheet is documentation. The parser reads the *active*
        # sheet, which is the data one, so this cannot confuse it.
        wb.active = 0

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build(conn, target_km: float = 500.0, trip_no: int | None = None) -> tuple[bytes, str, dict]:
    """(xlsx bytes, filename, what it is). Raises LookupError if nothing fits."""
    if trip_no is None:
        trip = pick(conn, target_km)
        if not trip:
            raise LookupError(
                f"no finished trip near {target_km:,.0f} km in the published run — "
                "run the batch pipeline first, or name a trip explicitly")
        trip_no = trip["i_trip_no"]
    else:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.i_trip_no, s.s_asset_id, s.d_distance_km, s.i_pings_read,
                          s.i_places, s.i_visits, s.s_quality, m.s_origin, m.s_destination,
                          m.s_trans_name
                     FROM geo_trip_summary s LEFT JOIN geo_trip_meta m
                          ON m.i_trip_no=s.i_trip_no
                    WHERE s.i_trip_no=%s ORDER BY s.i_run_id DESC LIMIT 1""", (trip_no,))
            trip = cur.fetchone() or {"i_trip_no": trip_no}
            if trip.get("d_distance_km") is not None:
                trip["d_distance_km"] = float(trip["d_distance_km"])

    rows = fixes(conn, trip_no)
    if not rows:
        raise LookupError(f"trip {trip_no} has no GPS in this application's feed")

    meta = {
        "Trip": trip_no,
        "Vehicle": trip.get("s_asset_id"),
        "Lane": f"{trip.get('s_origin') or '?'} → {trip.get('s_destination') or '?'}",
        "Transporter": trip.get("s_trans_name"),
        "Distance (km)": trip.get("d_distance_km"),
        "GPS fixes": len(rows),
        "From": rows[0]["dt_message"],
        "To": rows[-1]["dt_message"],
        "Exported": datetime.now().replace(microsecond=0),
        "": "",
        "How to use it": "Upload this file on the Upload Trip page. Every step the "
                         "pipeline takes on it is shown there, with its numbers and its "
                         "downloads.",
        "Layout": "One header row, one GPS fix per row. Only the timestamp, latitude "
                  "and longitude are required; speed makes the standstill handling work "
                  "and is strongly recommended.",
    }
    name = f"trip-{trip_no}-{len(rows)}fixes.xlsx"
    return workbook(rows, meta), name, {**trip, "fixes": len(rows)}
