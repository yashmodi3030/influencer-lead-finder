"""YouTube Data API v3 client (official API only; nothing is scraped from youtube.com).

Quota costs (default free quota: 10,000 units/day per Google Cloud project):
search.list = 100 units, channels.list / playlistItems.list / videos.list = 1 unit each.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import requests

from . import config
from .retry import request_with_retry

log = logging.getLogger(__name__)

COST_SEARCH = 100
COST_LIST = 1

_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
_FATAL_HINTS = {
    "accessNotConfigured": "YouTube Data API v3 is not enabled for this Google Cloud project "
                           "(SETUP.md step 4: APIs & Services > Library > YouTube Data API v3 > Enable)",
    "SERVICE_DISABLED": "YouTube Data API v3 is not enabled for this Google Cloud project "
                        "(SETUP.md step 4: APIs & Services > Library > YouTube Data API v3 > Enable)",
    "keyInvalid": "YOUTUBE_API_KEY is invalid (copy it again from Google Cloud > Credentials)",
    "API_KEY_INVALID": "YOUTUBE_API_KEY is invalid (copy it again from Google Cloud > Credentials)",
    "ipRefererBlocked": "YOUTUBE_API_KEY has an application restriction; set it to None",
    "API_KEY_SERVICE_BLOCKED": "YOUTUBE_API_KEY is restricted to other APIs; allow YouTube Data API v3",
    "forbidden": "YouTube refused the request (check the API key / service account)",
    "insufficientPermissions": "YouTube refused the service-account token; set YOUTUBE_API_KEY instead",
    "authError": "YouTube refused the credentials; set YOUTUBE_API_KEY (SETUP.md step 4)",
}


class YouTubeError(Exception):
    """One call failed; the run can continue with other channels."""


class YouTubeFatal(YouTubeError):
    """Calls cannot succeed in this run (bad credentials, API disabled)."""


class QuotaExceeded(YouTubeFatal):
    """The daily quota (or this run's budget) is used up."""


def _error_reason(resp) -> tuple[str, str]:
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        return "", resp.text[:200]
    reasons = [e.get("reason", "") for e in err.get("errors") or [] if isinstance(e, dict)]
    for detail in err.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reasons.append(detail["reason"])
    reason = next((r for r in reasons if r in _QUOTA_REASONS or r in _FATAL_HINTS), reasons[0] if reasons else "")
    return reason, str(err.get("message", ""))[:200]


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeClient:
    def __init__(self, api_key: str = "", service_account_json: str = "",
                 session: requests.Session | None = None, budget: int | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not api_key and not service_account_json:
            raise ValueError("YouTubeClient needs YOUTUBE_API_KEY or GOOGLE_SERVICE_ACCOUNT_JSON")
        self.api_key = api_key
        self.service_account_json = service_account_json
        self.session = session or requests.Session()
        self.sleep = sleep
        self.budget = (config.YOUTUBE_DAILY_UNITS - config.YOUTUBE_UNITS_RESERVE) if budget is None else budget
        self.units_used = 0
        self._credentials = None

    # -- transport -----------------------------------------------------------

    def _auth(self) -> tuple[dict, dict]:
        if self.api_key:
            return {"key": self.api_key}, {}
        if self._credentials is None:
            from google.oauth2 import service_account

            info = json.loads(self.service_account_json)
            self._credentials = service_account.Credentials.from_service_account_info(
                info, scopes=[config.YOUTUBE_SCOPE])
        if not self._credentials.valid:
            from google.auth.transport.requests import Request

            try:
                self._credentials.refresh(Request())
            except Exception as exc:
                raise YouTubeFatal(f"could not get a service-account token: {type(exc).__name__}") from exc
        return {}, {"Authorization": f"Bearer {self._credentials.token}"}

    def _get(self, endpoint: str, params: dict, cost: int) -> dict:
        if self.units_used + cost > self.budget:
            raise QuotaExceeded(f"YouTube quota budget reached ({self.units_used}/{self.budget} units used)")
        auth_params, headers = self._auth()
        try:
            resp = request_with_retry(self.session, "GET", f"{config.YOUTUBE_API_BASE}/{endpoint}",
                                      params={**params, **auth_params}, headers=headers, sleep=self.sleep)
        except requests.RequestException as exc:
            raise YouTubeError(f"YouTube network error: {type(exc).__name__}") from exc
        self.units_used += cost
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise YouTubeError("YouTube returned invalid JSON") from exc
        reason, message = _error_reason(resp)
        if reason in _QUOTA_REASONS:
            raise QuotaExceeded(f"YouTube quota exhausted ({reason}); it resets at midnight Pacific time")
        if reason in _FATAL_HINTS or resp.status_code == 401:
            raise YouTubeFatal(_FATAL_HINTS.get(reason, f"YouTube HTTP {resp.status_code}: {message}"))
        raise YouTubeError(f"YouTube HTTP {resp.status_code} {reason}: {message}".strip())

    # -- API calls -------------------------------------------------------------

    def search_videos(self, query: str, published_after: datetime,
                      max_results: int = config.SEARCH_RESULTS_PER_QUERY) -> list[dict]:
        """Most-viewed videos for the query published after the given time."""
        after = published_after.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        data = self._get("search", {
            "part": "snippet", "type": "video", "q": query, "order": "viewCount",
            "publishedAfter": after, "maxResults": max_results,
        }, COST_SEARCH)
        out = []
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            video_id = (item.get("id") or {}).get("videoId")
            if video_id and snippet.get("channelId"):
                out.append({"video_id": video_id, "channel_id": snippet["channelId"],
                            "channel_title": snippet.get("channelTitle", ""), "title": snippet.get("title", "")})
        return out

    def channels(self, channel_ids: list[str]) -> list[dict]:
        """Channel facts for up to any number of IDs (batched 50 per call)."""
        out = []
        for i in range(0, len(channel_ids), 50):
            batch = channel_ids[i:i + 50]
            data = self._get("channels", {"part": "snippet,statistics,contentDetails",
                                          "id": ",".join(batch), "maxResults": 50}, COST_LIST)
            for item in data.get("items") or []:
                snippet = item.get("snippet") or {}
                stats = item.get("statistics") or {}
                uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", "")
                out.append({
                    "channel_id": item.get("id", ""),
                    "title": snippet.get("title", ""),
                    "handle": (snippet.get("customUrl") or "").lstrip("@"),
                    "description": snippet.get("description", "") or "",
                    "country": snippet.get("country", "") or "",
                    "subscribers": None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
                    "total_views": _int(stats.get("viewCount")) or 0,
                    "video_count": _int(stats.get("videoCount")) or 0,
                    "uploads_playlist": uploads,
                })
        return out

    def recent_videos(self, uploads_playlist: str, count: int = config.RECENT_VIDEOS_PER_CHANNEL) -> list[dict]:
        """The channel's latest uploads with view/like/comment counts (live streams excluded)."""
        if not uploads_playlist:
            return []
        data = self._get("playlistItems", {"part": "contentDetails", "playlistId": uploads_playlist,
                                           "maxResults": count}, COST_LIST)
        ids = [(i.get("contentDetails") or {}).get("videoId") for i in data.get("items") or []]
        ids = [v for v in ids if v]
        if not ids:
            return []
        data = self._get("videos", {"part": "snippet,statistics", "id": ",".join(ids),
                                    "maxResults": len(ids)}, COST_LIST)
        out = []
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
                continue
            stats = item.get("statistics") or {}
            out.append({
                "video_id": item.get("id", ""),
                "title": snippet.get("title", ""),
                "description": snippet.get("description", "") or "",
                "published_at": snippet.get("publishedAt", ""),
                "views": _int(stats.get("viewCount")) or 0,
                "likes": _int(stats.get("likeCount")),  # None when the creator hides likes
                "comments": _int(stats.get("commentCount")),
            })
        return out


def channel_url(channel_id: str, handle: str = "") -> str:
    return f"https://www.youtube.com/@{handle}" if handle else f"https://www.youtube.com/channel/{channel_id}"


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
