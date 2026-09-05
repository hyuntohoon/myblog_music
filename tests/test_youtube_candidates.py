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
    DAILY_QUOTA_REASONS,
    RATE_LIMIT_REASONS,
    VIDEOS_LIST_MAX_IDS,
    YouTubeClient,
    YouTubeError,
    YouTubeNotConfigured,
    YouTubeQuotaExhausted,
    YouTubeRateLimited,
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
        self.last_max_results = None

    def search_videos(self, *, q, max_results=10):
        self.search_calls += 1
        self.last_query = q
        self.last_max_results = max_results
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
    _q, out = YouTubeCandidateService().find_candidates(
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
    _q, out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["first", "second"]


def test_unknown_duration_sorts_last_but_is_still_offered(patch_youtube):
    """"Unrankable" and "wrong" are different; only the member can tell which."""
    patch_youtube(StubYouTube(["stream", "known"], {
        "stream": _video("stream", title="24/7 radio", channel="lofi", duration="P0D"),
        "known":  _video("known",  title="Official",   channel="ch",   duration="PT5M10S"),
    }))
    _q, out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["known", "stream"]
    assert out[-1]["duration_sec"] is None and out[-1]["duration_delta_sec"] is None


def test_no_track_duration_keeps_search_order_and_drops_no_candidate(patch_youtube):
    """`tracks.duration_sec` is nullable. Absent duration must not empty the list."""
    patch_youtube(StubYouTube(["a", "b"], {
        "a": _video("a", title="A", channel="ch", duration="PT3M00S"),
        "b": _video("b", title="B", channel="ch", duration="PT4M00S"),
    }))
    _q, out = YouTubeCandidateService().find_candidates(
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
    _q, out = YouTubeCandidateService().find_candidates(
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
    _q, out = YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)
    assert [c["video_id"] for c in out] == ["real"]


def test_empty_search_result_makes_no_enrichment_call(patch_youtube):
    """A wasted videos.list is 1 unit; a wasted round trip is the member waiting."""
    stub = patch_youtube(StubYouTube([], {}))
    assert YouTubeCandidateService().find_candidates(
        title="t", artist_name="a", track_duration_sec=300, max_results=10)[1] == []
    assert stub.search_calls == 1 and stub.videos_calls == 0


def test_blank_track_makes_no_search_call_at_all(patch_youtube):
    """search.list costs 100 units. An empty query must never spend one."""
    stub = patch_youtube(StubYouTube(["x"], {"x": _video("x", title="X", channel="c", duration="PT1M")}))
    assert YouTubeCandidateService().find_candidates(
        title="", artist_name=None, track_duration_sec=300, max_results=10)[1] == []
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


@pytest.mark.parametrize("reason", sorted(DAILY_QUOTA_REASONS))
def test_client_maps_every_daily_quota_reason(monkeypatch, reason):
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        403, {"error": {"errors": [{"reason": reason}]}}))
    with pytest.raises(YouTubeQuotaExhausted):
        YouTubeClient().search_videos(q="x")


@pytest.mark.parametrize("reason", sorted(RATE_LIMIT_REASONS))
def test_short_window_rate_limits_are_not_the_daily_quota(monkeypatch, reason):
    """A rate limit clears in seconds; the daily pool does not clear until midnight.

    Folding them together tells a member to come back tomorrow for a condition
    that resolves in a moment — and it is the one case where retrying shortly is
    the correct client behaviour.
    """
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        403, {"error": {"errors": [{"reason": reason}]}}))
    with pytest.raises(YouTubeRateLimited) as ei:
        YouTubeClient().search_videos(q="x")
    assert not isinstance(ei.value, YouTubeQuotaExhausted)


