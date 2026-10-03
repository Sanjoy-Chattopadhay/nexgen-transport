"""Intelligence: ETA, SLA, anomaly, driver, fatigue, demand and route models.

Smart-Truck's ML service, ported unchanged (nexgen/services/ml/app), served
under /api/v1/ml/* instead of its own port 8001. It owns nx_ml (the model
registry and prediction log) and reads the historic trip store and its
summaries from analytics' published views (migrations/ml/R__aliases.sql).
Subscription keys and tiers work as before (api_keys.py); they are off unless
ml.settings.api_auth_enabled is set.

The `trainer` role retrains on a weekly schedule (and on demand from the
developer page); it is off by default because training is heavy.
"""

from __future__ import annotations

import logging

from fastapi import Depends

from nexgen.core.config import get_config
from nexgen.core.service import Service

logger = logging.getLogger(__name__)


def _retrain() -> dict:
    from nexgen.services.ml.app.training import train_pipeline
    if hasattr(train_pipeline, "train_all"):
        return {"result": train_pipeline.train_all()}
    if hasattr(train_pipeline, "run"):
        return {"result": train_pipeline.run()}
    raise RuntimeError("the training pipeline has no train_all/run entry point")


def build() -> Service:
    from nexgen.services.ml.api_keys import authenticate
    from nexgen.services.ml.app import main as ml_main

    svc = Service("ml")
    svc.include(ml_main.app.router, prefix="/api/v1", dependencies=[Depends(authenticate)])
    trainer = svc.worker("trainer")
    trainer.jobs.add("retrain", _retrain, cron=str(get_config().setting("ml", "retrain", "0 1 * * 0")),
                     description="retrain every model on the latest historic trips")
    return svc
