"""Candidate GET / explicit sync POST contract against LocalStack SQS."""
from __future__ import annotations

import json
import os
import time
import uuid

import boto3
import pytest

# app.core.db builds its engine at import time. The route dependency is replaced
# below, but imports still need a parseable URL and must never discover ambient
# Neon configuration.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")

REGION = "us-east-1"
LOCALSTACK_ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")


class _EmptyScalarResult:
    def all(self) -> list:
        return []


class _EmptyCatalogSession:
    """Small deterministic get_db replacement used by AlbumRepository."""

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def scalars(self, _statement) -> _EmptyScalarResult:
        return _EmptyScalarResult()


@pytest.mark.integration
def test_candidates_get_is_pure_and_sync_post_enqueues_format_a(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    sqs = boto3.client(
        "sqs",
        region_name=REGION,
        endpoint_url=LOCALSTACK_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    queue_name = f"album-sync-{uuid.uuid4().hex}"
    queue_url = sqs.create_queue(QueueName=queue_name)["QueueUrl"]

    from fastapi.testclient import TestClient

    from app.api.routers import sync_requests
    from app.clients import spotify_client, sqs_client
    from app.clients.sqs_client import SqsClient
    from app.core.config import settings
    from app.main import app
    from app.services.sync_request_service import SyncRequestService

    monkeypatch.setattr(settings, "ENV", "local")
    monkeypatch.setattr(settings, "AWS_DEFAULT_REGION", REGION)
    monkeypatch.setattr(settings, "LOCALSTACK_ENDPOINT", LOCALSTACK_ENDPOINT)
    monkeypatch.setattr(settings, "QUEUE_NAME", queue_name)
    monkeypatch.setattr(settings, "SQS_QUEUE_URL", queue_url)
    sqs_client._get_boto_sqs.cache_clear()

    mock_resp = {
        "albums": {
            "items": [
                {
                    "id": "alb_111",
                    "name": "Mock Album",
                    "album_type": "album",
                    "release_date": "2022-01-01",
                    "images": [{"url": "http://img"}],
                    "artists": [{"id": "art_1", "name": "Mock Artist"}],
                    "external_urls": {"spotify": "http://sp/alb_111"},
                }
            ]
        },
        "artists": {"items": []},
        "tracks": {
            "items": [
                {
                    "id": "trk_1",
                    "name": "Song A",
                    "duration_ms": 100000,
                    "track_number": 1,
                    "album": {
                        "id": "alb_111",
                        "name": "Mock Album",
                        "release_date": "2022-01-01",
                        "images": [{"url": "http://img"}],
                    },
                    "artists": [{"id": "art_1", "name": "Mock Artist"}],
                    "external_urls": {"spotify": "http://sp/trk_1"},
                },
                {
                    "id": "trk_2",
                    "name": "Song B",
                    "duration_ms": 120000,
                    "track_number": 2,
                    "album": {
                        "id": "alb_111",
                        "name": "Mock Album",
                        "release_date": "2022-01-01",
                        "images": [{"url": "http://img"}],
                    },
                    "artists": [{"id": "art_1", "name": "Mock Artist"}],
                    "external_urls": {"spotify": "http://sp/trk_2"},
                },
            ]
        },
    }
    monkeypatch.setattr(spotify_client.spotify, "search", lambda **_kwargs: mock_resp)

    monkeypatch.setattr(sync_requests, "get_sync_request_service", lambda: SyncRequestService(
        session_factory=_EmptyCatalogSession,
        sqs=SqsClient(),
    ))
    try:
        client = TestClient(app)
        response = client.get(
            "/api/music/search/candidates",
            params={"q": "album:Mock", "type": "album,artist,track", "market": "KR"},
        )
        assert response.status_code == 200, response.text

        # GET is a pure Spotify read: no queue message is emitted.
        assert "Messages" not in sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
        )

        candidates = response.json()
        album_ids = list(dict.fromkeys(
            [item["spotify_id"] for item in candidates.get("albums", []) if item.get("spotify_id")]
            + [
                item["album"]["spotify_id"]
                for item in candidates.get("tracks", [])
                if item.get("album", {}).get("spotify_id")
            ]
        ))
        accepted = client.post(
            "/api/music/sync-requests",
            json={"album_ids": album_ids, "market": "KR"},
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["enqueued_album_ids"] == ["alb_111"]

        messages = []
        for _ in range(10):
            received = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=1,
            ).get("Messages", [])
            for message in received:
                messages.append(json.loads(message["Body"]))
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                )
            if messages:
                break
            time.sleep(0.5)

        assert messages == [{"album_ids": ["alb_111"], "market": "KR"}]
    finally:
        sqs_client._get_boto_sqs.cache_clear()
        sqs.delete_queue(QueueUrl=queue_url)
