"""The gateway's route table: which service answers which public path.

Patterns come from the `routes` of each service in services.yaml. `*` matches
any run of characters (including '/'). The most specific pattern wins, where
specificity is the number of literal characters, so

    /api/v1/tta/trips/*/weather   -> routing     (21 literal chars after /api/v1)
    /api/v1/tta/*                 -> analytics

lets one legacy router whose paths now belong to different services keep every
URL it had. A tie between two services is a configuration error, reported at
start-up rather than resolved silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nexgen.core.config import get_config


@dataclass(frozen=True)
class Route:
    pattern: str
    service: str
    regex: re.Pattern
    literal: int


def _compile(pattern: str) -> re.Pattern:
    parts = [re.escape(p) for p in pattern.split("*")]
    return re.compile("^" + ".*".join(parts) + "$")


def build_table() -> list[Route]:
    cfg = get_config()
    routes: list[Route] = []
    for name in cfg.service_names:
        for pattern in cfg.service(name).routes:
            routes.append(Route(pattern, name, _compile(pattern), len(pattern.replace("*", ""))))
    routes.sort(key=lambda r: -r.literal)
    seen: dict[str, str] = {}
    for r in routes:
        if r.pattern in seen and seen[r.pattern] != r.service:
            raise ValueError(f"route {r.pattern} is claimed by both {seen[r.pattern]} and {r.service}")
        seen[r.pattern] = r.service
    return routes


def resolve(table: list[Route], path: str) -> Route | None:
    for r in table:
        if r.regex.match(path):
            return r
    return None