def test_the_two_reason_sets_are_disjoint_and_neither_is_empty():
    """Pins the MEMBERSHIP, not just the partition.

    A mutation that folded the rate-limit reasons into the daily set survived
    the parametrised tests above, because emptying `RATE_LIMIT_REASONS` collects
    ZERO cases and a parametrisation over an empty set is silently vacuous — it
    reports as one skip, not as a failure. Disjointness alone is likewise true
    of two empty sets. Name the reasons.
    """
    assert "quotaExceeded" in DAILY_QUOTA_REASONS
    assert "dailyLimitExceeded" in DAILY_QUOTA_REASONS
    assert "rateLimitExceeded" in RATE_LIMIT_REASONS
    assert "userRateLimitExceeded" in RATE_LIMIT_REASONS
    assert not (DAILY_QUOTA_REASONS & RATE_LIMIT_REASONS)
    # And neither reason set may swallow the other's members.
    assert "rateLimitExceeded" not in DAILY_QUOTA_REASONS
    assert "quotaExceeded" not in RATE_LIMIT_REASONS


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


def test_api_key_travels_in_a_header_never_in_the_query_string(monkeypatch):
    """The bigger leak channel is httpx's own INFO log, not the exception.

    `httpx/_client.py` logs `request.url` at INFO on EVERY completed request, so
    a `?key=` credential is one `basicConfig(level=INFO)` — or one Lambda
    ApplicationLogLevel change — away from CloudWatch. The prod Lambda's root
    logger defaulting to WARNING is an unset default, not a control. An earlier
    revision of this client put the key in `params` and closed only the
    exception path, which made the suite read as "key leakage is covered" while
    the larger channel stayed open.
    """
    seen = {}

    def capture(url, params=None, headers=None, timeout=None, **k):
        seen.update(url=url, params=params or {}, headers=headers or {})
        return _FakeResponse(200, {"items": []})

    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "SUPERSECRETKEY", raising=False)
    monkeypatch.setattr("httpx.get", capture)
    YouTubeClient().search_videos(q="x")

    assert seen["headers"].get("X-goog-api-key") == "SUPERSECRETKEY"
    assert "key" not in seen["params"], "the key must not be a query parameter"
    assert "SUPERSECRETKEY" not in str(seen["params"])
    assert "SUPERSECRETKEY" not in seen["url"]


def test_videos_list_also_sends_the_key_as_a_header(monkeypatch):
    """Control for the test above: one hardened method is not a hardened client."""
    seen = {}

    def capture(url, params=None, headers=None, timeout=None, **k):
        seen.update(params=params or {}, headers=headers or {})
        return _FakeResponse(200, {"items": []})

    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "SUPERSECRETKEY", raising=False)
    monkeypatch.setattr("httpx.get", capture)
    YouTubeClient().list_videos(["abc"])
    assert seen["headers"].get("X-goog-api-key") == "SUPERSECRETKEY"
    assert "key" not in seen["params"]


def test_transport_failure_does_not_leak_the_url_or_key(monkeypatch):
    import httpx
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "SUPERSECRETKEY", raising=False)

    def boom(*a, **k):
        raise httpx.ConnectTimeout("timed out for https://...&key=SUPERSECRETKEY")

    monkeypatch.setattr("httpx.get", boom)
    with pytest.raises(YouTubeError) as ei:
        YouTubeClient().search_videos(q="x")
    assert "SUPERSECRETKEY" not in str(ei.value)
    assert ei.value.__cause__ is None, "`raise ... from None` keeps the leaky cause off the traceback"


@pytest.mark.parametrize("body,label", [
    ({"items": [None, {"id": "ok", "snippet": {}, "status": {}, "contentDetails": {}}]}, "a null inside items"),
    ({"items": None}, "items is null"),
    ({}, "items absent"),
    ([1, 2, 3], "the body is a list (an edge error page parsed as JSON)"),
    ("nope", "the body is a string"),
])
def test_malformed_success_bodies_never_escape_as_a_500(monkeypatch, body, label):
    """A 200 whose body is not the documented shape is an UPSTREAM failure.

    It must surface as YouTubeError (-> 502), never as a bare
    AttributeError/ValueError the router does not catch (-> 500). An earlier
    revision hardened `search.list` and left `videos.list` raw — asymmetric
    hardening inside one file.
    """
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(200, body))
    c = YouTubeClient()
    for call in (lambda: c.search_videos(q="x"), lambda: c.list_videos(["abc"])):
        try:
            call()
        except YouTubeError:
            pass  # acceptable: a typed upstream failure
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"{label}: escaped as {type(e).__name__}, not YouTubeError") from e


