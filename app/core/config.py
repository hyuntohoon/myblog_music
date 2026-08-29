from __future__ import annotations

import json
import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    APP_NAME: str = "music-backend"
    # SEC-2 (OPS-safety-net-drift Step 3): absence must be restrictive. ENV
    # gates the require_cognito_token bypass and the CORS localhost origins —
    # with a "local" default, a Lambda that ever lost its ENV var would
    # silently disable auth. Local dev opts in explicitly via ENV=local.
    # Twin of myblog_backend/app/core/config.py (auth guard bug class: a fix
    # must land in both repos in the same sweep).
    ENV: str = "prod"

    # DB
    DATABASE_URL: str = ""

    # Search (FEAT-music-search-recall Step 4 / A1). When true, the unified
    # search matcher adds a pg_trgm `similarity()` fuzzy fallback to the WHERE
    # (recovers one-edit typos that ILIKE substring misses) and a similarity
    # tiebreaker to the ORDER BY. Default false so the code can ship a full
    # deploy cycle BEFORE prod has the V12 pg_trgm extension — flipping the flag
    # against a DB without the extension would error. Requires V12 applied.
    SEARCH_USE_PG_TRGM: bool = False
    # Minimum trigram similarity for a fuzzy (non-substring) match to be admitted.
    # At/below the default 0.3 pg_trgm threshold on purpose — '방탄'↔'방탄소년단' =
    # 0.286 (RFC Step 3 caveat). Tuned against the recall gate.
    SEARCH_TRGM_THRESHOLD: float = 0.3

    # DATA-release-noise Step 1: read-side classical-compilation filter tunables
    # (app/services/compilation_filter.py). A catalog row is hidden from the home
    # feed + /releases/ calendar if it credits >= COMP_FILTER_MAX_ARTISTS distinct
    # artists, OR carries a pure-compilation label, OR matches a compilation title
    # family. Read-side only — rows stay in the catalog, so this is reversible.
    # Threshold 10 spares genuine classical performances (max seen: 8 artists on a
    # named-conductor Requiem); labels are ones that only ever press comps.
    COMP_FILTER_MAX_ARTISTS: int = 10
    COMP_FILTER_BUDGET_LABELS: list[str] = [
        "UME - Global Clearing House",
        "Novus Promusica",
        "Naxos Special Projects",
    ]

    # Spotify
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_TOKEN_URL: str = "https://accounts.spotify.com/api/token"
    SPOTIFY_API_BASE: str = "https://api.spotify.com/v1"
    SPOTIFY_DEFAULT_MARKET: str = "KR"

    # YouTube Data API v3 (FEAT-youtube-playback-provider Step A2).
    # Discovery only — read-only search.list + videos.list, no OAuth. Milestone B
    # (OAuth, playlist import) is gated on Phase 0-B and adds nothing here.
    #
    # The key lives in its OWN SSM parameter, not in `/myblog/music`, because the
    # Step A5 refresh job in myblog_worker reads the same credential. Copying it
    # into per-service blobs would create a rotation-drift surface for a key the
    # owner has already had to treat as exposed once.
    #
    # It is deliberately NOT in the required-key check below: music must keep
    # booting when YouTube is unconfigured. The YouTube endpoint fails closed on
    # its own (503) instead — an absent credential must never widen anything, but
    # it also must not take down album search.
    YOUTUBE_SECRETS_PARAM: str = ""
    YOUTUBE_API_KEY: str = ""
    YOUTUBE_API_BASE: str = "https://www.googleapis.com/youtube/v3"
    # PER CALL, and one request makes TWO sequential calls, so this is a budget
    # of 2x. musicApi's Lambda timeout is 15s (workspace infra/lambda.tf): at
    # 10s per call a slow-but-not-failing YouTube would kill the function before
    # either timeout fired, and the member would get a bare API Gateway 502
    # instead of the 429/503/502 taxonomy. 4.0 leaves 7s for the DB read, the
    # response and a cold start. Asserted by a test against the 15s figure.
    YOUTUBE_HTTP_TIMEOUT: float = 4.0
    # Candidates shown to the member. 10 keeps one search.list page cheap to
    # enrich (videos.list takes 50 ids at 1 unit, so the enrichment is free at
    # any value here) while staying a list a human can actually read.
    YOUTUBE_SEARCH_MAX_RESULTS: int = 10

    # Cognito (auth for /candidates)
    COGNITO_REGION: str = "ap-northeast-2"
    COGNITO_USER_POOL_ID: str = ""
    # SEC-system-hardening: Cognito app clients whose tokens this service accepts,
    # comma-separated. Empty is a MISCONFIGURATION and fails closed (503), never
    # "accept any client in the pool" — see app/core/auth.py. Set from
    # infra/lambda.tf so a client can be added or retired without a code deploy.
    COGNITO_ALLOWED_CLIENT_IDS: str = ""

    # AWS / SQS
    AWS_DEFAULT_REGION: str = "ap-northeast-2"
    LOCALSTACK_ENDPOINT: str | None = None
    AWS_ACCOUNT_ID: str | None = None
    QUEUE_NAME: str = "test-queue"
    SQS_QUEUE_URL: str | None = None

    # Runtime secrets: SSM Parameter Store ONLY (CHORE-secrets-ssm-migration).
    # SECRETS_PARAM is an SSM SecureString name like /myblog/music. The legacy
    # Secrets Manager fallback (SECRETS_ARN) was removed once the migration
    # completed — AWS Secrets Manager holds zero secrets in this account, so the
    # fallback could only ever turn an SSM failure into a silent empty load.
    SECRETS_PARAM: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _load_secrets(param: str) -> dict:
    """Load the secret JSON dict from SSM Parameter Store (SecureString).

    SSM is the only source (CHORE-secrets-ssm-migration). A failure is raised,
    not swallowed: the caller's required-key check below would have turned a
    returned ``{}`` into a ValueError naming the wrong subsystem, and an
    IAM/network failure is not the same condition as "the parameter is missing
    a key". Same shape in ``myblog_backend`` and ``myblog_worker``.

    Everything that can fail is inside the ``try``: constructing the client
    (``NoRegionError``) and parsing the value (``JSONDecodeError``) are as much
    "the load failed" as the API call is, and each must still produce a log line
    naming the parameter.
    """
    import boto3

    try:
        ssm = boto3.client("ssm", region_name="ap-northeast-2")
        raw = ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
        return json.loads(raw)
    except Exception as e:
        logger.error("SSM load failed for %s: %s", param, e)
        raise


