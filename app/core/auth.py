from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any, Dict

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _get_jwks() -> Dict[str, Any]:
    url = (
        f"https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com"
        f"/{settings.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    )
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


# SEC-system-hardening: bound the kid-miss refetch.
#
# An unknown `kid` used to call `_get_jwks.cache_clear()` unconditionally before
# returning 401. That path is reachable by anyone who can base64 a JWT header —
# no signature, no valid claims — so N junk requests produced N outbound fetches
# to Cognito's JWKS endpoint, billed to and rate-limited against this account,
# and each one made concurrent legitimate requests pay a fresh 10s-timeout round
# trip. Key rotation is still picked up — a rotated key produces exactly this kid
# miss — just at a bounded rate rather than once per request.
#
# Caveat, unmeasured: `time.monotonic()` is CLOCK_MONOTONIC, which is not
# guaranteed to advance while a Lambda microVM is frozen between invocations. On a
# low-traffic container "60 seconds" of monotonic time can therefore span much more
# wall time, widening the window in which a rotated key returns 401. Verifying that
# needs a real Lambda logging monotonic() against time() across a cold gap, which
# has not been done. AWS does not auto-rotate user-pool signing keys today, so this
# is a documented unknown rather than a live risk.
#
# Twin of myblog_backend/app/core/auth.py. Fix both copies in the same change.
_JWKS_REFRESH_MIN_INTERVAL_SECONDS = 60.0
_jwks_last_refresh = 0.0
_jwks_refreshes = 0


def _refresh_jwks_if_due() -> None:
    """Drop the cached JWKS, at most once per `_JWKS_REFRESH_MIN_INTERVAL_SECONDS`."""
    global _jwks_last_refresh, _jwks_refreshes
    now = time.monotonic()
    if _jwks_last_refresh and now - _jwks_last_refresh < _JWKS_REFRESH_MIN_INTERVAL_SECONDS:
        return
    _jwks_last_refresh = now
    _jwks_refreshes += 1
    _get_jwks.cache_clear()


def _jwks_refresh_count() -> int:
    """Refreshes performed since process start. For tests and diagnostics."""
    return _jwks_refreshes


def _reset_jwks_refresh_throttle() -> None:
    """Test seam — the throttle is process-global state."""
    global _jwks_last_refresh, _jwks_refreshes
    _jwks_last_refresh = 0.0
    _jwks_refreshes = 0


def _allowed_client_ids() -> frozenset[str]:
    """The Cognito app clients whose tokens this service accepts.

    Comma-separated in config so a client can be added or retired from Terraform
    without a code deploy.
    """
    raw = settings.COGNITO_ALLOWED_CLIENT_IDS or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def require_cognito_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Dict[str, Any]:
    if settings.ENV in ("local", "dev"):
        return {}

    # FIX-bug-audit-2026-07 WS-A: in prod a missing pool id is a MISCONFIGURATION,
    # not a reason to skip auth. Fail CLOSED — never `or not COGNITO_USER_POOL_ID:
    # return {}`, which silently un-gated candidate Spotify reads and explicit
    # sync-request SQS enqueues if the musicApi Lambda env ever dropped the pool id. Mirrors the fail-closed
    # posture already in myblog_backend/app/core/auth.py (AUTH-5).
    if not settings.COGNITO_USER_POOL_ID:
        logger.error(
            "COGNITO_USER_POOL_ID unset while ENV=%s — refusing to fail open",
            settings.ENV,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured",
        )

    # SEC-system-hardening: the app-client binding is configuration, so an unset
    # allowlist is a misconfiguration and takes the same posture as an unset pool
    # id — 503, never "skip the check". This service sits behind NO API Gateway
    # authorizer at all (infra/apigateway.tf attaches it to 51 of 55 routes and to
    # none of /api/music/*), so this in-process check is the only place the app
    # client is bound for the whole music service.
    allowed_clients = _allowed_client_ids()
    if not allowed_clients:
        logger.error(
            "COGNITO_ALLOWED_CLIENT_IDS unset while ENV=%s — refusing to fail open",
            settings.ENV,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth not configured",
        )

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        try:
            jwks = _get_jwks()
        except httpx.HTTPError as e:
            # STAB-2 Step 4: a Cognito JWKS fetch failure (network/timeout/5xx)
            # is an upstream availability issue, not a bad token. Surface 503
            # instead of letting the HTTPError escape as an unhandled 500 — this
            # path is first exercised in prod once ENV=prod gates /candidates.
            # Not cached (lru_cache only stores successes), so the next request
            # retries. Mirrors myblog_backend/app/core/auth.py.
            logger.error("JWKS fetch failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Auth provider unavailable",
            )
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if key is None:
            _refresh_jwks_if_due()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown token key")

        issuer = (
            f"https://cognito-idp.{settings.COGNITO_REGION}.amazonaws.com"
            f"/{settings.COGNITO_USER_POOL_ID}"
        )
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_at_hash": False},
        )

        token_use = claims.get("token_use")
        if token_use not in ("access", "id"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

        # SEC-system-hardening: bind the token to an app client we actually issue
        # to. Cognito puts the client on `client_id` for access tokens and `aud`
        # for id tokens — but the `aud` half is unreachable today, because
        # `jwt.decode` is called without `audience=` and jose rejects any token
        # carrying `aud` before this line. It is written out so that whoever does
        # add ID-token support has the binding already correct rather than absent. Until now nothing here looked at either, so ANY token
        # minted by this user pool was accepted on the candidates/sync-request
        # pair that drives synchronous Spotify reads and explicit SQS enqueues.
        presented_client = claims.get("client_id") if token_use == "access" else claims.get("aud")
        if presented_client not in allowed_clients:
            logger.warning(
                "token for app client %r rejected — not in COGNITO_ALLOWED_CLIENT_IDS",
                presented_client,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token client"
            )

        return claims

    except JWTError as e:
        logger.warning("JWT validation failed: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
