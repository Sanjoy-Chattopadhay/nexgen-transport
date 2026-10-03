"""Import Smart-Truck's model registry and prediction log into nx_ml.

Registry paths such as `ml_models\eta_predictor.joblib` are relative to the
project root, and the trained files were copied into this repository's
ml_models/ folder (git-ignored) on first setup, so they resolve unchanged.
"""

from __future__ import annotations

from nexgen.core.config import get_config
from nexgen.core.db import connect


def import_legacy() -> dict:
    st = f"`{get_config().legacy_database('smart_truck')}`"
    out = {}
    with connect("ml") as conn, conn.cursor() as cur:
        for t in ("ml_models", "predictions"):
            cur.execute(f"INSERT IGNORE INTO `{t}` SELECT * FROM {st}.`{t}`")
            out[t] = {"rows": cur.rowcount}
        conn.commit()
    return out
