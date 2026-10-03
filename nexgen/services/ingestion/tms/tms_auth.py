"""
TMS Auth Client — JWT token lifecycle for the scheduled API sync.
--------------------------------------------------------------------
The upstream TMS exposes an auth API that returns a short-lived JWT
access token (~10 min) plus a refresh token. This client:

  * logs in with username/password (TMS_AUTH_URL),
  * caches the access + refresh tokens and the access-token expiry,
  * hands the caller a *valid* access token via get_token(), transparently
    refreshing (TMS_REFRESH_URL) shortly before expiry, and
  * falls back to a full re-login if the refresh token is rejected.

Isolated from the fetch logic (tta_api_sync.py) so it can be unit-tested
on its own. Thread-safe: the manual-trigger endpoint and the scheduled
tick can both call get_token() concurrently.
"""

import base64
import json
import logging
import threading
import time
from datetime import datetime, timedelta

import requests

from nexgen.shared.legacy_settings import settings
from nexgen.services.ingestion.tms.etl_log import log_event

logger = logging.getLogger(__name__)


def _err_status(e) -> object:
    r = getattr(e, "response", None)
    return getattr(r, "status_code", None) if r is not None else None

# Response field names we accept for each token (upstreams differ).
# eTrans nests tokens under `result`: result.auth / result.refreshToken.
_ACCESS_KEYS = ("auth", "access_token", "accessToken", "token", "jwt", "id_token")
_REFRESH_KEYS = ("refreshToken", "refresh_token")
_EXPIRY_KEYS = ("expiration", "expires_at", "expiresAt")           # epoch milliseconds
_NEST_KEYS = ("result", "data")

_EPOCH = datetime(1970, 1, 1)


class TmsAuthError(RuntimeError):
    """Raised when login/refresh cannot produce a usable access token."""


def _extract(data: dict, keys) -> object | None:
    """First present, non-empty value among `keys`, searching nested envelopes
    (`result` / `data`) too."""
    if not isinstance(data, dict):
        return None
    for k in keys:
        v = data.get(k)
        if v:
            return v
    for nest in _NEST_KEYS:
        nested = data.get(nest)
        if isinstance(nested, dict):
            found = _extract(nested, keys)
            if found:
                return found
    return None


