"""Configuration: environment variables, constants and Settings-tab defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_DAILY_UNITS = 10_000  # default free quota per Google Cloud project
YOUTUBE_UNITS_RESERVE = 300  # stop spending before the quota is fully used

# Web search (optional): used only to find a creator's Instagram profile and read its
# public bio from the search snippet. instagram.com itself is never fetched.
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_USAGE_URL = "https://api.tavily.com/usage"
SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"
SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"
WEB_SEARCH_RESULTS = 10
LOW_QUOTA_THRESHOLD = 50  # warn when fewer web searches than this remain

USER_AGENT = (
    "InfluencerLeadFinder/1.0 (polite bot; reads only publicly published "
    "business contact pages; respects robots.txt)"
)

HTTP_TIMEOUT = 10  # seconds, for every outbound request
MAX_RETRIES = 3  # retries for 429 / 5xx with exponential backoff
BACKOFF_BASE = 1.5  # seconds; delay = BACKOFF_BASE * 2**attempt

QUERY_REUSE_DAYS = 7  # skip queries used within this many days
SEARCH_RESULTS_PER_QUERY = 50  # YouTube search.list maximum
RECENT_VIDEOS_PER_CHANNEL = 10  # videos used to judge "good videos"

DEFAULT_PHONE_REGION = "IN"

LEAD_STATUSES = ("new", "contacted", "replied", "not_interested", "do_not_contact")

REQUIRED_ENV = ("GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID")

# Settings tab seed values (key -> value, as strings, written on first run).
DEFAULT_SETTINGS: dict[str, str] = {
    "niches": "fitness",
    "min_subscribers": "50000",
    "max_subscribers": "5000000",
    "min_median_views": "10000",
    "min_engagement_rate": "1.0",
    "max_days_since_upload": "60",
    "search_window_days": "90",
    "queries_per_run": "10",
    "max_new_leads_per_run": "50",
    "instagram_lookups_per_run": "15",
    "active": "TRUE",
}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


@dataclass
class Config:
    google_service_account_json: str = ""
    sheet_id: str = ""
    # Optional: a YouTube Data API key. Without it the service account is used.
    youtube_api_key: str = ""
    # Optional: web search, used to find Instagram profiles of creators.
    tavily_api_key: str = ""
    serpapi_key: str = ""

    def missing(self) -> list[str]:
        values = {
            "GOOGLE_SERVICE_ACCOUNT_JSON": self.google_service_account_json,
            "SHEET_ID": self.sheet_id,
        }
        return [name for name, value in values.items() if not value]

    @property
    def has_sheets(self) -> bool:
        return bool(self.google_service_account_json and self.sheet_id)


def load_config(env: Mapping[str, str] | None = None, use_dotenv: bool = True) -> Config:
    """Read secrets from the environment (and a local .env file in development)."""
    if env is None:
        if use_dotenv:
            try:
                from dotenv import load_dotenv

                load_dotenv(override=False)
            except ImportError:  # python-dotenv is optional at runtime
                pass
        env = os.environ
    get = lambda name: (env.get(name) or "").strip()  # noqa: E731
    return Config(
        google_service_account_json=get("GOOGLE_SERVICE_ACCOUNT_JSON"),
        sheet_id=get("SHEET_ID"),
        youtube_api_key=get("YOUTUBE_API_KEY"),
        tavily_api_key=get("TAVILY_API_KEY"),
        serpapi_key=get("SERPAPI_KEY"),
    )


# ---------------------------------------------------------------------------
# Settings tab parsing
# ---------------------------------------------------------------------------


@dataclass
class RunSettings:
    niches: list[str]
    min_subscribers: int = 50000
    max_subscribers: int = 5000000
    min_median_views: int = 10000
    min_engagement_rate: float = 1.0
    max_days_since_upload: int = 60
    search_window_days: int = 90
    queries_per_run: int = 10
    max_new_leads_per_run: int = 50
    instagram_lookups_per_run: int = 15
    active: bool = True


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def parse_settings(raw: Mapping[str, str]) -> tuple[RunSettings, list[str]]:
    """Turn Settings-tab key/value strings into typed settings.

    Invalid values fall back to defaults and produce a warning instead of failing.
    """
    warnings: list[str] = []
    merged = {**DEFAULT_SETTINGS, **{k.strip().lower(): str(v).strip() for k, v in raw.items() if k}}

    def as_int(key: str) -> int:
        try:
            return max(0, int(float(merged[key].replace(",", ""))))
        except ValueError:
            warnings.append(f"Settings: {key}={merged[key]!r} is not a number; using {DEFAULT_SETTINGS[key]}")
            return int(DEFAULT_SETTINGS[key])

    def as_float(key: str) -> float:
        try:
            return max(0.0, float(merged[key].replace("%", "")))
        except ValueError:
            warnings.append(f"Settings: {key}={merged[key]!r} is not a number; using {DEFAULT_SETTINGS[key]}")
            return float(DEFAULT_SETTINGS[key])

    settings = RunSettings(
        niches=_split_list(merged["niches"]) or _split_list(DEFAULT_SETTINGS["niches"]),
        min_subscribers=as_int("min_subscribers"),
        max_subscribers=as_int("max_subscribers"),
        min_median_views=as_int("min_median_views"),
        min_engagement_rate=as_float("min_engagement_rate"),
        max_days_since_upload=as_int("max_days_since_upload"),
        search_window_days=max(1, as_int("search_window_days")),
        queries_per_run=as_int("queries_per_run"),
        max_new_leads_per_run=as_int("max_new_leads_per_run"),
        instagram_lookups_per_run=as_int("instagram_lookups_per_run"),
        active=merged["active"].strip().upper() == "TRUE",
    )
    if settings.min_subscribers > settings.max_subscribers:
        warnings.append("Settings: min_subscribers > max_subscribers; no channel can pass the filter")
    return settings, warnings