def test_non_json_success_body_is_an_upstream_error(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(200, ValueError("not json")))
    with pytest.raises(YouTubeError):
        YouTubeClient().search_videos(q="x")


def test_search_sends_no_category_filter(monkeypatch):
    """`videoCategoryId=10` is NOT sent, and that is a measured decision.

    Phase 0-A scored 20/20 on the unfiltered query, and a re-run on 5 production
    tracks (2026-09-06) returned 5 candidates each with the identical top match
    both with and without the filter. Narrowing an input that already scores
    20/20 can only lose results, so the measured input is the one that ships.
    """
    seen = {}
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda url, params=None, headers=None, timeout=None, **k: (
        seen.update(params=params or {}) or _FakeResponse(200, {"items": []})))
    YouTubeClient().search_videos(q="x")
    assert "videoCategoryId" not in seen["params"]


def test_videos_list_sends_no_max_results(monkeypatch):
    """`maxResults` is not a documented parameter alongside `id`; the API ignores it.

    A parameter that does nothing reads like a bound and is not one.
    """
    seen = {}
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", lambda url, params=None, headers=None, timeout=None, **k: (
        seen.update(params=params or {}) or _FakeResponse(200, {"items": []})))
    YouTubeClient().list_videos(["a", "b"])
    assert "maxResults" not in seen["params"]


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


@pytest.mark.parametrize("method", ["search", "videos"])
def test_every_outbound_request_sets_an_explicit_timeout(monkeypatch, method):
    """Both methods, not just one — the name used to cover only `search.list`."""
    seen = {}

    def capture(url, params=None, headers=None, timeout=None, **k):
        seen["timeout"] = timeout
        seen["params"] = params or {}
        return _FakeResponse(200, {"items": []})

    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "k", raising=False)
    monkeypatch.setattr("httpx.get", capture)
    c = YouTubeClient()
    c.search_videos(q="x") if method == "search" else c.list_videos(["abc"])
    assert seen["timeout"] is not None and seen["timeout"] > 0


def test_two_call_timeout_budget_fits_inside_the_lambda_ceiling():
    """`musicApi` has `timeout = 15` (workspace infra/lambda.tf).

    One request makes TWO sequential outbound calls, so the per-call timeout is
    a budget of 2x. If 2x exceeds the Lambda ceiling, a slow-but-not-failing
    YouTube kills the function before either timeout fires: API Gateway returns
    a bare 502, the 429/503/502 taxonomy never executes, and the member gets no
    usable message. Headroom is left for the DB read and a cold start.
    """
    from app.core.config import settings

    LAMBDA_TIMEOUT_S = 15
    assert settings.YOUTUBE_HTTP_TIMEOUT * 2 <= LAMBDA_TIMEOUT_S - 5, (
        "two sequential YouTube calls plus DB and cold-start headroom must fit "
        "inside musicApi's 15s Lambda timeout"
    )


# --------------------------------------------------------------------------
# Router — status codes the UI branches on
# --------------------------------------------------------------------------

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

TRACK_ID = "11111111-2222-3333-4444-555555555555"


def _client(row):
    """TestClient whose DB returns `row` (the materialised tuple) or None.

    Auth is overridden rather than removed: this endpoint is authenticated, and
    a harness that dropped the dependency could not tell an authenticated route
    from an open one. The static control below covers what the override hides.
    """
    from app.core.auth import require_cognito_token
    from app.core.db import get_db
    from app.main import app

    session = MagicMock()
    session.execute.return_value.first.return_value = row
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_cognito_token] = lambda: {"sub": "test-sub"}
    return TestClient(app), app, session


