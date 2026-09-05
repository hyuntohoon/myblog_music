"""FEAT-youtube-playback-provider Step A2 — YouTube candidate search.

Two things these tests are deliberately built to catch, both of which have bitten
this project before:

1. A STUB THAT RETURNS INSTANTLY erases the async window a real HTTP call opens.
   The client stub here sleeps, so a caller that forgot to await/serialise shows
   up as wrong ordering rather than as an accidental pass.
2. A STUB PAYLOAD THAT THE READER REJECTS makes every assertion vacuous. The
   service DROPS any video whose `status.embeddable` is not exactly True, so a
   stub missing that field yields zero candidates and a lazy "did not crash"
   test passes against a service that does nothing. Every happy-path test below
   therefore asserts on candidate CONTENT, never merely on the status code.
"""
from __future__ import annotations

import os

# Must precede every `app.` import: app.core.db builds its engine from
# settings.DATABASE_URL at import time, and app.core.config caches Settings via
# lru_cache — so a value set after the first import is never read. Same
# placement as tests/test_sync_requests.py.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")

import time

import pytest

from app.clients.youtube_client import (
    QUOTA_REASONS,
    VIDEOS_LIST_MAX_IDS,
    YouTubeClient,
    YouTubeError,
    YouTubeNotConfigured,
    YouTubeQuotaExhausted,
)
from app.services.youtube_candidate_service import (
    YouTubeCandidateService,
    parse_iso8601_duration,
)


