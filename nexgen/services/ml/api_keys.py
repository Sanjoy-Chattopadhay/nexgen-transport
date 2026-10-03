"""
API key authentication for the Smart-Truck ML subscription API.

Keys are configured via the `ML_API_KEYS` env var as a comma-separated list of
`key:tier:client_name` entries, e.g.

    ML_API_KEYS="abc123:pro:acme_logistics,xyz789:basic:beta_corp"

Each request must send an `X-API-Key` header. The middleware:
  * verifies the key exists,
  * checks the caller's tier is allowed to call the requested model,
  * attaches `request.state.api_client` for downstream use / logging.

If `ML_API_AUTH_ENABLED=0` (default), auth is skipped — local dev mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set

from fastapi import HTTPException, Request, status

from nexgen.shared.legacy_settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier → allowed model endpoints
# Edit this map to change what each subscription tier can call.
# Keys are *path prefixes* matched with str.startswith().
# ---------------------------------------------------------------------------
TIER_MODELS: Dict[str, Set[str]] = {
    "basic": {
        "/ml/predict/eta",
        "/ml/drivers/scores",
        "/ml/drivers/",            # /ml/drivers/{id}/score
        "/ml/forecast/demand",
        "/ml/forecast/trips",
        "/ml/models",
    },
    "pro": {
        "/ml/predict/eta",
        "/ml/predict/sla",
        "/ml/scan/anomalies",
        "/ml/drivers/scores",
        "/ml/drivers/",
        "/ml/drivers/fatigue",
        "/ml/forecast/demand",
        "/ml/forecast/trips",
        "/ml/optimize/route",
        "/ml/optimize/hubs",
        "/ml/recommend/drivers",
        "/ml/models",
    },
    "enterprise": {
        # everything except training/cache — see ADMIN_PATHS below
        "/ml/",
    },
}

# Paths that ONLY enterprise (or unauthenticated dev) can hit.
ADMIN_PATHS: Set[str] = {
    "/ml/train",
    "/ml/train-all",
    "/ml/train-tier",
    "/ml/cache/clear",
}

# Paths that bypass auth entirely (health, docs, public landing).
PUBLIC_PATHS: Set[str] = {
    "/health",
    "/ml",
    "/docs",
    "/redoc",
    "/openapi.json",
}


@dataclass(frozen=True)
class ApiClient:
    key: str
    tier: str
    name: str


def _parse_keys(raw: str) -> Dict[str, ApiClient]:
    """Parse the ML_API_KEYS env var into a lookup dict."""
    out: Dict[str, ApiClient] = {}
    if not raw:
        return out
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 3:
            logger.warning("Skipping malformed ML_API_KEYS entry: %r", entry)
            continue
        key, tier, name = parts[0], parts[1].lower(), ":".join(parts[2:])
        if tier not in TIER_MODELS:
            logger.warning("Skipping entry with unknown tier %r: %r", tier, entry)
            continue
        out[key] = ApiClient(key=key, tier=tier, name=name)
    return out


# Load once at import. Restart the service to pick up new keys.
_KEYS: Dict[str, ApiClient] = _parse_keys(settings.ML_API_KEYS)
logger.info("Loaded %d API keys (auth_enabled=%s)", len(_KEYS), settings.ML_API_AUTH_ENABLED)


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PATHS)


def _is_admin_path(path: str) -> bool:
    return any(path.startswith(p) for p in ADMIN_PATHS)


def _tier_allows(tier: str, path: str) -> bool:
    """Does the tier's allowlist cover this path?"""
    allowed = TIER_MODELS.get(tier, set())
    return any(path.startswith(prefix) for prefix in allowed)


def authenticate(request: Request) -> Optional[ApiClient]:
    """
    FastAPI dependency. Raises 401/403 on failure, returns the ApiClient on success.
    Returns None (allows through) when auth is disabled or the path is public.
    """
    path = request.url.path
    # NexGen serves the ML API under /api/v1; the tier map names /ml/... paths.
    if path.startswith("/api/v1/"):
        path = path[len("/api/v1"):]

    if _is_public(path):
        return None

    if not settings.ML_API_AUTH_ENABLED:
        # Dev mode — let everything through but stamp a synthetic client for logs.
        return ApiClient(key="dev", tier="enterprise", name="local_dev")

    key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header. Contact support to get a subscription key.",
        )

    client = _KEYS.get(key)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    if _is_admin_path(path) and client.tier != "enterprise":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Endpoint {path} requires the 'enterprise' tier. "
                   f"Your tier: '{client.tier}'.",
        )

    if not _tier_allows(client.tier, path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your subscription tier '{client.tier}' does not include {path}. "
                   f"Upgrade to access this model.",
        )

    # Stash on request for logging / per-client rate limiting later.
    request.state.api_client = client
    return client
