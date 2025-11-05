# tests/test_candidates_localstack_cli.py
import os, json, time, shlex, subprocess, importlib
import pytest

# ---- awslocal helpers ----
CONTAINER = os.getenv("LOCALSTACK_CONTAINER", "localstack")
REGION = os.getenv("AWS_REGION", "us-east-1")
QUEUE_URL = os.getenv("SQS_QUEUE_URL", "http://localhost:4566/000000000000/album-sync.fifo")

def _run(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True)

def awslocal_receive(queue_url: str, max_number: int = 10, wait_seconds: int = 1):
    cmd = (
        f"docker exec -e AWS_DEFAULT_REGION={REGION} -i {CONTAINER} "
        f"awslocal sqs receive-message "
        f"--queue-url {queue_url} "
        f"--max-number-of-messages {max_number} "
        f"--wait-time-seconds {wait_seconds}"
    )
    p = _run(cmd)
    if p.returncode != 0 or not p.stdout.strip():
        return []
    try:
        data = json.loads(p.stdout)
        return data.get("Messages", [])
    except json.JSONDecodeError:
        return []

def awslocal_delete(queue_url: str, receipt_handle: str):
    quoted_rh = shlex.quote(receipt_handle)
    cmd = (
        f"docker exec -e AWS_DEFAULT_REGION={REGION} -i {CONTAINER} "
        f"awslocal sqs delete-message "
        f"--queue-url {queue_url} "
        f"--receipt-handle {quoted_rh}"
    )
    return _run(cmd).returncode == 0

def awslocal_purge_all(queue_url: str):
    while True:
        messages = awslocal_receive(queue_url, max_number=10, wait_seconds=1)
        if not messages:
            break
        for m in messages:
            awslocal_delete(queue_url, m["ReceiptHandle"])

# ---- test ----
def test_candidates_enqueues_album_ids(monkeypatch):
    # 0) env 확정
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("LOCALSTACK_URL", "http://localhost:4566")   # ← endpoint_url로 쓰임
    monkeypatch.setenv("SQS_IS_FIFO", "true")
    monkeypatch.setenv("SQS_QUEUE_URL", "http://localhost:4566/000000000000/album-sync.fifo")
    monkeypatch.setenv("DEFAULT_MARKET", "KR")
    monkeypatch.setenv("SYNC_TEST_STRICT", "1")  # 예외 삼키지 말고 보이게

    # 1) 라우터 모듈 → 메인 순서로 리로드  ←★ 핵심
    import app.api.routers as search_router   # ← _sqs 정의된 파일 경로로 바꿔!
    importlib.reload(search_router)

    import app.main as main
    importlib.reload(main)

    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    # 2) 큐 비우기
    awslocal_purge_all(QUEUE_URL)

    # 3) spotify.search 모킹
    from app.clients import spotify_client
    mock_resp = {
        "albums": {"items":[{"id":"alb_111","name":"Mock Album","album_type":"album",
                             "release_date":"2022-01-01","images":[{"url":"http://img"}],
                             "artists":[{"id":"art_1","name":"Mock Artist"}],
                             "external_urls":{"spotify":"http://sp/alb_111"}}]},
        "artists":{"items":[]},
        "tracks":{"items":[
            {"id":"trk_1","name":"Song A","duration_ms":100000,"track_number":1,
             "album":{"id":"alb_111","name":"Mock Album","release_date":"2022-01-01","images":[{"url":"http://img"}]},
             "artists":[{"id":"art_1","name":"Mock Artist"}],"external_urls":{"spotify":"http://sp/trk_1"}},
            {"id":"trk_2","name":"Song B","duration_ms":120000,"track_number":2,
             "album":{"id":"alb_111","name":"Mock Album","release_date":"2022-01-01","images":[{"url":"http://img"}]},
             "artists":[{"id":"art_1","name":"Mock Artist"}],"external_urls":{"spotify":"http://sp/trk_2"}},
        ]}
    }
    monkeypatch.setattr(spotify_client.spotify, "search", lambda **kwargs: mock_resp)

    # 4) 호출
    r = client.get("/api/search/candidates",
                   params={"q":"album:Mock","type":"album,artist,track","market":"KR"})
    assert r.status_code == 200

    # 5) 수신/검증
    msgs = []
    for _ in range(10):
        for m in awslocal_receive(QUEUE_URL, max_number=10, wait_seconds=1):
            msgs.append(json.loads(m["Body"]))
            awslocal_delete(QUEUE_URL, m["ReceiptHandle"])
        if msgs: break
        time.sleep(0.5)

    assert len(msgs) == 1
    assert msgs[0]["spotify_album_id"] == "alb_111"
    assert msgs[0]["market"] == "KR"