# --------------------------------------------------------------------------
# Duration parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "iso,expected",
    [
        ("PT4M13S", 253),
        ("PT1H2M3S", 3723),
        ("PT45S", 45),
        ("PT7M", 420),
        ("P1DT1S", 86401),
        # Both zero forms must read as UNKNOWN, not as a zero-second video —
        # zero wins every proximity comparison and would rank a 24/7 stream
        # first. `P0D` is the shape the live API returns for a stream (measured
        # 2026-09-06); `PT0S` is what exercises the zero-guard itself, since
        # `P0D` alone could be satisfied by a regex that merely rejects it.
        ("P0D", None),
        ("PT0S", None),
        ("P0DT0S", None),
        # A bare `P1D` (no time part) must still parse — the `T` section is
        # optional in the emitted shape.
        ("P1D", 86400),
        ("", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_iso8601_duration(iso, expected):
    assert parse_iso8601_duration(iso) == expected


# --------------------------------------------------------------------------
# Query construction
# --------------------------------------------------------------------------

def test_build_query_is_artist_then_title():
    s = YouTubeCandidateService()
    assert s.build_query(title="Smells Like Teen Spirit", artist_name="Nirvana") == \
        "Nirvana Smells Like Teen Spirit"


def test_build_query_survives_a_missing_artist():
    s = YouTubeCandidateService()
    assert s.build_query(title="Untitled", artist_name=None) == "Untitled"
    assert s.build_query(title="Untitled", artist_name="   ") == "Untitled"


# --------------------------------------------------------------------------
# A stub that models latency and satisfies every reader-side required field
# --------------------------------------------------------------------------

def _video(vid, *, title, channel, duration, embeddable=True, privacy="public", mfk=False):
    """A videos.list item carrying EVERY field the service reads.

    Kept as one builder so a future required field is added in one place — a
    per-test hand-written dict is how a payload silently drifts below the
    reader's validation and turns the suite green against a broken service.
    """
    return {
        "id": vid,
        "snippet": {
            "title": title,
            "channelTitle": channel,
            "thumbnails": {"medium": {"url": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"}},
        },
        "status": {"embeddable": embeddable, "privacyStatus": privacy, "madeForKids": mfk},
        "contentDetails": {"duration": duration},
    }


class StubYouTube:
    """Stands in for `youtube`. Sleeps like a network call does."""

    LATENCY = 0.02

    def __init__(self, ids, details, *, search_error=None, videos_error=None):
        self.ids, self.details = ids, details
        self.search_error, self.videos_error = search_error, videos_error
        self.search_calls, self.videos_calls = 0, 0
        self.last_query = None

    def search_videos(self, *, q, max_results=10):
        self.search_calls += 1
        self.last_query = q
        time.sleep(self.LATENCY)
        if self.search_error:
            raise self.search_error
        return list(self.ids)[:max_results]

    def list_videos(self, video_ids):
        self.videos_calls += 1
        time.sleep(self.LATENCY)
        if self.videos_error:
            raise self.videos_error
        return {v: self.details[v] for v in video_ids if v in self.details}


@pytest.fixture
def patch_youtube(monkeypatch):
    def _apply(stub):
        monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
        return stub
    return _apply


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def test_candidates_are_ranked_by_duration_proximity_not_search_order(patch_youtube):
    """The whole point of A2: YouTube's #1 is not automatically our #1."""
    ids = ["farA", "closeB", "midC"]
    stub = patch_youtube(StubYouTube(ids, {
        "farA":   _video("farA",   title="Live at Reading",   channel="Fan Uploads", duration="PT9M00S"),
        "closeB": _video("closeB", title="Official Audio",    channel="Nirvana - Topic", duration="PT5M02S"),
        "midC":   _video("midC",   title="Official Video",    channel="NirvanaVEVO", duration="PT5M20S"),
    }))
    out = YouTubeCandidateService().find_candidates(
        title="Smells Like Teen Spirit", artist_name="Nirvana",
        track_duration_sec=301, max_results=10,
    )
    assert [c["video_id"] for c in out] == ["closeB", "midC", "farA"]
    assert [c["duration_delta_sec"] for c in out] == [1, 19, 239]
    # Content assertions — these are what make the test non-vacuous.
    assert out[0]["channel_title"] == "Nirvana - Topic"
    assert out[0]["thumbnail_url"].startswith("https://i.ytimg.com/")
    assert out[0]["embeddable"] is True
    assert stub.search_calls == 1 and stub.videos_calls == 1
    assert stub.last_query == "Nirvana Smells Like Teen Spirit"


def test_search_rank_breaks_ties_when_deltas_are_equal(patch_youtube):
    patch_youtube(StubYouTube(["first", "second"], {
        "first":  _video("first",  title="A", channel="ch", duration="PT5M00S"),
        "second": _video("second", title="B", channel="ch", duration="PT5M00S"),
    }))
    out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["first", "second"]


def test_unknown_duration_sorts_last_but_is_still_offered(patch_youtube):
    """"Unrankable" and "wrong" are different; only the member can tell which."""
    patch_youtube(StubYouTube(["stream", "known"], {
        "stream": _video("stream", title="24/7 radio", channel="lofi", duration="P0D"),
        "known":  _video("known",  title="Official",   channel="ch",   duration="PT5M10S"),
    }))
    out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["known", "stream"]
    assert out[-1]["duration_sec"] is None and out[-1]["duration_delta_sec"] is None


def test_no_track_duration_keeps_search_order_and_drops_no_candidate(patch_youtube):
    """`tracks.duration_sec` is nullable. Absent duration must not empty the list."""
    patch_youtube(StubYouTube(["a", "b"], {
        "a": _video("a", title="A", channel="ch", duration="PT3M00S"),
        "b": _video("b", title="B", channel="ch", duration="PT4M00S"),
    }))
    out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="ar", track_duration_sec=None, max_results=10)
    assert [c["video_id"] for c in out] == ["a", "b"]
    assert all(c["duration_delta_sec"] is None for c in out)


# --------------------------------------------------------------------------
# Filtering — the predicate must match the resolve read (OQ9)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad,label", [
    ({"embeddable": False}, "embedding disabled by the owner"),
    ({"embeddable": None}, "embeddable unknown"),
    ({"privacy": "unlisted"}, "not public"),
    ({"privacy": "private"}, "private"),
])
def test_unplayable_candidates_are_never_offered(patch_youtube, bad, label):
    patch_youtube(StubYouTube(["bad", "good"], {
        "bad":  _video("bad",  title="X", channel="ch", duration="PT5M00S", **bad),
        "good": _video("good", title="Y", channel="ch", duration="PT5M00S"),
    }))
    out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["good"], label


def test_ids_absent_from_videos_list_are_dropped(patch_youtube):
    """search.list can return an id videos.list omits — deleted between calls.

    Measured 2026-09-06: `videos.list` reports such an id by OMISSION, never as
    an error, so a service that assumed a 1:1 response would KeyError here.
    """
    patch_youtube(StubYouTube(["ghost", "real"], {
        "real": _video("real", title="Y", channel="ch", duration="PT5M00S"),
    }))
    out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["real"]


def test_empty_search_result_makes_no_enrichment_call(patch_youtube):
    """A wasted videos.list is 1 unit; a wasted round trip is the member waiting."""
    stub = patch_youtube(StubYouTube([], {}))
    assert YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10) == []
    assert stub.search_calls == 1 and stub.videos_calls == 0