# --- Required-secret contract for the /myblog/music SecureString -------------
#
# Derived from the runtime call sites, not from what the parameter happens to
# contain today. This sweep ports myblog_backend's *mechanism*; it deliberately
# does NOT widen the set, which is the one this service already enforced.
#
# DATABASE_URL — `app/db/session.py` builds the engine at import time and every
#   `/api/music` route is a DB read, so an absent key is a total outage. Unlike
#   myblog_backend, the Settings default here is "" rather than a localhost DSN,
#   so absence cannot silently point the Lambda at 127.0.0.1 — but it still
#   surfaces at request time as a driver error naming neither SSM nor this
#   parameter.
# SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET — the client-credentials token mint
#   in `app/services/spotify_client.py`. `/api/music/search/unified` is DB-only
#   (CLAUDE.md), so these do not gate search, but the candidates path and every
#   album/artist enrich read does depend on them.
#
# COGNITO_USER_POOL_ID / COGNITO_ALLOWED_CLIENT_IDS are not in this parameter at
# all — they arrive as Lambda env vars from `infra/lambda.tf`, and
# `app/core/auth.py` already fails closed (503) on each.
REQUIRED_SECRET_KEYS: tuple[str, ...] = (
    "DATABASE_URL",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
)
# No degradable key lives in /myblog/music today. Kept (empty) so that adding one
# is a single tuple entry instead of a re-derivation of the twin's shape.
OPTIONAL_SECRET_KEYS: tuple[str, ...] = ()


