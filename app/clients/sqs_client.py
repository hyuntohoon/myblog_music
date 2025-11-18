from __future__ import annotations

import os
import json as _json
import uuid
from functools import lru_cache
from typing import Iterable, List, Dict
from app.core.config import settings

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
        # --- 기본 환경 설정: 전부 settings 기반 ---
        self.region = region or settings.AWS_DEFAULT_REGION
        self.endpoint_url = (endpoint_url or (settings.LOCALSTACK_ENDPOINT or "")).rstrip("/") or None
        self.queue_name = queue_name or settings.QUEUE_NAME
        self.account_id = account_id or (settings.AWS_ACCOUNT_ID or "000000000000")

        # --- Queue URL 구성 (환경변수/인자 우선) ---
        self.queue_url = queue_url or settings.SQS_QUEUE_URL

        # queue_url만 있고 endpoint_url이 없으면 queue_url에서 엔드포인트 역추출
        if not self.endpoint_url and self.queue_url:
            from urllib.parse import urlparse
            p = urlparse(self.queue_url)
            self.endpoint_url = f"{p.scheme}://{p.netloc}"

        # 로컬 판별 (localhost/localstack 도메인)
        host = self.endpoint_url or ""
        self.is_local = bool(host and ("localhost" in host or "localstack" in host))

        # --- boto3 client 초기화 ---
        self._client = _get_boto_sqs(region=self.region, endpoint_url=self.endpoint_url)

        # --- Queue URL 최종 확정 ---
        if not self.queue_url:
            if self.is_local:
                self.queue_url = f"{self.endpoint_url}/{self.account_id}/{self.queue_name}"
            else:
                self.queue_url = self._client.get_queue_url(QueueName=self.queue_name)["QueueUrl"]

        self.is_fifo = self.queue_name.endswith(".fifo")

        print("[SQS INIT]", {
            "region": self.region,
            "endpoint_url": self.endpoint_url,
            "is_local": self.is_local,
            "queue_name": self.queue_name,
            "queue_url": self.queue_url,
        })
    def enqueue_album_sync(self, album_ids: Iterable[str], market: str) -> None:
        """
        앨범 ID들을 '메시지 1개당 최대 20개'로 묶어서 SQS에 전송.
        SQS 배치 전송은 엔트리 10개씩. 실패는 로깅만.
        """
        ids: List[str] = [sid for sid in album_ids if sid]
        if not ids:
            print("[SQS] No album IDs to enqueue")
            return

        GROUP = 20  # 메시지 1개에 담을 앨범ID 최대(Spotify /albums?ids= 한도)
        BATCH = 10  # SQS send_message_batch 엔트리 한도

        try:
            # 1) 앨범ID → 최대 20개씩 그룹으로 메시지 본문 생성
            grouped_bodies: List[str] = []
            for i in range(0, len(ids), GROUP):
                chunk = ids[i:i + GROUP]
                body = _json.dumps(
                    {"album_ids": chunk, "market": market},
                    separators=(",", ":"), ensure_ascii=False
                )
                grouped_bodies.append(body)

            print(f"[SQS] Prepared {len(grouped_bodies)} grouped message(s) "
                f"(total_ids={len(ids)}, group_size<=20)")

            # 2) 메시지 본문들을 SQS 배치(엔트리 10개)로 전송
            for i in range(0, len(grouped_bodies), BATCH):
                entries: List[Dict] = []
                for body in grouped_bodies[i:i + BATCH]:
                    entry = {
                        "Id": str(uuid.uuid4()),
                        "MessageBody": body,
                    }
                    if self.is_fifo:
                        entry["MessageGroupId"] = "album-sync"
                        # 같은 청크는 디듀프되도록 chunk 해시 기반으로 생성
                        entry["MessageDeduplicationId"] = str(uuid.uuid5(
                            uuid.NAMESPACE_URL, body + f":{market}"
                        ))
                    entries.append(entry)

                print(f"[SQS] Sending batch ({len(entries)}) to {self.queue_url}")
                print(f"[SQS] Example message: {entries[0]['MessageBody']}")
                response = self._client.send_message_batch(
                    QueueUrl=self.queue_url,
                    Entries=entries
                )
                print(f"[SQS] Response: {response}")

        except Exception as e:
            import traceback
            print("[SQS ERROR]", e)
            traceback.print_exc()


@lru_cache(maxsize=1)
def _get_boto_sqs(region: str, endpoint_url: str | None = None):
    """endpoint_url 있으면 LocalStack, 없으면 AWS 실서비스"""
    params = {"region_name": region}
    if endpoint_url:
        params["endpoint_url"] = endpoint_url
    return boto3.client("sqs", **params)