def _jwt_exp(token: str) -> datetime | None:
    """Read the `exp` claim from a JWT WITHOUT verifying the signature.

    We only use it to schedule refreshes, never for trust decisions.
    Returns None if the token isn't a decodable JWT with an exp.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to a multiple of 4
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return datetime.fromtimestamp(int(exp)) if exp else None
    except Exception:
        return None


class TmsAuthClient:
    def __init__(self):
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_expiry: datetime = _EPOCH
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #

    def get_token(self) -> str:
        """Return a valid access token, refreshing or re-logging in as needed.

        Never returns an expired token. Raises TmsAuthError if a fresh token
        cannot be obtained.
        """
        with self._lock:
            if self._access_token and datetime.now() < self._refresh_deadline():
                return self._access_token

            # Token missing or within the skew window — try a cheap refresh
            # first, then fall back to a full login.
            if self._refresh_token:
                try:
                    self._refresh_locked()
                    return self._access_token
                except Exception as e:
                    logger.warning("TMS token refresh failed (%s); re-logging in", e)

            self._login_locked()
            return self._access_token

    def invalidate(self, token: str | None = None) -> None:
        """Drop the cached access token so the next get_token() refreshes.

        Used by the fetch layer's 401-retry path. Pass the token that got the
        401 so concurrent callers don't stampede: if another thread already
        refreshed (current token differs from the failed one), this is a no-op.
        """
        with self._lock:
            if token is not None and token != self._access_token:
                return  # someone already rotated the token
            self._access_token = None
            self._access_expiry = _EPOCH

    def status(self) -> dict:
        """Non-secret snapshot for the /sync/status endpoint."""
        with self._lock:
            return {
                "authenticated": bool(self._access_token),
                "has_refresh_token": bool(self._refresh_token),
                "access_expires_at": (
                    self._access_expiry.isoformat()
                    if self._access_expiry != _EPOCH else None
                ),
            }

    # ------------------------------------------------------------------ #
    # Internal (call with self._lock held)
    # ------------------------------------------------------------------ #

    def _refresh_deadline(self) -> datetime:
        """Instant at which the cached token is considered too old to reuse."""
        return self._access_expiry - timedelta(seconds=settings.TMS_TOKEN_SKEW_SECONDS)

    def _login_locked(self) -> None:
        if not (settings.TMS_AUTH_URL and settings.TMS_USERNAME and settings.TMS_AUTH_KEY):
            raise TmsAuthError("TMS auth is not configured (TMS_AUTH_URL/USERNAME/AUTH_KEY)")
        logger.info("TMS login as %s", settings.TMS_USERNAME)
        payload = {
            "username": settings.TMS_USERNAME,
            "authKey": settings.TMS_AUTH_KEY,
            "accessMode": settings.TMS_ACCESS_MODE,
        }
        t0 = time.perf_counter()
        try:
            resp = requests.post(settings.TMS_AUTH_URL, json=payload,
                                 timeout=settings.TMS_API_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log_event("auth", method="POST", url=settings.TMS_AUTH_URL, payload=payload,
                      status=_err_status(e), elapsed_ms=round((time.perf_counter() - t0) * 1000),
                      error=str(e), detail="login")
            raise
        log_event("auth", method="POST", url=settings.TMS_AUTH_URL, payload=payload,
                  status=resp.status_code, elapsed_ms=round((time.perf_counter() - t0) * 1000),
                  detail="login")
        self._store_tokens(data, context="login")

    def _refresh_locked(self) -> None:
        # No refresh endpoint published yet — fall back to a full re-login,
        # which is equivalent (authKey re-login yields fresh tokens).
        if not settings.TMS_REFRESH_URL:
            self._login_locked()
            return
        payload = {"refreshToken": self._refresh_token, "refresh_token": self._refresh_token}
        t0 = time.perf_counter()
        try:
            resp = requests.post(settings.TMS_REFRESH_URL, json=payload,
                                 timeout=settings.TMS_API_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log_event("auth", method="POST", url=settings.TMS_REFRESH_URL, payload=payload,
                      status=_err_status(e), elapsed_ms=round((time.perf_counter() - t0) * 1000),
                      error=str(e), detail="refresh")
            raise
        log_event("auth", method="POST", url=settings.TMS_REFRESH_URL, payload=payload,
                  status=resp.status_code, elapsed_ms=round((time.perf_counter() - t0) * 1000),
                  detail="refresh")
        self._store_tokens(data, context="refresh")

    def _store_tokens(self, data: dict, context: str) -> None:
        access = _extract(data, _ACCESS_KEYS)
        if not access:
            msg = data.get("message") if isinstance(data, dict) else None
            raise TmsAuthError(f"No access token in {context} response"
                               + (f": {msg}" if msg else ""))
        self._access_token = str(access)

        # Refresh token: keep the previous one if the response omits it.
        refresh = _extract(data, _REFRESH_KEYS)
        if refresh:
            self._refresh_token = str(refresh)

        # Expiry: prefer the response's explicit epoch-ms expiration; else the
        # JWT's own exp claim; else the configured fallback TTL.
        expiry = None
        exp_ms = _extract(data, _EXPIRY_KEYS)
        if exp_ms is not None:
            try:
                expiry = datetime.fromtimestamp(int(exp_ms) / 1000)
            except (ValueError, TypeError, OSError):
                expiry = None
        if expiry is None:
            expiry = _jwt_exp(self._access_token)
        if expiry is None:
            expiry = datetime.now() + timedelta(seconds=settings.TMS_TOKEN_TTL_SECONDS)
        self._access_expiry = expiry
        logger.info("TMS %s ok; access token valid until %s", context, expiry.isoformat())


# Module-level singleton -------------------------------------------------- #

_client: TmsAuthClient | None = None
_client_lock = threading.Lock()


def get_auth_client() -> TmsAuthClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = TmsAuthClient()
    return _client