def test_blank_track_makes_no_search_call_at_all(patch_youtube):
    """search.list costs 100 units. An empty query must never spend one."""
    stub = patch_youtube(StubYouTube(["x"], {"x": _video("x", title="X", channel="c", duration="PT1M")}))
    assert YouTubeCandidateService().find_candidates(
        title="", artist_name=None, track_duration_sec=300, max_results=10) == []
    assert stub.search_calls == 0


# --------------------------------------------------------------------------
# Quota exhaustion is an outcome, not a retry
# --------------------------------------------------------------------------

def test_quota_exhaustion_propagates_and_is_not_retried(patch_youtube):
    stub = patch_youtube(StubYouTube(["a"], {}, search_error=YouTubeQuotaExhausted("quotaExceeded")))
    with pytest.raises(YouTubeQuotaExhausted):
        YouTubeCandidateService().find_candidates(
            title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert stub.search_calls == 1, "a retry loop would multiply one exhausted quota into a burst of 403s"


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code, self._payload = status_code, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.mark.parametrize("reason", sorted(QUOTA_REASONS))
def test_client_maps_every_documented_quota_reason(monkeypatch, reason):
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        403, {"error": {"errors": [{"reason": reason}]}}))
    with pytest.raises(YouTubeQuotaExhausted):
        YouTubeClient().search_videos(q="x")


def test_a_non_quota_403_is_not_reported_as_quota(monkeypatch):
    """Control for the test above: a 403 that is NOT quota must stay generic.

    Without this, mapping every 403 to "quota exhausted" would pass the whole
    parametrised set above and tell the member to come back tomorrow for a
    permanently broken API key.
    """
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        403, {"error": {"errors": [{"reason": "accessNotConfigured"}]}}))
    with pytest.raises(YouTubeError) as ei:
        YouTubeClient().search_videos(q="x")
    assert not isinstance(ei.value, YouTubeQuotaExhausted)


def test_malformed_error_body_does_not_crash_the_error_path(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(403, ValueError("not json")))
    with pytest.raises(YouTubeError) as ei:
        YouTubeClient().search_videos(q="x")
    assert not isinstance(ei.value, YouTubeQuotaExhausted)


# --------------------------------------------------------------------------
# Client: fails closed, bounded, and never echoes the key
# --------------------------------------------------------------------------

def test_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "", raising=False)
    with pytest.raises(YouTubeNotConfigured):
        YouTubeClient().search_videos(q="x")


def test_transport_failure_does_not_leak_the_url_or_key(monkeypatch):
    """httpx puts the full URL — key and all — in its exception string."""
    import httpx
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "SUPERSECRETKEY", raising=False)

    def boom(*a, **k):
        raise httpx.ConnectTimeout("timed out for https://...&key=SUPERSECRETKEY")

    monkeypatch.setattr("httpx.get", boom)
    with pytest.raises(YouTubeError) as ei:
        YouTubeClient().search_videos(q="x")
    assert "SUPERSECRETKEY" not in str(ei.value)
    assert ei.value.__cause__ is None, "`raise ... from None` keeps the leaky cause off the traceback"


def test_videos_list_refuses_more_than_the_api_cap(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    with pytest.raises(ValueError):
        YouTubeClient().list_videos([f"v{i}" for i in range(VIDEOS_LIST_MAX_IDS + 1)])


def test_videos_list_short_circuits_on_empty_input(monkeypatch):
    called = {"n": 0}

    def counted(*a, **k):
        called["n"] += 1
        return _FakeResponse(200, {"items": []})

    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", counted)
    assert YouTubeClient().list_videos([]) == {}
    assert called["n"] == 0


def test_every_outbound_request_sets_an_explicit_timeout(monkeypatch):
    seen = {}

    def capture(url, params=None, timeout=None, **k):
        seen["timeout"] = timeout
        seen["params"] = params
        return _FakeResponse(200, {"items": []})

    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", capture)
    YouTubeClient().search_videos(q="x")
    assert seen["timeout"] is not None and seen["timeout"] > 0
    # Discovery is one page. maxResults is clamped to the API's own ceiling so a
    # caller cannot ask for a page the API will silently truncate.
    assert seen["params"]["maxResults"] <= 50


# --------------------------------------------------------------------------
# Router — status codes the UI branches on
# --------------------------------------------------------------------------

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

TRACK_ID = "11111111-2222-3333-4444-555555555555"


def _fake_track(*, title="Smells Like Teen Spirit", artist="Nirvana", duration=301):
    t = MagicMock()
    t.id = TRACK_ID
    t.title = title
    t.duration_sec = duration
    if artist is None:
        t.artists = []
        t.album = None
    else:
        a = MagicMock()
        a.name = artist
        t.artists = [a]
        t.album = None
    return t


def _client(track):
    """TestClient with the DB returning `track` and Cognito satisfied.

    Auth is overridden rather than stubbed away silently: this endpoint is
    authenticated, and a test harness that removed the dependency entirely could
    not tell an authenticated route from an open one.
    """
    from app.core.auth import require_cognito_token
    from app.core.db import get_db
    from app.main import app

    session = MagicMock()
    session.execute.return_value.scalars.return_value.first.return_value = track
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_cognito_token] = lambda: {"sub": "test-sub"}
    return TestClient(app), app


