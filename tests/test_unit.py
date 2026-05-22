"""Unit tests — no external services required."""
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("SPOTIFY_CLIENT_ID", "test")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test")


class TestCognitoAuthBypass:
    """require_cognito_token must bypass when ENV is local/dev or pool ID is unset."""

    def test_bypasses_when_env_local(self, monkeypatch):
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "local")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "some-pool")
        result = auth.require_cognito_token(credentials=None)
        assert result == {}

    def test_bypasses_when_env_dev(self, monkeypatch):
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "dev")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "some-pool")
        result = auth.require_cognito_token(credentials=None)
        assert result == {}

    def test_bypasses_when_pool_id_empty(self, monkeypatch):
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "")
        result = auth.require_cognito_token(credentials=None)
        assert result == {}

    def test_raises_401_when_no_token_in_prod(self, monkeypatch):
        from fastapi import HTTPException
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "ap-northeast-2_abc123")
        with pytest.raises(HTTPException) as exc_info:
            auth.require_cognito_token(credentials=None)
        assert exc_info.value.status_code == 401

    def test_raises_401_on_invalid_jwt_in_prod(self, monkeypatch):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from app.core import auth, config
        monkeypatch.setattr(config.settings, "ENV", "prod")
        monkeypatch.setattr(config.settings, "COGNITO_USER_POOL_ID", "ap-northeast-2_abc123")
        monkeypatch.setattr(auth, "_get_jwks", lambda: {"keys": []})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.valid.jwt")
        with pytest.raises(HTTPException) as exc_info:
            auth.require_cognito_token(credentials=creds)
        assert exc_info.value.status_code == 401
