from __future__ import annotations

import pytest

from app.clients import sqs_client
from app.clients.sqs_client import SqsClient, SqsEnqueueError


def test_enqueue_album_sync_raises_on_partial_batch_failure(monkeypatch):
    class _PartialFailureClient:
        def send_message_batch(self, **_kwargs):
            return {
                "Successful": [{"Id": "accepted"}],
                "Failed": [{"Id": "rejected"}],
            }

    monkeypatch.setattr(sqs_client, "_get_boto_sqs", lambda **_kwargs: _PartialFailureClient())
    client = SqsClient(
        queue_url="https://sqs.us-east-1.amazonaws.com/000000000000/blogSQS",
        queue_name="blogSQS",
    )

    with pytest.raises(SqsEnqueueError):
        # 21 IDs create two Format-A messages in the same SQS batch, so this is
        # a true partial success rather than a total rejection.
        client.enqueue_album_sync([f"album-{i}" for i in range(21)], "KR")
