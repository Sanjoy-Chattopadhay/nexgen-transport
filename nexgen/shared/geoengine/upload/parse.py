"""Read a trip out of a spreadsheet.

The engine wants four things per fix -- when, where (lat, lon) and how fast --
and every fleet system spells them differently. Rather than demand one layout,
this matches the header row against a list of known spellings and reports the
mapping it chose, so the user can see the sheet was understood before trusting
anything computed from it.

What is deliberately *not* done here
------------------------------------
Nothing is cleaned, dropped for being implausible, sorted or de-duplicated.
This stage answers one question: which cell is which, and does it hold a
number. Everything else is a pipeline decision that has to be visible as one
(`engine/filters.py`), and a parser that quietly fixed things first would hide
the very steps this feature exists to show. A row whose coordinates will not
read as numbers is refused here -- nothing downstream can use it -- but it is
refused by row number, with the offending cell contents, so it can be found in
the file.

Formats: .xlsx / .xlsm (openpyxl), .csv / .tsv / .txt (delimiter sniffed), and
.json (a list of objects). Excel serial dates, ISO strings, unix epochs and
the dd/mm/yyyy forms Indian exports use are all accepted; an ambiguous
day/month pair is resolved over the whole column, because one row cannot tell
03/04 from 04/03 but a column that reaches 25 can.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

MAX_ROWS = 400_000

# Header spellings accepted, most specific first. Matching is on the header
# squashed to lowercase letters and digits, so "GPS Date/Time", "gps_datetime"
# and "GPSDATETIME" are one key.
FIELDS: dict[str, tuple[str, ...]] = {
    "ts": (
        "dtmessage", "messagedatetime", "messagetime", "gpsdatetime", "gpstime", "gpsdate",
        "datetime", "timestamp", "devicetime", "packettime", "fixtime", "recordedat",
        "date", "time", "ts", "dt",
    ),
    "lat": ("dlat", "latitude", "lat", "gpslat", "latdeg", "ylat"),
    "lon": ("dlong", "dlon", "longitude", "longitute", "long", "lon", "lng", "gpslong"),
    "speed": ("ispeed", "speedkmph", "speedkmh", "speed", "kmph", "velocity"),
    "asset": ("sassetid", "assetid", "vehicleno", "vehiclenumber", "vehicle", "asset",
              "registrationno", "regno", "truckno", "deviceid", "imei"),
    "trip": ("itripno", "tripno", "tripnumber", "tripid", "trip"),
}

# A date and a time in two columns is common enough in fleet exports to join
# rather than refuse.
DATE_ONLY = ("date", "gpsdate", "messagedate", "fixdate", "dtdate")
TIME_ONLY = ("time", "gpstime", "messagetime", "fixtime", "dttime")

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


class ParseError(ValueError):
    """The file cannot be read as a trip at all."""


def _key(header) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header or "").strip().lower())


def map_columns(headers: list) -> dict:
    """Which column feeds which field, and how it was matched.

    Returns `{field: {"index", "header", "matched", "how"}}`, plus a
    `_date`/`_time` pair when the sheet splits the stamp over two columns.
    """
    keys = [_key(h) for h in headers]
    taken: set[int] = set()
    out: dict[str, dict] = {}

    for field, spellings in FIELDS.items():
        for spelling in spellings:
            # Exact header first: "speed" must not be won by "speed_limit".
            for i, k in enumerate(keys):
                if i in taken or k != spelling:
                    continue
                out[field] = {"index": i, "header": str(headers[i]), "matched": spelling,
                              "how": "exact"}
                taken.add(i)
                break
            if field in out:
                break
        if field in out:
            continue
        # Then a contained spelling, longest first so "latitude" beats "lat".
        for spelling in sorted(spellings, key=len, reverse=True):
            for i, k in enumerate(keys):
                if i in taken or spelling not in k:
                    continue
                out[field] = {"index": i, "header": str(headers[i]), "matched": spelling,
                              "how": "contains"}
                taken.add(i)
                break
            if field in out:
                break

    # A split date/time stamp, only when the two are different columns.
    d_i = next((i for i, k in enumerate(keys) if k in DATE_ONLY), None)
    t_i = next((i for i, k in enumerate(keys) if k in TIME_ONLY), None)
    if d_i is not None and t_i is not None and d_i != t_i:
        out["_date"] = {"index": d_i, "header": str(headers[d_i]), "matched": keys[d_i],
                        "how": "split"}
        out["_time"] = {"index": t_i, "header": str(headers[t_i]), "matched": keys[t_i],
                        "how": "split"}
        out["ts"] = dict(out["_date"], how="split")
    return out


def looks_like_header(cells: list) -> bool:
    """Does this row name at least a latitude and a longitude?"""
    cols = map_columns(list(cells or ()))
    return "lat" in cols and "lon" in cols


# ---------------------------------------------------------------------------
# Cell readers
# ---------------------------------------------------------------------------

def _num(v):
    """A number out of a cell that may be a number, or text around one."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.search(str(v).replace(",", ""))
    return float(m.group()) if m else None


