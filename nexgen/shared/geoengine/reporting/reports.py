"""Turn a run's ledger into a report.

Two rules shape everything here.

**Never aggregate across fence scales.** The master holds 1,419 fences under a
hectare and 93 over 100 km2. A mean dwell time over both is not a small
inaccuracy, it is a meaningless number, so every duration figure is broken out
by scale and the regional-scale rows are labelled as catchments rather than
facilities.

**Never present a number without its confidence.** Each trip carries a
data-quality verdict from the pre-filter, and a "site never visited" drawn
from a trail with a four-hour hole is not evidence of absence. Trip counts are
therefore always reported alongside the quality mix behind them, and the
headline figures are additionally given over `good`-quality trails alone so a
reader can see whether the conclusion survives excluding the doubtful ones.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from decimal import Decimal

from nexgen.shared.geoengine.db import geo_conn

# Only these carry an unambiguous "the truck was seen here" signal.
GOOD_QUALITY = ("good", "sparse")


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return list(cur.fetchall())


def latest_run_id(conn=None) -> int | None:
    close = conn is None
    conn = conn or geo_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(i_run_id) AS r FROM geo_run WHERE s_status='ok'")
            return (cur.fetchone() or {}).get("r")
    finally:
        if close:
            conn.close()


def build(run_id: int, conn=None) -> dict:
    """Everything the report needs, in one pass over the ledger."""
    close = conn is None
    conn = conn or geo_conn()
    try:
        with conn.cursor() as cur:
            run = _rows(cur, "SELECT * FROM geo_run WHERE i_run_id=%s", (run_id,))
            if not run:
                raise LookupError(f"no run {run_id}")
            run = run[0]

            # geo_import_reject is an audit table and keeps every import's
            # findings, so it must be scoped to the import that produced the
            # master currently loaded -- otherwise re-importing doubles every
            # reject count in the report.
            import_id = _rows(cur, """
                SELECT MAX(i_import_id) AS i FROM geo_import_run WHERE s_status='ok'
            """)[0]["i"]

            master = _rows(cur, """
                SELECT (SELECT COUNT(*) FROM geo_site)                     AS sites,
                       (SELECT COUNT(*) FROM geo_fence)                    AS fences,
                       (SELECT COUNT(*) FROM geo_fence WHERE b_active=1)   AS fences_active,
                       (SELECT COUNT(*) FROM geo_fence WHERE s_geom_notes IS NOT NULL) AS fences_flagged,
                       (SELECT COUNT(*) FROM geo_site_vertex)              AS vertices,
                       (SELECT COUNT(*) FROM geo_import_reject
                         WHERE i_import_id = %s)                           AS rejects
            """, (import_id,))[0]
            master["import_id"] = import_id

            quality = _rows(cur, """
                SELECT s_quality, COUNT(*) trips, SUM(i_pings_used) pings,
                       SUM(i_visits) visits
                  FROM geo_trip_summary WHERE i_run_id=%s
                 GROUP BY 1 ORDER BY trips DESC""", (run_id,))

            headline = _rows(cur, """
                SELECT COUNT(*) trips,
                       SUM(i_pings_read)  pings_read,
                       SUM(i_pings_used)  pings_used,
                       SUM(i_pings_dropped) pings_dropped,
                       SUM(i_visits)      visits,
                       SUM(i_violations)  violations,
                       ROUND(AVG(d_coverage_pct),1) avg_coverage_pct,
                       SUM(i_pings_inside) pings_inside
                  FROM geo_trip_summary WHERE i_run_id=%s""", (run_id,))[0]

            headline_good = _rows(cur, f"""
                SELECT COUNT(*) trips, SUM(i_visits) visits, SUM(i_violations) violations,
                       ROUND(AVG(d_coverage_pct),1) avg_coverage_pct
                  FROM geo_trip_summary
                 WHERE i_run_id=%s AND s_quality IN ({','.join(['%s']*len(GOOD_QUALITY))})""",
                (run_id, *GOOD_QUALITY))[0]

            by_scale = _rows(cur, """
                SELECT s_scale, COUNT(*) visits, COUNT(DISTINCT i_site_id) sites,
                       COUNT(DISTINCT i_trip_no) trips,
                       ROUND(AVG(i_dwell_seconds)/60,1) avg_dwell_min,
                       ROUND(MAX(i_dwell_seconds)/60,1) max_dwell_min,
                       SUM(b_open) still_inside
                  FROM geo_visit WHERE i_run_id=%s
                 GROUP BY 1 ORDER BY FIELD(s_scale,'micro','site','campus','regional')""",
                (run_id,))

            top_sites = _rows(cur, """
                SELECT v.i_site_id, v.s_site_name, v.s_type, v.s_category, v.s_scale,
                       COUNT(*) visits, COUNT(DISTINCT v.i_trip_no) trips,
                       COUNT(DISTINCT v.s_asset_id) assets,
                       ROUND(AVG(v.i_dwell_seconds)/60,1) avg_dwell_min,
                       ROUND(SUM(v.i_dwell_seconds)/3600,1) total_hours
                  FROM geo_visit v
                 WHERE v.i_run_id=%s AND v.b_primary=1
                 GROUP BY 1,2,3,4,5 ORDER BY visits DESC LIMIT 30""", (run_id,))

            longest = _rows(cur, """
                SELECT i_trip_no, s_asset_id, s_site_name, s_scale, s_category,
                       dt_enter, dt_exit, b_open,
                       ROUND(i_dwell_seconds/3600,2) dwell_hours, i_pings,
                       i_enter_gap_seconds, i_exit_gap_seconds, s_confirmed_by
                  FROM geo_visit
                 WHERE i_run_id=%s AND s_scale IN ('micro','site') AND i_dwell_seconds IS NOT NULL
                 ORDER BY i_dwell_seconds DESC LIMIT 20""", (run_id,))

            violations = _rows(cur, """
                SELECT s_kind, COUNT(*) n, COUNT(DISTINCT i_trip_no) trips,
                       COUNT(DISTINCT i_site_id) sites
                  FROM geo_violation WHERE i_run_id=%s GROUP BY 1 ORDER BY n DESC""",
                (run_id,))

            violation_detail = _rows(cur, """
                SELECT s_kind, s_site_name, i_trip_no, s_asset_id, dt_event,
                       i_observed, i_limit, s_detail
                  FROM geo_violation WHERE i_run_id=%s
                 ORDER BY (i_observed - i_limit) DESC, dt_event LIMIT 25""", (run_id,))

            events = _rows(cur, """
                SELECT s_event, s_confirmed_by, COUNT(*) n,
                       ROUND(AVG(i_gap_seconds)) avg_gap_s,
                       SUM(i_gap_seconds > 900) gap_over_15min
                  FROM geo_event WHERE i_run_id=%s GROUP BY 1,2 ORDER BY n DESC""",
                (run_id,))

            rejects = _rows(cur, """
                SELECT s_reason, SUM(i_count) fixes, COUNT(DISTINCT i_trip_no) trips
                  FROM geo_ping_reject WHERE i_run_id=%s GROUP BY 1 ORDER BY fixes DESC""",
                (run_id,))

            unvisited = _rows(cur, """
                SELECT COUNT(*) n FROM geo_fence f
                 WHERE f.b_active=1
                   AND NOT EXISTS (SELECT 1 FROM geo_visit v
                                    WHERE v.i_run_id=%s AND v.i_fence_id=f.i_fence_id)""",
                (run_id,))[0]["n"]

            import_rejects = _rows(cur, """
                SELECT s_reason, COUNT(*) n FROM geo_import_reject
                 WHERE i_import_id = %s GROUP BY 1 ORDER BY n DESC""", (import_id,))

            no_fence_trips = _rows(cur, """
                SELECT COUNT(*) n FROM geo_trip_summary
                 WHERE i_run_id=%s AND i_visits=0""", (run_id,))[0]["n"]
    finally:
        if close:
            conn.close()

    return {
        "generated_at": datetime.now(),
        "run": run,
        "master": master,
        "headline": headline,
        "headline_good_quality": headline_good,
        "quality": quality,
        "by_scale": by_scale,
        "top_sites": top_sites,
        "longest_dwells": longest,
        "violations": violations,
        "violation_detail": violation_detail,
        "events": events,
        "ping_rejects": rejects,
        "import_rejects": import_rejects,
        "fences_never_visited": unvisited,
        "trips_with_no_fence_contact": no_fence_trips,
    }


def trip_report(run_id: int, trip_no: int, conn=None) -> dict | None:
    """One trip, in enough detail to argue about."""
    close = conn is None
    conn = conn or geo_conn()
    try:
        with conn.cursor() as cur:
            summary = _rows(cur, "SELECT * FROM geo_trip_summary WHERE i_run_id=%s AND i_trip_no=%s",
                            (run_id, trip_no))
            if not summary:
                return None
            visits = _rows(cur, """
                SELECT s_site_name, i_site_id, s_type, s_category, s_scale, dt_enter, dt_exit,
                       b_open, b_primary, b_entry_observed, i_dwell_seconds, i_pings,
                       i_max_speed, ROUND(d_distance_m) d_distance_m,
                       i_enter_gap_seconds, i_exit_gap_seconds, s_confirmed_by
                  FROM geo_visit WHERE i_run_id=%s AND i_trip_no=%s
                 ORDER BY dt_enter, s_scale""", (run_id, trip_no))
            events = _rows(cur, """
                SELECT s_event, s_site_name, dt_event, i_gap_seconds, d_lat, d_long,
                       i_speed, s_confirmed_by
                  FROM geo_event WHERE i_run_id=%s AND i_trip_no=%s
                 ORDER BY dt_event""", (run_id, trip_no))
            viol = _rows(cur, """
                SELECT s_kind, s_site_name, dt_event, i_observed, i_limit, s_detail
                  FROM geo_violation WHERE i_run_id=%s AND i_trip_no=%s ORDER BY dt_event""",
                (run_id, trip_no))
            rejects = _rows(cur, "SELECT s_reason, i_count FROM geo_ping_reject WHERE i_run_id=%s AND i_trip_no=%s",
                            (run_id, trip_no))
    finally:
        if close:
            conn.close()

    return {"summary": summary[0], "visits": visits, "events": events,
            "violations": viol, "ping_rejects": rejects}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    """Format a cell.

    `Decimal` is handled explicitly: MySQL returns it for every `SUM()`, and
    it is neither an `int` nor a `float`, so the obvious two-branch version
    silently prints fleet-scale totals as unseparated digit strings.
    """
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, Decimal):
        v = int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.1f}" if v % 1 else f"{int(v):,}"
    return str(v)


def _table_text(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return "    (none)\n"
    cols = cols or list(rows[0].keys())
    widths = {c: max(len(c), *(len(_fmt(r.get(c))) for r in rows)) for c in cols}
    out = ["    " + "  ".join(c.ljust(widths[c]) for c in cols),
           "    " + "  ".join("-" * widths[c] for c in cols)]
    for r in rows:
        out.append("    " + "  ".join(_fmt(r.get(c)).ljust(widths[c]) for c in cols))
    return "\n".join(out) + "\n"


def render_text(d: dict) -> str:
    run = d["run"]
    h = d["headline"]
    hg = d["headline_good_quality"]
    m = d["master"]
    params = run["j_params"]
    if isinstance(params, str):
        params = json.loads(params)

    L = []
    A = L.append
    A("=" * 78)
    A("GEOFENCE DETECTION REPORT")
    A("=" * 78)
    A(f"run {run['i_run_id']}  |  {run['s_mode']}  |  {run['s_scope']}")
    A(f"started {run['dt_started']}   finished {run['dt_finished']}   "
      f"{_fmt(run['d_seconds'])}s @ {_fmt(run['d_pings_per_sec'])} fixes/s")
    A(f"generated {d['generated_at']}")
    A("")
    A("DETECTOR SETTINGS (this run is reproducible from these)")
    for k, v in params.items():
        A(f"    {k:24} {v}")
    A("")
    A("MASTER")
    A(f"    sites {m['sites']:,}   fences compiled {m['fences']:,}   "
      f"active {m['fences_active']:,}   flagged {m['fences_flagged']:,}   "
      f"vertices {m['vertices']:,}")
    A(f"    import rejections: " + ", ".join(f"{r['s_reason']}={r['n']}" for r in d["import_rejects"]))
    A("")
    A("HEADLINE")
    A(f"    trips              {_fmt(h['trips'])}")
    A(f"    fixes read         {_fmt(h['pings_read'])}   used {_fmt(h['pings_used'])}   "
      f"dropped {_fmt(h['pings_dropped'])}")
    A(f"    fixes inside a fence {_fmt(h['pings_inside'])}")
    A(f"    visits             {_fmt(h['visits'])}")
    A(f"    violations         {_fmt(h['violations'])}")
    A(f"    trips with no fence contact  {_fmt(d['trips_with_no_fence_contact'])}")
    A(f"    active fences never visited  {_fmt(d['fences_never_visited'])} of {m['fences_active']:,}")
    A("")
    A("    Restricted to good/sparse-quality trails only:")
    A(f"        trips {_fmt(hg['trips'])}   visits {_fmt(hg['visits'])}   "
      f"violations {_fmt(hg['violations'])}")
    A("")
    A("TRAIL QUALITY  (a verdict from a broken trail is not a negative result)")
    A(_table_text(d["quality"]))
    A("VISITS BY FENCE SCALE  (never averaged across scales -- see module docs)")
    A(_table_text(d["by_scale"]))
    A("BUSIEST SITES  (innermost fence only, so nested zones are not double counted)")
    A(_table_text(d["top_sites"][:20]))
    A("LONGEST DWELLS AT FACILITY SCALE")
    A(_table_text(d["longest_dwells"][:15]))
    A("CROSSINGS BY HOW THEY WERE CONFIRMED")
    A(_table_text(d["events"]))
    A("VIOLATIONS")
    A(_table_text(d["violations"]))
    if d["violation_detail"]:
        A("    worst cases:")
        A(_table_text(d["violation_detail"][:12]))
    A("GPS FIXES REJECTED BY THE PRE-FILTER")
    A(_table_text(d["ping_rejects"]))
    A("=" * 78)
    return "\n".join(L)


_CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--mut:#6b6b64;--line:#e2e0da;--card:#fff;--accent:#8a5a2b;--warn:#a4432b}
:root:not([data-theme=light]) @media (prefers-color-scheme:dark){}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#16161a;--fg:#e8e6e1;--mut:#9a978e;--line:#2e2e34;--card:#1d1d22;--accent:#d3a066;--warn:#e0805f}}
:root[data-theme=dark]{--bg:#16161a;--fg:#e8e6e1;--mut:#9a978e;--line:#2e2e34;--card:#1d1d22;--accent:#d3a066;--warn:#e0805f}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;padding:32px 20px}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:34px 0 10px;font-weight:600}
.sub{color:var(--mut);margin:0 0 24px;font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card .n{font-size:22px;font-weight:600;letter-spacing:-.02em}
.card .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin-top:2px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:6px;padding:10px 14px;margin:10px 0;color:var(--mut);font-size:13px}
.warn{border-left-color:var(--warn)}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--bg);padding:1px 5px;border-radius:4px;border:1px solid var(--line)}
"""


