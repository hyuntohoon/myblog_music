from __future__ import annotations

import json
import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    APP_NAME: str = "music-backend"
    ENV: str = "local"

    # DB
    DATABASE_URL: str = ""

    # Spotify
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_TOKEN_URL: str = "https://accounts.spotify.com/api/token"
    SPOTIFY_API_BASE: str = "https://api.spotify.com/v1"
    SPOTIFY_DEFAULT_MARKET: str = "KR"

    # Cognito (auth for /candidates)
    COGNITO_REGION: str = "ap-northeast-2"
    COGNITO_USER_POOL_ID: str = ""

    # AWS / SQS
    AWS_DEFAULT_REGION: str = "ap-northeast-2"
    LOCALSTACK_ENDPOINT: str | None = None
    AWS_ACCOUNT_ID: str | None = None
    QUEUE_NAME: str = "test-queue"
    SQS_QUEUE_URL: str | None = None

    # Secrets Manager
    SECRETS_ARN: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _load_secrets(arn: str) -> dict:
    try:
        import boto3
        sm = boto3.client("secretsmanager", region_name="ap-northeast-2")
        val = sm.get_secret_value(SecretId=arn)
        return json.loads(val["SecretString"])
    except Exception as e:
        logger.error("Failed to load secrets from %s: %s", arn, e)
        return {}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.SECRETS_ARN:
        secrets = _load_secrets(s.SECRETS_ARN)
        if secrets.get("DATABASE_URL"):
            s.DATABASE_URL = secrets["DATABASE_URL"]
        if secrets.get("SPOTIFY_CLIENT_ID"):
            s.SPOTIFY_CLIENT_ID = secrets["SPOTIFY_CLIENT_ID"]
        if secrets.get("SPOTIFY_CLIENT_SECRET"):
            s.SPOTIFY_CLIENT_SECRET = secrets["SPOTIFY_CLIENT_SECRET"]
    return s


settings = get_settings()