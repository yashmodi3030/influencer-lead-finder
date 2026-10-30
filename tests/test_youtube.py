from datetime import datetime, timezone

import pytest

from pipeline.metrics import video_stats
from pipeline.youtube import QuotaExceeded, YouTubeClient, YouTubeError, YouTubeFatal, channel_url

from .conftest import FakeResponse, FakeSession

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def client(*responses, budget=None):
    session = FakeSession(list(responses))
    return YouTubeClient(api_key="k", session=session, budget=budget, sleep=lambda s: None), session


def test_search_videos_params_and_parsing():
    yt, session = client(FakeResponse(200, {"items": [
        {"id": {"videoId": "v1"}, "snippet": {"channelId": "UC1", "channelTitle": "One", "title": "t"}},
        {"id": {"channelId": "UCx"}, "snippet": {}},  # not a video: ignored
    ]}))
    out = yt.search_videos("fitness tips", datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert out == [{"video_id": "v1", "channel_id": "UC1", "channel_title": "One", "title": "t"}]
    method, url, kwargs = session.calls[0]
    assert url.endswith("/youtube/v3/search")
    params = kwargs["params"]
    assert params["type"] == "video" and params["order"] == "viewCount" and params["key"] == "k"
    assert params["publishedAfter"] == "2026-08-01T00:00:00Z"
    assert yt.units_used == 100


def test_channels_batches_and_parses():
    items = [{"id": "UC1", "snippet": {"title": "One", "customUrl": "@one", "description": "d", "country": "IN"},
              "statistics": {"subscriberCount": "81000", "viewCount": "9", "videoCount": "3"},
              "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}},
             {"id": "UC2", "snippet": {"title": "Two"},
              "statistics": {"hiddenSubscriberCount": True, "subscriberCount": "0"}, "contentDetails": {}}]
    yt, session = client(FakeResponse(200, {"items": items}), FakeResponse(200, {"items": []}))
    out = yt.channels([f"UC{i}" for i in range(60)])
    assert len(session.calls) == 2 and yt.units_used == 2
    assert out[0] == {"channel_id": "UC1", "title": "One", "handle": "one", "description": "d", "country": "IN",
                      "subscribers": 81000, "total_views": 9, "video_count": 3, "uploads_playlist": "UU1"}
    assert out[1]["subscribers"] is None


def test_recent_videos_skips_live_and_keeps_hidden_likes_as_none():
    yt, _ = client(
        FakeResponse(200, {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]}),
        FakeResponse(200, {"items": [
            {"id": "a", "snippet": {"publishedAt": "2026-10-20T10:00:00Z", "description": "x"},
             "statistics": {"viewCount": "100", "commentCount": "2"}},
            {"id": "b", "snippet": {"liveBroadcastContent": "upcoming"}, "statistics": {}},
        ]}),
    )
    out = yt.recent_videos("UU1")
    assert [v["video_id"] for v in out] == ["a"]
    assert out[0]["likes"] is None and out[0]["views"] == 100
    assert yt.recent_videos("") == []


@pytest.mark.parametrize("status,reason,exc,text", [
    (403, "quotaExceeded", QuotaExceeded, "quota"),
    (403, "accessNotConfigured", YouTubeFatal, "not enabled"),
    (400, "keyInvalid", YouTubeFatal, "invalid"),
    (404, "playlistNotFound", YouTubeError, "playlistNotFound"),
])
def test_error_mapping(status, reason, exc, text):
    yt, _ = client(FakeResponse(status, {"error": {"message": "m", "errors": [{"reason": reason}]}}))
    with pytest.raises(exc, match=text) as info:
        yt.recent_videos("UU1")
    if exc is YouTubeError:
        assert not isinstance(info.value, YouTubeFatal)  # per-channel error: the run continues


def test_budget_stops_before_spending():
    yt, session = client(budget=150)
    yt.units_used = 100
    with pytest.raises(QuotaExceeded):
        yt.search_videos("q", NOW)
    assert session.calls == []


def test_requires_some_credential():
    with pytest.raises(ValueError):
        YouTubeClient()


def test_channel_url():
    assert channel_url("UC1", "one") == "https://www.youtube.com/@one"
    assert channel_url("UC1") == "https://www.youtube.com/channel/UC1"


def test_video_stats():
    videos = [
        {"views": 1000, "likes": 50, "comments": 10, "published_at": "2026-10-25T00:00:00Z"},
        {"views": 3000, "likes": 90, "comments": 0, "published_at": "2026-10-01T00:00:00Z"},
        {"views": 2000, "likes": None, "comments": 5, "published_at": "2026-09-01T00:00:00Z"},
    ]
    s = video_stats(videos, NOW)
    assert (s.median_views, s.avg_views, s.sample_size) == (2000, 2000, 3)
    assert s.engagement_rate == 3.75  # (60 + 90) / (1000 + 3000); hidden-likes video excluded
    assert (s.days_since_upload, s.last_upload) == (5, "2026-10-25")
    assert video_stats([], NOW).sample_size == 0
    assert video_stats([{"views": 10, "likes": None, "published_at": ""}], NOW).engagement_rate is None
