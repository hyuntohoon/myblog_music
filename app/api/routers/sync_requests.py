from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from app.clients.sqs_client import SqsClient
from app.core.auth import require_cognito_token
from app.core.db import SessionLocal
from app.domain.schemas import AlbumSyncAccepted, AlbumSyncFailed, AlbumSyncRequest
from app.services.sync_request_service import SyncRequestService

logger = logging.getLogger(__name__)
router = APIRouter()


def get_sync_request_service() -> SyncRequestService:
    return SyncRequestService(session_factory=SessionLocal, sqs=SqsClient())


@router.post(
    "",
    response_model=AlbumSyncAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": AlbumSyncFailed,
            "description": "The queue could not accept every requested album-sync message.",
        }
    },
    summary="앨범 동기화 요청 enqueue",
)
def create_sync_request(
    request: AlbumSyncRequest,
    _claims: dict = Depends(require_cognito_token),
):
    try:
        service = get_sync_request_service()
        return service.enqueue(request.album_ids, request.market)
    except Exception:
        # Do not echo provider errors, queue identifiers, or configuration details.
        logger.exception("Album sync request was not accepted by the queue")
        failure = AlbumSyncFailed()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=failure.model_dump(),
        )
