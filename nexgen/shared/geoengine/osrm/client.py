"""A client for an OSRM server -- optional, and never trusted blindly.

OSRM is switched off unless `OSRM_URL` is set. Every stage that uses it has a
defined behaviour without it, and the run records which one it got, so a
report produced with map matching and one produced without can never be
mistaken for each other.

What it is asked for
--------------------
`/match`  -- where each *moving* fix most probably was on the road network
            (hidden Markov map matching, Newson & Krumm 2009). Stationary fixes
            are never sent: a parked truck is usually in a yard, off the
            network, and matching would drag it onto the nearest road.
`/route`  -- the most probable road path across a hole in the trail, so the
            fences a truck must have passed while unobserved can be named.
`/nearest`-- a health probe.

How the answers are treated
---------------------------
OSRM answers a request it understood but could not satisfy -- no road near
the points, no route -- with HTTP 400 and a JSON `code`. Those are answers
("this fix is not on a road") and are returned as such. Only transport
failures count against the circuit breaker, which disables OSRM for the rest
of the process after a run of them rather than stalling every trip on a
timeout.

Nothing here decides whether a snapped position is *used*. That is the fit
stage's call (`prep/fit.py`), which bounds how far a snap may move a fix.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from nexgen.shared.geoengine.osrm import polyline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OsrmConfig:
    url: str | None = None
    profile: str = "driving"
    timeout_s: float = 15.0
    # osrm-routed's default --max-matching-size. A server started with a
    # larger limit can take bigger chunks; more context helps the HMM.
    match_chunk: int = 100
    # Fixes shared between consecutive chunks. Each chunk's edges are where
    # the matcher has least context, so only the middle of each is kept.
    match_overlap: int = 10
    # GPS standard deviation handed to the matcher, in metres. Candidates are
    # searched within about three of these.
    radius_m: float = 15.0
    # A snap that would move a fix further than this is not used.
    max_snap_m: float = 30.0
    min_confidence: float = 0.0
    snap_moves: bool = True
    route_gaps: bool = True
    breaker_failures: int = 5

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def as_dict(self) -> dict:
        return {
            "osrm_url": self.url, "osrm_profile": self.profile,
            "osrm_match_chunk": self.match_chunk, "osrm_match_overlap": self.match_overlap,
            "osrm_radius_m": self.radius_m, "osrm_max_snap_m": self.max_snap_m,
            "osrm_min_confidence": self.min_confidence,
            "osrm_snap_moves": self.snap_moves, "osrm_route_gaps": self.route_gaps,
        }


@dataclass(frozen=True, slots=True)
class Tracepoint:
    lat: float
    lon: float
    snap_m: float          # distance OSRM moved the fix onto the road
    matching: int          # which sub-matching it belongs to
    confidence: float      # that sub-matching's confidence, 0..1
    name: str              # road name, often empty


@dataclass(frozen=True, slots=True)
class Route:
    distance_m: float
    duration_s: float
    polyline6: str
    snap_from_m: float | None
    snap_to_m: float | None

    def points(self) -> list[tuple[float, float]]:
        """(lat, lon) along the route."""
        return polyline.decode(self.polyline6, 6)


class OsrmUnavailable(Exception):
    """The server could not be reached, or OSRM is switched off."""


class OsrmClient:
    """Thread-safe; one keep-alive connection per thread.

    Keep-alive matters once the server is remote: a batch over the corpus is
    tens of thousands of requests, and a TLS handshake on each would cost more
    than the matching.
    """

    def __init__(self, cfg: OsrmConfig):
        self.cfg = cfg
        self._local = threading.local()
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self.broken = False
        self.requests = 0
        self.transport_errors = 0
        self.no_match = 0
        if cfg.url:
            parts = urlsplit(cfg.url)
            self._https = parts.scheme == "https"
            self._host = parts.hostname or "localhost"
            self._port = parts.port
            self._base = parts.path.rstrip("/")

    # -- state ---------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and not self.broken

    @property
    def status(self) -> str:
        if not self.cfg.enabled:
            return "off"
        if self.broken:
            return "unreachable"
        if self.transport_errors:
            return "partial"
        return "ok"

    def stats(self) -> dict:
        return {"status": self.status, "requests": self.requests,
                "transport_errors": self.transport_errors, "no_match": self.no_match}

    # -- transport -----------------------------------------------------------

    def _connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            conn = cls(self._host, self._port, timeout=self.cfg.timeout_s)
            self._local.conn = conn
        return conn

    def _drop_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    def _get(self, path: str) -> dict:
        if not self.enabled:
            raise OsrmUnavailable("OSRM is switched off" if not self.cfg.enabled
                                  else "OSRM disabled after repeated failures")
        last: Exception | None = None
        # One retry on a fresh connection: a keep-alive socket the server has
        # already closed fails on first use, and that is not an outage.
        for _ in range(2):
            conn = self._connection()
            try:
                conn.request("GET", self._base + path, headers={"Connection": "keep-alive"})
                resp = conn.getresponse()
                body = resp.read()
                if resp.status >= 500:
                    raise http.client.HTTPException(f"HTTP {resp.status}")
                data = json.loads(body)
                with self._lock:
                    self.requests += 1
                    self._consecutive_failures = 0
                return data
            except (OSError, http.client.HTTPException, ValueError) as exc:
                last = exc
                self._drop_connection()
        with self._lock:
            self.transport_errors += 1
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.cfg.breaker_failures and not self.broken:
                self.broken = True
                logger.warning("OSRM at %s failed %s times in a row; disabled for this process",
                               self.cfg.url, self._consecutive_failures)
        raise OsrmUnavailable(f"{type(last).__name__}: {last}")

    # -- services ------------------------------------------------------------

    def health(self, lat: float = 22.8046, lon: float = 86.2029) -> dict:
        """Probe the server with one /nearest call. Defaults to Jamshedpur,
        so a server built from an extract that does not cover the client's
        home region reports NoSegment instead of looking healthy."""
        if not self.cfg.enabled:
            return {"enabled": False, "status": "off"}
        t0 = time.perf_counter()
        try:
            data = self._get(f"/nearest/v1/{self.cfg.profile}/{lon:.6f},{lat:.6f}?number=1")
        except OsrmUnavailable as exc:
            return {"enabled": True, "status": "unreachable", "url": self.cfg.url, "error": str(exc)}
        ms = round((time.perf_counter() - t0) * 1000, 1)
        ok = data.get("code") == "Ok"
        wp = (data.get("waypoints") or [{}])[0]
        return {"enabled": True, "status": "ok" if ok else "no_coverage", "url": self.cfg.url,
                "code": data.get("code"), "latency_ms": ms,
                "probe_snap_m": wp.get("distance"), "probe_road": wp.get("name")}

    def match(self, lats, lons, epochs) -> list[Tracepoint | None]:
        """Map-match a sequence of fixes. The result is aligned with the input;
        `None` means OSRM could not place that fix on the network.

        Raises OsrmUnavailable on a transport failure, so a caller can tell
        "no road here" apart from "no answer at all".
        """
        n = len(lats)
        out: list[Tracepoint | None] = [None] * n
        if n < 2:
            return out
        chunk = max(2, int(self.cfg.match_chunk))
        overlap = max(0, min(int(self.cfg.match_overlap), chunk // 2))
        step = chunk - overlap
        start = 0
        while True:
            end = min(start + chunk, n)
            pts = self._match_chunk(lats[start:end], lons[start:end], epochs[start:end])
            # Keep the middle of the chunk. The halves of the overlap meet
            # exactly, so every fix is taken from precisely one chunk.
            keep_from = start if start == 0 else start + overlap // 2
            keep_to = end if end == n else end - (overlap - overlap // 2)
            for k in range(keep_from, keep_to):
                out[k] = pts[k - start]
            if end == n:
                return out
            start += step

    def _match_chunk(self, lats, lons, epochs) -> list[Tracepoint | None]:
        m = len(lats)
        coords = ";".join(f"{float(lo):.6f},{float(la):.6f}" for la, lo in zip(lats, lons))
        stamps = ";".join(str(int(t)) for t in epochs)
        radius = f"{self.cfg.radius_m:g}"
        path = (f"/match/v1/{self.cfg.profile}/{coords}"
                f"?timestamps={stamps}&radiuses={';'.join([radius] * m)}"
                "&gaps=split&tidy=false&overview=false&steps=false&annotations=false")
        data = self._get(path)
        if data.get("code") != "Ok":
            with self._lock:
                self.no_match += 1
            return [None] * m
        confidences = [float(x.get("confidence") or 0.0) for x in data.get("matchings") or []]
        result: list[Tracepoint | None] = []
        for tp in (data.get("tracepoints") or [])[:m]:
            if not tp:
                result.append(None)
                continue
            lon, lat = tp["location"]
            idx = int(tp.get("matchings_index") or 0)
            result.append(Tracepoint(
                lat=float(lat), lon=float(lon),
                snap_m=float(tp.get("distance") or 0.0),
                matching=idx,
                confidence=confidences[idx] if idx < len(confidences) else 0.0,
                name=str(tp.get("name") or "")[:120],
            ))
        result.extend([None] * (m - len(result)))
        return result

    def route(self, a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> Route | None:
        """Fastest road path from A to B, or None if OSRM has no route."""
        path = (f"/route/v1/{self.cfg.profile}/{a_lon:.6f},{a_lat:.6f};{b_lon:.6f},{b_lat:.6f}"
                "?overview=full&geometries=polyline6&steps=false&alternatives=false&annotations=false")
        data = self._get(path)
        routes = data.get("routes") or []
        if data.get("code") != "Ok" or not routes:
            with self._lock:
                self.no_match += 1
            return None
        r = routes[0]
        wps = data.get("waypoints") or []
        return Route(
            distance_m=float(r.get("distance") or 0.0),
            duration_s=float(r.get("duration") or 0.0),
            polyline6=str(r.get("geometry") or ""),
            snap_from_m=float(wps[0]["distance"]) if len(wps) > 0 and wps[0].get("distance") is not None else None,
            snap_to_m=float(wps[1]["distance"]) if len(wps) > 1 and wps[1].get("distance") is not None else None,
        )
