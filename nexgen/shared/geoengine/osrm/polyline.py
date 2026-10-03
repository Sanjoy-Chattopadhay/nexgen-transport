"""Encoded polylines, the geometry format OSRM speaks.

The algorithm is Google's: each coordinate is stored as the delta from the
previous one, scaled to an integer, zig-zag encoded so the sign costs one bit,
and emitted five bits at a time as printable characters. OSRM uses it with
precision 5 (`polyline`) or 6 (`polyline6`); this module asks for 6, because
precision 5 quantises to about 1.1 m and a reconstructed route that is later
tested against a 40 m weighbridge fence should not start out a metre wrong.

Coordinates are (lat, lon) in both directions, which is the encoding's own
order and the opposite of OSRM's URL order. Getting that backwards produces a
valid-looking route in the Indian Ocean, so it is asserted in the tests.

Reference: https://developers.google.com/maps/documentation/utilities/polylinealgorithm
"""

from __future__ import annotations

from collections.abc import Iterable


def encode(points: Iterable[tuple[float, float]], precision: int = 6) -> str:
    """(lat, lon) pairs to an encoded polyline."""
    factor = 10 ** precision
    out: list[str] = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat = round(lat * factor)
        ilon = round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def decode(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """An encoded polyline to (lat, lon) pairs."""
    factor = 10 ** precision
    points: list[tuple[float, float]] = []
    index = lat = lon = 0
    length = len(encoded)
    while index < length:
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= length:
                    raise ValueError("truncated polyline")
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / factor, lon / factor))
    return points
