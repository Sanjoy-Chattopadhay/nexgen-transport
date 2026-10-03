"""Load the National Highway toll plaza list into geo_toll_plaza.

The list is data/nh_fee_plazas.json, extracted from the PDF IHMCL publishes
(scripts/extract_nh_fee_plazas.py explains the few corrections made). It names
every NH fee plaza with its state, district, highway and section, but carries
no coordinates: it answers "how many toll plazas are there in each state", not
"did this truck pass one". The coordinate columns stay NULL until a located
source is loaded beside it.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from nexgen.shared.geoengine.db import geo_session

DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "nh_fee_plazas.json"
SOURCE = "ihmcl"


def import_nh_fee_plazas(path: Path = DATA_FILE) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    as_of = date.fromisoformat(doc["published"]) if doc.get("published") else None
    rows = [(p["netc_code"], p["name"], p["state"], p["state_listed"], p["district"], p["section"],
             p["nh"], p["piu"], p["regional_office"], SOURCE, as_of)
            for p in doc["plazas"]]
    with geo_session() as conn, conn.cursor() as cur:
        # Replace this source's rows whole: the list is republished, not patched.
        cur.execute("DELETE FROM geo_toll_plaza WHERE s_source=%s", (SOURCE,))
        cur.executemany("""
            INSERT INTO geo_toll_plaza
                (s_netc_code, s_name, s_state, s_state_listed, s_district, s_section, s_nh, s_piu,
                 s_regional_office, s_source, d_as_of)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
        conn.commit()
    return {"source": SOURCE, "plazas": len(rows), "published": doc.get("published"),
            "states": len({p["state"] for p in doc["plazas"]})}
