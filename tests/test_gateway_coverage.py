"""Every public path a service serves must reach that service through the gateway.

The route table (services.yaml `routes`) is hand-written, and a router's bare
list path is easy to miss: `/api/v1/hotspots/*` does not match
`/api/v1/hotspots`, and the Unscheduled Stops page answered 404 until this
test existed. Building each service's app needs no database.
"""

from __future__ import annotations

import importlib
import os
import re

import pytest

from nexgen.core.config import get_config
from nexgen.supervisor.routes import build_table, resolve

# The ML app's own documentation and health pages ride along when its router
# is mounted; they are not part of the public API.
NOT_PUBLIC = re.compile(r"/(openapi\.json|docs|docs/oauth2-redirect|redoc|health)$")


@pytest.mark.parametrize("name", get_config().service_names)
def test_every_service_path_is_routed_to_its_service(name, monkeypatch):
    monkeypatch.setenv("NEXGEN_SERVICE", name)
    try:
        svc = importlib.import_module(f"nexgen.services.{name}.service").build()
    except ImportError as exc:          # an optional heavy dependency missing in this environment
        pytest.skip(f"{name} cannot be built here: {exc}")
    table = build_table()
    problems = []
    for route in svc.app().routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or NOT_PUBLIC.search(path):
            continue
        got = resolve(table, re.sub(r"\{[^}]+\}", "x", path))
        if got is None:
            problems.append(f"{path}: no route")
        elif got.service != name:
            problems.append(f"{path}: routed to {got.service} by {got.pattern}")
    assert not problems, f"{name}:\n  " + "\n  ".join(problems)


def test_service_env_is_restored():
    # monkeypatch undoes NEXGEN_SERVICE; nothing else in the suite should see it.
    assert os.environ.get("NEXGEN_SERVICE") in (None, "")
