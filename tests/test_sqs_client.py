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


def test_boto_sqs_client_has_explicit_timeouts():
    """CLAUDE.md: every outbound request has an explicit timeout (sweep with backend #189)."""
    from unittest.mock import patch

    from app.clients import sqs_client

    sqs_client._get_boto_sqs.cache_clear()
    try:
        with patch.object(sqs_client.boto3, "client") as boto:
            sqs_client._get_boto_sqs("ap-northeast-2", None)
        cfg = boto.call_args.kwargs["config"]
        assert (cfg.connect_timeout, cfg.read_timeout) == (3, 5)
    finally:
        sqs_client._get_boto_sqs.cache_clear()