def _row(*, title="Smells Like Teen Spirit", track_artist="Nirvana", album_artist=None, duration=301):
    """The 5-tuple the route selects: (id, title, duration_sec, track_artist, album_artist)."""
    return (TRACK_ID, title, duration, track_artist, album_artist)


def _teardown(app):
    app.dependency_overrides.clear()


def test_endpoint_returns_ranked_candidates(monkeypatch):
    stub = StubYouTube(["far", "close"], {
        "far":   _video("far",   title="Live",     channel="Fan",   duration="PT9M00S"),
        "close": _video("close", title="Official", channel="Topic", duration="PT5M02S"),
    })
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app, _ = _client(_row())
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


def test_album_artist_is_used_when_the_track_has_none(monkeypatch):
    """The compilation path — the branch the Phase 0-A finding exists to serve.

    A compilation track can carry no track-level artist; the album's performer is
    then the only name available, and 5 of the 20 Phase 0-A matches came from
    classical/jazz compilations found by exactly that name.
    """
    stub = StubYouTube(["v"], {"v": _video("v", title="X", channel="ch", duration="PT5M00S")})
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app, _ = _client(_row(track_artist=None, album_artist="Glenn Gould"))
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 200, r.text
        assert r.json()["query"] == "Glenn Gould Smells Like Teen Spirit"
        assert stub.last_query == "Glenn Gould Smells Like Teen Spirit"
    finally:
        _teardown(app)


def test_track_artist_wins_over_album_artist(monkeypatch):
    """Control for the test above: the fallback must be a fallback, not a default."""
    stub = StubYouTube(["v"], {"v": _video("v", title="X", channel="ch", duration="PT5M00S")})
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app, _ = _client(_row(track_artist="Nirvana", album_artist="Various Artists"))
    try:
        client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert stub.last_query == "Nirvana Smells Like Teen Spirit"
    finally:
        _teardown(app)


def test_limit_is_passed_through_and_defaults_to_the_setting(monkeypatch):
    from app.core.config import settings

    stub = StubYouTube([], {})
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app, _ = _client(_row())
    try:
        client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}&limit=3")
        assert stub.last_max_results == 3
        client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert stub.last_max_results == settings.YOUTUBE_SEARCH_MAX_RESULTS
    finally:
        _teardown(app)


def test_session_is_closed_before_any_outbound_call(monkeypatch):
    """The recurring bug class: a transaction held across an external API call.

    `get_db` closes only in its `finally`, which runs AFTER the response is
    built — i.e. after the HTTP calls. The handler must therefore close it
    itself. Asserted as an ORDERING, because "close() was called" is also true
    of a handler that closes it last.
    """
    order = []
    client, app, session = _client(_row())
    session.close.side_effect = lambda: order.append("db.close")

    class Ordered(StubYouTube):
        def search_videos(self, *, q, max_results=10):
            order.append("search.list")
            return super().search_videos(q=q, max_results=max_results)

        def list_videos(self, video_ids):
            order.append("videos.list")
            return super().list_videos(video_ids)

    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", Ordered(
        ["v"], {"v": _video("v", title="X", channel="ch", duration="PT5M00S")}))
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 200, r.text
        assert order == ["db.close", "search.list", "videos.list"], order
    finally:
        _teardown(app)


def test_malformed_track_id_is_404_and_never_touches_the_driver(monkeypatch):
    """A non-UUID must not reach psycopg — it raises InvalidTextRepresentation (500).

    Same defect class as AUDIT-2026-07-26 A-3; this route is also registered in
    tests/test_malformed_ids.py next to the original four.
    """
    stub = StubYouTube(["x"], {})
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app, session = _client(_row())
    try:
        r = client.get("/api/music/search/youtube-candidates?track_id=not-a-uuid")
        assert r.status_code == 404, r.text
        session.execute.assert_not_called()
        assert stub.search_calls == 0
    finally:
        _teardown(app)


