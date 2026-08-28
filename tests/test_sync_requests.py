from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")

from fastapi.testclient import TestClient

from app.api.routers import sync_requests
from app.clients import sqs_client
from app.clients.sqs_client import SqsEnqueueError
from app.core import db as db_module
from app.domain.schemas import AlbumSyncAccepted
from app.main import app
from app.services import cadidate_search_service
from app.services.sync_request_service import SyncRequestService


class _ScalarResult:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def all(self) -> list[str]:
        return self.values


class _CatalogSession:
    def __init__(self, existing: list[str] | None = None) -> None:
        self.existing = existing or []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True

    def scalars(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.existing)


class _RecordingSqs:
    def __init__(self, session: _CatalogSession | None = None) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.session = session

    def enqueue_album_sync(self, album_ids, market: str) -> int:
        if self.session is not None:
            assert self.session.closed, "DB session must close before SQS send"
        self.calls.append((list(album_ids), market))
        return 1


class _ServiceStub:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def enqueue(self, _album_ids, _market):
        if self.error:
            raise self.error
        return self.result


def test_candidates_get_resolves_neither_db_nor_sqs(monkeypatch):
    def _unexpected_db():
        raise AssertionError("candidate GET must not resolve get_db")

    def _unexpected_sqs(*_args, **_kwargs):
        raise AssertionError("candidate GET must not construct SQS")

    monkeypatch.setattr(cadidate_search_service.spotify, "search", lambda **_kwargs: {})
    monkeypatch.setattr(sqs_client, "_get_boto_sqs", _unexpected_sqs)
    monkeypatch.setattr(db_module, "SessionLocal", _unexpected_db)
    response = TestClient(app).get(
        "/api/music/search/candidates",
        params={"q": "candidate"},
    )

    assert response.status_code == 200
    assert response.json() == {}


def test_sync_request_returns_202_and_filters_catalogued_ids(monkeypatch):
    session = _CatalogSession(["known"])
    sqs = _RecordingSqs(session)
    service = SyncRequestService(session_factory=lambda: session, sqs=sqs)
    result = service.enqueue(["new", "known", "new"], "kr")

    assert result == AlbumSyncAccepted(
        enqueued_album_ids=["new"],
        skipped_existing_album_ids=["known"],
        queued_message_count=1,
    )
    assert sqs.calls == [(["new"], "KR")]

    monkeypatch.setattr(sync_requests, "get_sync_request_service", lambda: _ServiceStub(result=result))
    response = TestClient(app).post(
        "/api/music/sync-requests",
        json={"album_ids": ["new", "known", "new"], "market": "kr"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "status": "accepted",
        "enqueued_album_ids": ["new"],
        "skipped_existing_album_ids": ["known"],
        "queued_message_count": 1,
    }
    assert session.closed


def test_sync_request_returns_safe_503_when_enqueue_fails(monkeypatch):
    monkeypatch.setattr(
        sync_requests,
        "get_sync_request_service",
        lambda: _ServiceStub(error=SqsEnqueueError("internal queue detail")),
    )
    response = TestClient(app).post(
        "/api/music/sync-requests",
        json={"album_ids": ["new"], "market": "KR"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "status": "failed",
        "message": "Album sync request could not be accepted",
    }
    assert "internal queue detail" not in response.text


def test_sync_request_returns_503_when_catalog_filter_fails(monkeypatch):
    class _FailingCatalogSession(_CatalogSession):
        def scalars(self, _statement):
            raise RuntimeError("database unavailable")

    service = SyncRequestService(
        session_factory=_FailingCatalogSession,
        sqs=_RecordingSqs(),
    )
    monkeypatch.setattr(sync_requests, "get_sync_request_service", lambda: service)
    response = TestClient(app).post(
        "/api/music/sync-requests",
        json={"album_ids": ["new"], "market": "KR"},
    )

    assert response.status_code == 503
    assert response.json()["status"] == "failed"


def test_sync_request_rejects_whitespace_album_id():
    response = TestClient(app).post(
        "/api/music/sync-requests",
        json={"album_ids": ["   "], "market": "KR"},
    )

    assert response.status_code == 422
