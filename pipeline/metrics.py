"""How well a channel's recent videos perform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median


@dataclass
class VideoStats:
    median_views: int = 0
    avg_views: int = 0
    engagement_rate: float | None = None  # (likes + comments) / views x 100; None if hidden
    days_since_upload: int | None = None
    last_upload: str = ""  # YYYY-MM-DD
    sample_size: int = 0


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def video_stats(videos: list[dict], now: datetime) -> VideoStats:
    """Median/average views, engagement and recency over the given recent uploads."""
    if not videos:
        return VideoStats()
    views = [int(v.get("views") or 0) for v in videos]
    engaged_views = engaged = 0
    for v in videos:
        if v.get("likes") is None:
            continue  # likes hidden: don't count this video either way
        engaged += int(v["likes"]) + int(v.get("comments") or 0)
        engaged_views += int(v.get("views") or 0)
    rate = round(engaged / engaged_views * 100, 2) if engaged_views > 0 else None

    times = [t for t in (_parse_time(v.get("published_at", "")) for v in videos) if t]
    latest = max(times) if times else None
    return VideoStats(
        median_views=int(median(views)),
        avg_views=int(sum(views) / len(views)),
        engagement_rate=rate,
        days_since_upload=max(0, (now - latest).days) if latest else None,
        last_upload=latest.date().isoformat() if latest else "",
        sample_size=len(videos),
    )