def test_unknown_track_is_404_and_spends_no_quota(monkeypatch):
    stub = StubYouTube(["x"], {})
    monkeypatch.setattr("app.services.youtube_candidate_service.youtube", stub)
    client, app, _ = _client(None)
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 404
        assert stub.search_calls == 0, "a 404 must not cost 100 units"
    finally:
        _teardown(app)


def test_daily_quota_exhaustion_is_429_with_a_long_retry_after(monkeypatch):
    monkeypatch.setattr(
        "app.services.youtube_candidate_service.youtube",
        StubYouTube([], {}, search_error=YouTubeQuotaExhausted("quotaExceeded")),
    )
    client, app, _ = _client(_row())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 429
        assert "youtube_quota_exhausted" in r.json()["detail"]
        # Long — the pool resets at midnight Pacific, not in seconds.
        assert int(r.headers["Retry-After"]) >= 60
    finally:
        _teardown(app)


def test_rate_limit_is_429_with_a_short_retry_after_and_different_advice(monkeypatch):
    """Both are 429; only one of them should say "come back tomorrow"."""
    monkeypatch.setattr(
        "app.services.youtube_candidate_service.youtube",
        StubYouTube([], {}, search_error=YouTubeRateLimited("rateLimitExceeded")),
    )
    client, app, _ = _client(_row())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 429
        detail = r.json()["detail"]
        assert "youtube_rate_limited" in detail
        assert "midnight" not in detail, "a short-window limit must not advertise a daily reset"
        assert int(r.headers["Retry-After"]) <= 60
    finally:
        _teardown(app)


def test_missing_credential_is_503_not_a_silent_empty_list(monkeypatch):
    monkeypatch.setattr(
        "app.services.youtube_candidate_service.youtube",
        StubYouTube([], {}, search_error=YouTubeNotConfigured("no key")),
    )
    client, app, _ = _client(_row())
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
    client, app, _ = _client(_row())
    try:
        r = client.get(f"/api/music/search/youtube-candidates?track_id={TRACK_ID}")
        assert r.status_code == 502
    finally:
        _teardown(app)


def _iter_endpoint_routes(container):
    """Every route carrying an `endpoint`, however deeply `include_router` nested it.

    FastAPI 0.141 (the version in requirements.lock) no longer flattens
    `include_router` into `app.routes` — it inserts `_IncludedRouter` wrappers
    that expose their children only via `original_router`, and the child's
    `.path` is the UNPREFIXED path. So neither `app.routes` nor a `.path ==
    "/api/music/search/youtube-candidates"` comparison finds anything, and a
    lookup written either way fails with a `StopIteration` that says nothing
    about the thing under test. Walk the tree and match on identity instead.
    """
    routes = getattr(container, "routes", None)
    if routes is None and hasattr(container, "original_router"):
        routes = getattr(container.original_router, "routes", None)
    for r in routes or []:
        if hasattr(r, "endpoint"):
            yield r
        if hasattr(r, "routes") or hasattr(r, "original_router"):
            yield from _iter_endpoint_routes(r)


def test_endpoint_declares_the_cognito_dependency():
    """Control for the auth override every router test above installs.

    It cannot be checked by calling the route: the suite runs with ENV=local,
    which disables the JWT check by design (workspace CLAUDE.md, Auth), so an
    unauthenticated request returns 200 here and would in `dev` too. Overriding
    `require_cognito_token` in the other tests would therefore "pass" just as
    well against a route that never declared it — which is the hole this asserts
    against.
    """
    from app.api.routers.search import search_youtube_candidates
    from app.core.auth import require_cognito_token
    from app.main import app

    routes = [
        r for r in _iter_endpoint_routes(app)
        if getattr(r, "endpoint", None) is search_youtube_candidates
    ]
    assert routes, "the endpoint is not registered on the app at all"
    deps = {d.call for d in routes[0].dependant.dependencies}
    assert require_cognito_token in deps, (
        "the endpoint must be authenticated; a new protected route also needs a "
        "matching entry in infra/apigateway.tf or it 404s at the edge"
    )
