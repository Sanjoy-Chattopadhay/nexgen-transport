"""Logging: one rotating file per service, plus the console.

Each service writes `logs/<service>.log` (5 MB x 5 files). The supervisor
also captures each child's raw console output in `logs/<service>.console.log`,
which is where a crash before logging was configured ends up. The developer
page tails both.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections import deque
from pathlib import Path

from nexgen.core.config import get_config

_configured = False


def logs_dir() -> Path:
    cfg = get_config()
    d = cfg.path(cfg.run.get("logs_dir", "logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(name: str) -> None:
    global _configured
    if _configured:
        return
    cfg = get_config()
    lc = cfg.get("logging") or {}
    level = getattr(logging, str(lc.get("level", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter(lc.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(logs_dir() / f"{name}.log", maxBytes=5_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    # Third-party chatter that drowns the useful lines.
    for noisy in ("apscheduler", "httpx", "httpcore", "uvicorn.access", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def tail(name: str, lines: int = 200, console: bool = False) -> list[str]:
    """The last `lines` lines of a service's log (or console capture)."""
    path = logs_dir() / (f"{name}.console.log" if console else f"{name}.log")
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return [ln.rstrip("\n") for ln in deque(f, maxlen=max(1, min(lines, 5000)))]