def _teardown(app):
    app.dependency_overrides.clear()


def test_endpoint_returns_ranked_candidates(monkeypatch):
    stub = StubYouTube(["far", "close"], {
        "far":   _video("far",   title="Live",     channel="Fan",   duration="PT9M00S"),
        "close": _video("close", title="Official", channel="Topic", duration="PT5M02S"),
    })
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app = _client(_fake_track())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 200, r.text
        body = r.json()
        # Content, not just the status code — an empty list would 200 too.
        assert [c["video_id"] for c in body["candidates"]] == ["close", "far"]
        assert body["query"] == "Nirvana Smells Like Teen Spirit"
        assert body["track_duration_sec"] == 301
        assert body["candidates"][0]["duration_delta_sec"] == 1
        assert body["candidates"][0]["channel_title"] == "Topic"
    finally:
        _teardown(app)


def test_unknown_track_is_404_and_spends_no_quota(monkeypatch):
    stub = StubYouTube(["x"], {})
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app = _client(None)
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 404
        assert stub.search_calls == 0, "a 404 must not cost 100 units"
    finally:
        _teardown(app)


def test_quota_exhaustion_is_429_with_a_printable_code(monkeypatch):
    monkeypatch.setattr(
        "app.services.youtube_candidate_service.youtube",
        StubYouTube([], {}, search_error=YouTubeQuotaExhausted("quotaExceeded")),
    )
    client, app = _client(_fake_track())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        # 429, NOT 503: the service is healthy and the request was valid; the
        # daily discovery budget is spent and resets on its own.
        assert r.status_code == 429
        assert "youtube_quota_exhausted" in r.json()["detail"]
    finally:
        _teardown(app)


def test_missing_credential_is_503_not_a_silent_empty_list(monkeypatch):
    monkeypatch.setattr(
        "app.services.youtube_candidate_service.youtube",
        StubYouTube([], {}, search_error=YouTubeNotConfigured("no key")),
    )
    client, app = _client(_fake_track())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 503
        assert r.json()["detail"] == "youtube_not_configured"
    finally:
        _teardown(app)


def test_upstream_failure_is_502(monkeypatch):
    monkeypatch.setattr(
        "app.services.youtube_candidate_service.youtube",
        StubYouTube([], {}, search_error=YouTubeError("HTTP 500")),
    )
    client, app = _client(_fake_track())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 502
    finally:
        _teardown(app)


def test_endpoint_declares_the_cognito_dependency():
    """Control for the auth override every router test above installs.

    It cannot be checked by calling the route: the suite runs with ENV=local,
    which disables the JWT check by design (workspace CLAUDE.md, Auth), so an
    unauthenticated request returns 200 here and would in `dev` too. Overriding
    `require_cognito_token` in the other tests would therefore "pass" just as
    well against a route that never declared it — which is exactly the hole this
    asserts against. Declared dependency, checked statically.
    """
    from app.core.auth import require_cognito_token
    from app.main import app

    route = next(
        r for r in app.routes
        if getattr(r, "path", None) == "/api/music/search/youtube-candidates"
    )
    deps = {d.call for d in route.dependant.dependencies}
    assert require_cognito_token in deps, (
        "the endpoint must be authenticated; a new protected route also needs a "
        "matching entry in infra/apigateway.tf or it 404s at the edge"
    )
