"""Authoritative Tata plant coordinates, looked up rather than derived.

Why this file exists
--------------------
Every other fence in this module is derived from the GPS trail, because the
eTrans feed ships node names and no geometry. That derivation has a failure
mode nothing inside the data can correct: a trip is sometimes **closed at a
different plant** from the one it was booked against, because the driver was
redirected en route. The medoid of "pings at this node" then sits wherever the
trucks actually ended up, not where the plant is.

So for the nodes that are real Tata facilities, the coordinates below are taken
from published sources rather than inferred from the corpus. They are the
ground truth the trip data is measured *against*, and they do not move when the
data is noisy.

Radius
------
`PLANT_RADIUS_M` is 10 km, as specified by the business. It is a deliberate
choice with a visible consequence, so state it plainly:

  * A works belt is not a point. Trucks booked to "JAMSHEDPUR" physically load
    at CRM BARA, TRYL-BARA, ADITYAPUR, JAMSHEDPUR (IBMD) and others; measured
    from the plant coordinate below those sit between 3.9 and 5.5 km out, so a
    10 km circle contains the belt.
  * Gamharia is 12.2 km from the Jamshedpur plant, so it falls **outside** this
    fence and a JAMSHEDPUR -> GAMHARIA run is a real departure rather than an
    intra-fence move. Under the previous 15 km derived fence it was inside.
    That is the intended reading, but it does move the numbers.

Provenance
----------
Each entry carries the URL it came from and whether the source called the
coordinate exact. Nothing here is guessed; if a plant is not listed, the node
falls back to the derived fence rather than getting an invented centre.
"""

from __future__ import annotations

from dataclasses import dataclass

from .index import normalise_key

# The business specified 10 km around the plant. See the module docstring for
# what this radius does and does not contain.
PLANT_RADIUS_M = 10_000


@dataclass(frozen=True)
class Plant:
    """A Tata facility with a coordinate that came from outside the trip data."""
    plant_id: str
    name: str
    lat: float
    lon: float
    district: str
    source_url: str
    source_precision: str      # what the source itself claimed
    radius_m: int = PLANT_RADIUS_M


# ---------------------------------------------------------------------------
# The gazetteer.
#
# Coordinates are WGS 84 decimal degrees, taken from Global Energy Monitor's
# plant pages, which publish a per-plant coordinate and label its precision.
# ---------------------------------------------------------------------------
PLANTS: dict[str, Plant] = {
    "jamshedpur": Plant(
        plant_id="jamshedpur",
        name="Tata Steel Jamshedpur",
        lat=22.788598, lon=86.199600,
        district="East Singhbhum, Jharkhand",
        source_url="https://www.gem.wiki/Tata_Steel_Jamshedpur_steel_plant",
        source_precision="exact",
    ),
    "gamharia": Plant(
        plant_id="gamharia",
        name="Tata Steel Gamharia",
        lat=22.812895, lon=86.084022,
        district="Seraikela Kharsawan, Jharkhand",
        source_url="https://www.gem.wiki/Tata_Steel_Gamharia_steel_plant",
        source_precision="exact",
    ),
    "kalinganagar": Plant(
        plant_id="kalinganagar",
        name="Tata Steel Kalinganagar",
        lat=20.970411, lon=86.015211,
        district="Jajpur, Odisha",
        source_url="https://www.gem.wiki/Tata_Steel_Kalinganagar_steel_plant",
        source_precision="exact",
    ),
    "meramandali": Plant(
        plant_id="meramandali",
        name="Tata Steel Meramandali",
        lat=20.796053, lon=85.260382,
        district="Dhenkanal, Odisha",
        source_url="https://www.gem.wiki/Tata_Steel_Meramandali_steel_plant",
        source_precision="exact",
    ),
}


# ---------------------------------------------------------------------------
# Node name -> plant, split by the ROLE the node is playing.
#
# This split is the whole design, and getting it wrong silently ruins the
# destination reports, so it is worth stating why.
#
# ORIGIN. Every trip is booked out of "JAMSHEDPUR". That one label covers a
# works belt whose units sit 0.9-6.4 km from the plant coordinate (JCAPCPL 0.87,
# JSR WORKS PLANTTRY 0.88, CRM BARA 3.88, TRYL-BARA 4.22, TBL-BARA 4.92, IBMD
# 5.46, ADITYAPUR 6.41 -- all measured against the published coordinate above).
# A 10 km circle on the plant therefore contains the belt, which is exactly what
# the origin fence needs to do.
#
# DESTINATION. The same belt names also appear as delivery points, and there
# they are *distinct places*. Collapsing them onto the plant coordinate would
# give seven different destinations one identical fence and destroy the ability
# to say which one a truck reached. So belt sub-nodes keep their own derived
# fences when they are a destination, and only the genuinely separate Tata
# plants are corrected here.
#
# A node absent from both maps keeps its derived fence. That is the common case:
# most destinations are customer sites, and inventing a plant coordinate for
# them is precisely the guesswork this file exists to avoid.
# ---------------------------------------------------------------------------

# Origin labels -> the plant they are booked out of.
_ORIGIN_TO_PLANT: dict[str, str] = {
    "JAMSHEDPUR": "jamshedpur",
}

# Destinations that are themselves Tata plants with a published coordinate.
#
# GAMHARIA is 12.15 km from the Jamshedpur plant, so it sits outside the 10 km
# origin fence and a JAMSHEDPUR -> GAMHARIA run reads as a real departure.
#
# DHENKANAL is the correction that proves the approach: its derived fence was a
# gazetteer town centroid 38.26 km from the plant, yet the pings observed at
# that node land 0.2 km from Tata Steel Meramandali. The feed's "DHENKANAL"
# means the plant, not the town, and the town centroid was measuring the wrong
# place entirely.
#
# BEEKAY STEEL-JSR is deliberately absent: Beekay Steel is a separate company,
# its anchor sits 13.23 km from the Tata plant, and forcing it onto the plant
# coordinate would move a correct fence 13 km for no reason.
_DESTINATION_TO_PLANT: dict[str, str] = {
    "GAMHARIA": "gamharia",
    "DUBURI": "kalinganagar",
    "JAJPUR ROAD": "kalinganagar",
    "DHENKANAL": "meramandali",
}


def resolve_plant(node_name: str | None, role: str = "origin") -> Plant | None:
    """The Tata plant a node name refers to in this role, or None.

    None is the common and correct answer -- most destinations are customer
    sites. It sends the node back to its derived fence rather than inventing a
    centre for it.
    """
    key = normalise_key(node_name or "")
    if not key:
        return None
    table = _ORIGIN_TO_PLANT if role == "origin" else _DESTINATION_TO_PLANT
    plant_id = table.get(key)
    return PLANTS.get(plant_id) if plant_id else None


def plant_nodes() -> list[tuple[str, str, Plant]]:
    """Every (node key, role, plant) this gazetteer is authoritative for."""
    out: list[tuple[str, str, Plant]] = []
    for key, pid in _ORIGIN_TO_PLANT.items():
        if pid in PLANTS:
            out.append((key, "origin", PLANTS[pid]))
    for key, pid in _DESTINATION_TO_PLANT.items():
        if pid in PLANTS:
            out.append((key, "destination", PLANTS[pid]))
    return out