def _present(value: object) -> bool:
    """True when `value` is a usable secret string.

    A key written as `null`, `""`, or `"   "` is as absent as a key that is not
    there at all — SSM parameters are hand-edited JSON and all three shapes have
    a plausible way of happening. Non-strings are rejected rather than coerced:
    every field these feed is typed `str`, and this model does not set
    `validate_assignment`, so a JSON number assigned here would sit in a `str`
    field until some caller concatenated it and raised somewhere unrelated.

    The predicate is the same one ``myblog_backend`` and ``myblog_worker`` apply;
    the prose around it is per-service, so compare the ``return``, not the text.
    """
    return isinstance(value, str) and bool(value.strip())


def _apply_secrets(s: Settings, secrets: object, param: str) -> None:
    """Copy the loaded secret payload onto `s`, failing closed on a missing key.

    Presence is decided from the PAYLOAD, never from the resulting attribute.
    The `if not s.DATABASE_URL` formulation this replaced asked the wrong
    question in three ways, each demonstrated against this loader rather than
    inferred:

    - `{"DATABASE_URL": "   "}` is truthy, so it was copied over and then passed
      the check — the service booted holding a whitespace DSN.
    - `{"SPOTIFY_CLIENT_ID": 12345}` is truthy too, and with no
      `validate_assignment` on this model the `int` was stored verbatim in a
      `str` field.
    - A Lambda **env var** left behind under the same name satisfied the check
      for a key the parameter never supplied. Dormant today (`musicApi` carries
      no such variable) but it is a hole that opens silently.

    Values are assigned verbatim. Whitespace is used to judge presence but never
    stripped: these are compared byte-for-byte by the services that consume them
    (Spotify rejects a trimmed secret; a DSN is no safer to rewrite silently).

    Raises ValueError naming the parameter and the missing KEYS. No secret value
    is included, and none is logged.
    """
    if not isinstance(secrets, dict):
        raise ValueError(
            f"{param} must hold a JSON object of secret keys; parsed as "
            f"{type(secrets).__name__}. Fix the SecureString value."
        )

    missing = [k for k in REQUIRED_SECRET_KEYS if not _present(secrets.get(k))]
    if missing:
        raise ValueError(
            f"Required secrets missing from {param}: {sorted(missing)}. "
            "The parameter was read and parsed as JSON, so this is not an SSM or "
            "IAM failure — these keys are absent, empty, or not strings. The "
            "process refuses to start rather than serve on defaults: every "
            "/api/music route is a DB read, and the Spotify credentials mint the "
            "client-credentials token the candidates and enrich paths need."
        )

    for key in OPTIONAL_SECRET_KEYS:
        if not _present(secrets.get(key)):
            logger.warning(
                "%s has no usable %s; boot continues by design (this key is "
                "optional — requiring it would take every route down for one "
                "degradable integration).",
                param,
                key,
            )

    s.DATABASE_URL = secrets["DATABASE_URL"]
    s.SPOTIFY_CLIENT_ID = secrets["SPOTIFY_CLIENT_ID"]
    s.SPOTIFY_CLIENT_SECRET = secrets["SPOTIFY_CLIENT_SECRET"]


@lru_cache
def get_settings() -> Settings:
    s = Settings()

    # Gated on SECRETS_PARAM, which only a deployed environment sets
    # (infra/lambda.tf). Local dev and the test suite leave it empty, take no
    # SSM call, and are unaffected by the required-key contract above.
    if s.SECRETS_PARAM:
        _apply_secrets(s, _load_secrets(s.SECRETS_PARAM), s.SECRETS_PARAM)

    # Loaded separately and NOT required: see YOUTUBE_SECRETS_PARAM above.
    # A failure here must not stop music from serving album search, so the
    # exception is logged and swallowed — and the endpoint then fails closed
    # because YOUTUBE_API_KEY stays empty. That is the only place in this file
    # where a swallowed SSM failure is correct, and it is correct precisely
    # because the fallback state is "refuse", not "proceed unauthenticated".
    if s.YOUTUBE_SECRETS_PARAM and not s.YOUTUBE_API_KEY:
        try:
            s.YOUTUBE_API_KEY = _load_secrets(s.YOUTUBE_SECRETS_PARAM).get("YOUTUBE_API_KEY", "")
        except Exception:
            logger.error(
                "YouTube secret load failed for %s; the YouTube endpoints will fail closed.",
                s.YOUTUBE_SECRETS_PARAM,
            )
    return s


settings = get_settings()
