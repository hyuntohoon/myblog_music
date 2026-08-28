from __future__ import annotations

from collections.abc import Callable
from typing import Iterable

from sqlalchemy.orm import Session

from app.clients.sqs_client import SqsClient
from app.domain.schemas import AlbumSyncAccepted
from app.repositories.album_repo import AlbumRepository


class SyncRequestService:
    """Filter catalogued Spotify albums and explicitly dispatch Format-A jobs."""

    def __init__(self, session_factory: Callable[[], Session], sqs: SqsClient) -> None:
        self.session_factory = session_factory
        self.sqs = sqs

    def enqueue(self, album_ids: Iterable[str], market: str) -> AlbumSyncAccepted:
        ordered_ids = list(dict.fromkeys(album_id.strip() for album_id in album_ids if album_id.strip()))
        # Materialize the catalog read and close its transaction before the
        # external SQS loop. Neon may drop idle-in-transaction connections, and
        # the queue client can perform several send_message_batch calls.
        with self.session_factory() as db:
            existing_ids = AlbumRepository(db).get_existing_spotify_ids(
                ordered_ids,
                fail_on_error=True,
            )
        new_ids = [album_id for album_id in ordered_ids if album_id not in existing_ids]
        skipped_ids = [album_id for album_id in ordered_ids if album_id in existing_ids]
        message_count = self.sqs.enqueue_album_sync(new_ids, market.upper()) if new_ids else 0
        return AlbumSyncAccepted(
            enqueued_album_ids=new_ids,
            skipped_existing_album_ids=skipped_ids,
            queued_message_count=message_count,
        )
