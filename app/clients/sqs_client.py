from __future__ import annotations

import os
import json as _json
import uuid
from functools import lru_cache
from typing import Iterable, List, Dict

import boto3


class SqsClient:
    """SQS 전송 전용 클라이언트 (LocalStack ↔ AWS 자동 전환)"""

    def __init__(
        self,
        region: str | None = None,
        endpoint_url: str | None = None,
        queue_url: str | None = None,
        queue_name: str | None = None,
        account_id: str | None = None,
    ) -> None:
        # --- 기본 환경 설정 ---
        self.region = region or os.getenv("AWS_DEFAULT_REGION", "ap-northeast-2")
        self.endpoint_url = (endpoint_url or os.getenv("LOCALSTACK_ENDPOINT", "")).rstrip("/") or None
        self.queue_name = queue_name or os.getenv("QUEUE_NAME", "test-queue")
        self.account_id = account_id or os.getenv("AWS_ACCOUNT_ID", "000000000000")

        # --- 실행 환경 감지 ---
        # LOCALSTACK_ENDPOINT가 지정되어 있으면 로컬 모드로 판단
        self.is_local = bool(self.endpoint_url and "localhost" in self.endpoint_url)

        # --- Queue URL 구성 ---
        self.queue_url = (
            queue_url
            or os.getenv("SQS_QUEUE_URL")
        )
        self.is_fifo = self.queue_name.endswith(".fifo")

        # --- boto3 client 초기화 ---
        self._client = _get_boto_sqs(region=self.region, endpoint_url=self.endpoint_url)

        # --- 실제 AWS 환경이면 Queue URL 자동 조회 ---
        if not self.is_local and not self.queue_url:
            self.queue_url = self._client.get_queue_url(QueueName=self.queue_name)["QueueUrl"]

    def enqueue_album_sync(self, album_ids: Iterable[str], market: str) -> None:
        """앨범 ID들을 10개 배치로 전송. 실패는 조용히 무시."""
        ids: List[str] = [sid for sid in album_ids if sid]
        if not ids:
            return

        BATCH = 10
        try:
            for i in range(0, len(ids), BATCH):
                chunk = ids[i : i + BATCH]
                entries: List[Dict] = []
                for sid in chunk:
                    entry = {
                        "Id": str(uuid.uuid4()),
                        "MessageBody": _json.dumps(
                            {"spotify_album_id": sid, "market": market},
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    }
                    if self.is_fifo:
                        entry["MessageGroupId"] = "album-sync"
                        entry["MessageDeduplicationId"] = f"{sid}:{market}"
                    entries.append(entry)
                self._client.send_message_batch(QueueUrl=self.queue_url, Entries=entries)
        except Exception:
            # 로컬/프로덕션 상관없이 조용히 무시
            pass


@lru_cache(maxsize=1)
def _get_boto_sqs(region: str, endpoint_url: str | None = None):
    """endpoint_url 있으면 LocalStack, 없으면 AWS 실서비스"""
    params = {"region_name": region}
    if endpoint_url:
        params["endpoint_url"] = endpoint_url
    return boto3.client("sqs", **params)