def _table_html(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return '<div class="note">(none)</div>'
    cols = cols or list(rows[0].keys())
    def numeric(c):
        return all(isinstance(r.get(c), (int, float, Decimal)) or r.get(c) is None
                   for r in rows)
    nums = {c: numeric(c) for c in cols}
    head = "".join(f'<th class="{"num" if nums[c] else ""}">{html.escape(c)}</th>' for c in cols)
    body = []
    for r in rows:
        tds = "".join(
            f'<td class="{"num" if nums[c] else ""}">{html.escape(_fmt(r.get(c)))}</td>'
            for c in cols
        )
        body.append(f"<tr>{tds}</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_html(d: dict) -> str:
    run = d["run"]
    h = d["headline"]
    hg = d["headline_good_quality"]
    m = d["master"]
    params = run["j_params"]
    if isinstance(params, str):
        params = json.loads(params)

    cards = [
        ("trips", h["trips"]), ("fixes read", h["pings_read"]),
        ("fixes used", h["pings_used"]), ("visits", h["visits"]),
        ("violations", h["violations"]),
        ("fences active", m["fences_active"]),
    ]
    card_html = "".join(
        f'<div class="card"><div class="n">{_fmt(v)}</div><div class="l">{html.escape(k)}</div></div>'
        for k, v in cards
    )
    param_html = " &nbsp;·&nbsp; ".join(
        f"<code>{html.escape(k)}={html.escape(str(v))}</code>" for k, v in params.items()
    )

    return f"""<title>Geofence Detection Report</title>
<style>{_CSS}</style>
<div class="wrap">
<h1>Geofence detection report</h1>
<p class="sub">Run {run['i_run_id']} &middot; {html.escape(str(run['s_scope'] or ''))} &middot;
{_fmt(run['d_seconds'])}s at {_fmt(run['d_pings_per_sec'])} fixes/s &middot;
generated {d['generated_at']:%Y-%m-%d %H:%M}</p>

<div class="cards">{card_html}</div>

<div class="note">Detector settings, which this run is reproducible from: {param_html}</div>

<h2>Confidence</h2>
<div class="note warn">Restricted to good and sparse-quality trails only:
<strong>{_fmt(hg['trips'])}</strong> trips, <strong>{_fmt(hg['visits'])}</strong> visits,
<strong>{_fmt(hg['violations'])}</strong> violations. A trail with a multi-hour hole can hide an
entire visit, so a "never visited" drawn from one is missing evidence, not evidence of absence.</div>
{_table_html(d['quality'])}

<h2>Visits by fence scale</h2>
<div class="note">Fence area in this master spans eight orders of magnitude, from a
weighbridge to a district. Dwell time is never averaged across that range: a
<code>regional</code> row is a catchment, not a facility, and a visit to one means something
quite different from a visit to a <code>micro</code> fence.</div>
{_table_html(d['by_scale'])}

<h2>Busiest sites</h2>
<div class="note">Innermost fence only. The master nests heavily &mdash; a truck at Jamshedpur is
inside six polygons at once &mdash; so counting every containment would multiply these figures.</div>
{_table_html(d['top_sites'][:25])}

<h2>Longest dwells at facility scale</h2>
{_table_html(d['longest_dwells'][:15])}

<h2>How crossings were confirmed</h2>
<div class="note"><code>dwell</code> means the new state held for the confirmation window.
<code>escape</code> means the truck got far enough past the boundary that brevity did not matter &mdash;
this is what preserves fast transits and departures whose tracker then died.
<code>i_gap_seconds</code> is the uncertainty on the stamp.</div>
{_table_html(d['events'])}

<h2>Violations</h2>
{_table_html(d['violations'])}
{_table_html(d['violation_detail'][:15])}

<h2>GPS fixes rejected by the pre-filter</h2>
{_table_html(d['ping_rejects'])}

<h2>Master import rejections</h2>
<div class="note">Geometry is never silently repaired. Everything the loader refused is listed
here and, per site, in <code>geo_import_reject</code>.</div>
{_table_html(d['import_rejects'])}

<h2>Coverage</h2>
<div class="note">{_fmt(d['fences_never_visited'])} of {_fmt(m['fences_active'])} active fences saw no
traffic in this run, and {_fmt(d['trips_with_no_fence_contact'])} trips touched no fence at all.
Neither is necessarily wrong &mdash; the master covers the whole client estate, this run covers one
window &mdash; but a fence that never fires is either unused or misplaced, and both are worth knowing.</div>
</div>
"""
