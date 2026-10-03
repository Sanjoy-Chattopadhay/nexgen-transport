"""Smart-Truck's ML service called config.logging_config.setup_logging at
import time; NexGen configures logging once per service process."""

from __future__ import annotations


def setup_logging(service_name: str = "ml") -> None:
    from nexgen.core.logs import setup_logging as _setup
    _setup("ml")
