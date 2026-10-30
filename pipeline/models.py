"""Dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Candidate:
    """A channel found through a video search, before it is checked."""

    channel_id: str
    niche: str
    found_via: str  # URL of the video that surfaced the channel


@dataclass
class Lead:
    channel_id: str
    handle: str
    channel_url: str
    name: str = ""
    subscribers: int = 0
    total_views: int = 0
    video_count: int = 0
    median_views: int = 0
    avg_views: int = 0
    engagement_rate: float | str = ""  # "" when the channel hides likes
    last_upload: str = ""
    country: str = ""
    niche: str = ""
    description: str = ""
    website: str = ""
    email: str = ""
    email_status: str = ""
    phone: str = ""
    contact_source: str = ""
    source_url: str = ""
    date_found: str = ""
    status: str = "new"
    notes: str = ""

    def to_row(self, headers: list[str]) -> list:
        """Values ordered to match the sheet's header row (unknown columns blank)."""
        data = asdict(self)
        return [data.get(h.strip().lower(), "") for h in headers]


@dataclass
class Rejection:
    channel_id: str
    name: str
    reason: str
    date: str

    def to_row(self, headers: list[str]) -> list:
        data = asdict(self)
        return [data.get(h.strip().lower(), "") for h in headers]


@dataclass
class QueryLogEntry:
    query: str
    api: str
    date: str
    results_count: int

    def to_row(self, headers: list[str]) -> list:
        data = asdict(self)
        return [data.get(h.strip().lower(), "") for h in headers]


@dataclass
class RunStats:
    run_id: str
    started_at: str
    finished_at: str = ""
    youtube_units_used: int = 0
    found: int = 0
    checked: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def to_row(self, headers: list[str]) -> list:
        data = asdict(self)
        data["warnings"] = " | ".join(self.warnings)
        return [data.get(h.strip().lower(), "") for h in headers]