# Excel counts days from 1900-01-01 = 1 and believes 1900 was a leap year, so
# the epoch that makes every real date come out right is 1899-12-30.
_EXCEL_EPOCH = datetime(1899, 12, 30)

_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y",
    "%d %b %Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S",
)

_SLASHED = re.compile(
    r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)


def _timestamp(v, dayfirst: bool = True):
    """A datetime out of a cell, or None.

    `dayfirst` resolves 03/04/2026; `scan_dayfirst` settles it for the whole
    column before any row is read.
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        f = float(v)
        # An Excel serial, bounded to 1987-2105 so a stray count in a timestamp
        # column is refused rather than read as a date in 1902.
        if 32000 < f < 75000:
            return _EXCEL_EPOCH + timedelta(days=f)
        # Naive UTC, matching the rest of the pipeline: the feed's own
        # datetimes are naive and only differences are ever taken, so no zone
        # is assumed anywhere.
        if 9.4e8 < f < 4.1e9:                       # unix seconds
            return datetime.fromtimestamp(f, timezone.utc).replace(tzinfo=None)
        if 9.4e11 < f < 4.1e12:                     # unix milliseconds
            return datetime.fromtimestamp(f / 1000.0, timezone.utc).replace(tzinfo=None)
        return None

    s = str(v).strip()
    if not s:
        return None
    # A bare run of digits is a count, not a date, and must take the numeric
    # path. strptime is lenient about field widths, so "%Y%m%d%H%M%S" happily
    # reads the unix epoch 1752732714 as the year 1752 — a plausible-looking
    # timestamp three centuries wrong, which is the worst kind of wrong.
    if s.isdigit() and len(s) != 14:
        return _timestamp(float(s))
    m = _SLASHED.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm, ss = int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0)
        if y < 100:
            y += 2000
        # A value over 12 settles the row on its own, whatever the column said.
        first = True if a > 12 else False if b > 12 else dayfirst
        d, mo = (a, b) if first else (b, a)
        try:
            return datetime(y, mo, d, hh, mm, ss)
        except ValueError:
            try:
                return datetime(y, d, mo, hh, mm, ss)
            except ValueError:
                return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "").replace("/", "-"))
    except ValueError:
        return None


def scan_dayfirst(values) -> bool:
    """Is this column day/month/year or month/day/year?

    Decided over the whole column, not per row: a value above 12 in the first
    position proves day-first, one in the second proves month-first. Indian
    fleet exports are day-first, so that is the tie-break when a column never
    reaches 13 -- and the choice is reported on the page rather than hidden.
    """
    day_first = month_first = 0
    for v in values:
        if not isinstance(v, str):
            continue
        m = _SLASHED.match(v)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            day_first += 1
        elif b > 12:
            month_first += 1
    return month_first <= day_first


def _combine(d, t):
    """A date cell and a time cell into one stamp."""
    base = _timestamp(d)
    if base is None:
        return None
    if isinstance(t, time):
        return datetime.combine(base.date(), t)
    if isinstance(t, datetime):
        return datetime.combine(base.date(), t.time())
    if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= float(t) < 1:
        return base + timedelta(days=float(t))          # Excel time fraction
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?", str(t or "").strip())
    if m:
        return base + timedelta(hours=int(m.group(1)), minutes=int(m.group(2)),
                                seconds=int(m.group(3) or 0))
    return base


# ---------------------------------------------------------------------------
# Readers, one per container format
# ---------------------------------------------------------------------------

def _find_header(stream) -> tuple[list, int]:
    """The first row naming a latitude and a longitude, and how many rows of
    title or blurb sat above it. Exports routinely carry both.

    If no such row turns up, the refusal names the columns the file *did*
    have. "Your file has no latitude" is only actionable next to the list the
    reader can compare against their own spreadsheet.
    """
    skipped = 0
    best: list = []
    for row in stream:
        cells = list(row or ())
        if looks_like_header(cells):
            return cells, skipped
        # Remember the widest row of text cells as the likely intended header.
        text = [c for c in cells if isinstance(c, str) and c.strip()]
        if len(text) > len(best):
            best = [str(c).strip() for c in cells if c not in (None, "")]
        skipped += 1
        if skipped > 25:
            break
    seen = ", ".join(best[:25]) if best else "none that could be read as names"
    raise ParseError(
        "could not find a column for: latitude, longitude. "
        f"Columns seen: {seen}")


def _rows_xlsx(data: bytes) -> tuple[list, list[list], int]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        stream = wb.active.iter_rows(values_only=True)
        header, skipped = _find_header(stream)
        body: list[list] = []
        for row in stream:
            body.append(list(row or ()))
            if len(body) >= MAX_ROWS:
                break
        return header, body, skipped
    finally:
        wb.close()


def _rows_csv(data: bytes) -> tuple[list, list[list], int]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header, skipped = _find_header(reader)
    body: list[list] = []
    for row in reader:
        body.append(row)
        if len(body) >= MAX_ROWS:
            break
    return header, body, skipped


def _rows_json(data: bytes) -> tuple[list, list[list], int]:
    doc = json.loads(data.decode("utf-8-sig", errors="replace"))
    if isinstance(doc, dict):
        for key in ("points", "pings", "rows", "data", "items", "gps"):
            if isinstance(doc.get(key), list):
                doc = doc[key]
                break
    if not isinstance(doc, list) or not doc or not isinstance(doc[0], dict):
        raise ParseError("expected a JSON list of objects, one per GPS fix")
    header = list(doc[0].keys())
    return header, [[r.get(h) for h in header] for r in doc[:MAX_ROWS]], 0


READERS = {"xlsx": _rows_xlsx, "xlsm": _rows_xlsx, "csv": _rows_csv, "txt": _rows_csv,
           "tsv": _rows_csv, "json": _rows_json}


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------

def parse(data: bytes, filename: str) -> dict:
    """Spreadsheet bytes -> fixes, the column mapping, and what was refused.

    `pings` are `{seq, row, ts, lat, lon, speed}` in file order -- unsorted,
    un-deduplicated, unfiltered. `refused` holds rows that carried no usable
    stamp or coordinates, with the cell values that could not be read.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "csv"
    reader = READERS.get(ext)
    if reader is None:
        raise ParseError(f"cannot read a .{ext} file; use .xlsx, .csv or .json")
    header, body, skipped = reader(data)
    if not body:
        raise ParseError("the file has a header row but no data rows")

    cols = map_columns(header)
    missing = [f for f in ("ts", "lat", "lon") if f not in cols]
    if missing:
        raise ParseError(
            "could not find a column for: " + ", ".join(missing)
            + ". Columns seen: " + ", ".join(str(h) for h in header[:25])
        )

    ts_i = cols["ts"]["index"]
    lat_i = cols["lat"]["index"]
    lon_i = cols["lon"]["index"]
    spd_i = cols["speed"]["index"] if "speed" in cols else None
    date_i = cols["_date"]["index"] if "_date" in cols else None
    time_i = cols["_time"]["index"] if "_time" in cols else None

    def cell(row, i):
        return row[i] if i is not None and i < len(row) else None

    dayfirst = scan_dayfirst([cell(r, date_i if date_i is not None else ts_i)
                              for r in body[:5000]])

    # Spreadsheet row numbers, so a refusal can be found in the file: rows are
    # 1-based and the header sits at `skipped + 1`.
    offset = skipped + 2

    pings: list[dict] = []
    refused: list[dict] = []
    for n, row in enumerate(body):
        if date_i is not None and time_i is not None:
            ts = _combine(cell(row, date_i), cell(row, time_i))
        else:
            ts = _timestamp(cell(row, ts_i), dayfirst)
        lat = _num(cell(row, lat_i))
        lon = _num(cell(row, lon_i))
        if ts is None or lat is None or lon is None:
            # A wholly blank row is padding, not an error worth reporting.
            if not any(c not in (None, "") for c in row):
                continue
            refused.append({
                "row": n + offset,
                "reason": ("no timestamp" if ts is None
                           else "no latitude" if lat is None else "no longitude"),
                "ts": str(cell(row, date_i if date_i is not None else ts_i))[:40],
                "lat": str(cell(row, lat_i))[:24],
                "lon": str(cell(row, lon_i))[:24],
            })
            continue
        spd = _num(cell(row, spd_i)) if spd_i is not None else None
        pings.append({
            "seq": len(pings), "row": n + offset, "ts": ts, "lat": lat, "lon": lon,
            "speed": None if spd is None else int(round(max(0.0, min(255.0, spd)))),
        })

    asset = _single(body, cols.get("asset"), cell)
    trip = _single(body, cols.get("trip"), cell)

    if not pings:
        raise ParseError(
            f"{len(body)} rows read, but none carried a usable timestamp and coordinates"
        )

    return {
        "format": ext,
        "headers": [str(h) for h in header],
        "columns": cols,
        "dayfirst": dayfirst,
        "header_row": skipped + 1,
        "rows_read": len(body),
        "pings": pings,
        "refused": refused,
        "asset_id": asset,
        "trip_no": None if trip is None else int(_num(trip) or 0) or None,
    }


def _single(body, col, cell):
    """A column's value when the whole file agrees on one -- the vehicle, the
    trip number. Two different values means the sheet is not one trip, and
    nothing is claimed."""
    if not col:
        return None
    seen = set()
    for row in body[:2000]:
        v = cell(row, col["index"])
        s = str(v).strip() if v is not None else ""
        if s and s.lower() not in ("none", "nan", "null", "-"):
            seen.add(s)
            if len(seen) > 1:
                return None
    return next(iter(seen))[:50] if seen else None
