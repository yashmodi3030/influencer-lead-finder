"""Web search clients: Tavily (primary) and SerpApi (fallback), with quota tracking."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

import requests

from . import config
from .handles import normalize_handle
from .retry import request_with_retry

log = logging.getLogger(__name__)

_SITE_IG_RE = re.compile(r"\bsite:instagram\.com\b\s*", re.IGNORECASE)
# SerpApi labels Instagram results "Instagram · <handle>" (the link may be a Google redirect).
_SERP_IG_SOURCE_RE = re.compile(r"^\s*Instagram\s*[·•-]\s*@?([A-Za-z0-9._]{1,30})\s*$", re.IGNORECASE)


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    api: str
    handle: str = ""  # owner handle when the API states it (Instagram content only)


class SearchError(Exception):
    """A single search call failed (network, bad response, ...)."""


class QuotaExceeded(SearchError):
    """The provider says the plan's credits are used up."""


class AllSearchUnavailable(SearchError):
    """No provider can serve requests any more in this run."""


class TavilyClient:
    name = "tavily"

    def __init__(self, api_key: str, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.sleep = sleep

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def search(self, query: str, max_results: int = config.WEB_SEARCH_RESULTS) -> list[SearchResult]:
        body: dict = {"query": query, "max_results": max_results, "search_depth": "basic"}
        # Tavily does not reliably honour the "site:" operator; use include_domains instead.
        if _SITE_IG_RE.search(query):
            body["query"] = _SITE_IG_RE.sub("", query).strip()
            body["include_domains"] = ["instagram.com"]
        try:
            resp = request_with_retry(self.session, "POST", config.TAVILY_SEARCH_URL,
                                      json=body, headers=self._headers(), sleep=self.sleep)
        except requests.RequestException as exc:
            raise SearchError(f"Tavily network error: {type(exc).__name__}") from exc
        # 432 = plan limit exceeded, 433 = pay-as-you-go limit exceeded.
        if resp.status_code in (432, 433):
            raise QuotaExceeded(f"Tavily quota exhausted (HTTP {resp.status_code})")
        if resp.status_code == 401:
            raise QuotaExceeded("Tavily rejected the API key (HTTP 401)")
        if resp.status_code != 200:
            raise SearchError(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchError("Tavily returned invalid JSON") from exc
        return [
            SearchResult(url=r.get("url", ""), title=r.get("title", "") or "",
                         snippet=r.get("content", "") or "", api=self.name)
            for r in data.get("results", []) or []
            if r.get("url")
        ]

    def remaining(self) -> int | None:
        """Credits left on the key/plan, or None if unknown."""
        try:
            resp = request_with_retry(self.session, "GET", config.TAVILY_USAGE_URL,
                                      headers=self._headers(), sleep=self.sleep, max_retries=1)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (requests.RequestException, ValueError):
            return None
        candidates = []
        for section, used_key, limit_key in (("key", "usage", "limit"),
                                             ("account", "plan_usage", "plan_limit")):
            block = data.get(section) or {}
            used, limit = block.get(used_key), block.get(limit_key)
            if isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
                candidates.append(int(limit - used))
        return min(candidates) if candidates else None


class SerpApiClient:
    name = "serpapi"

    def __init__(self, api_key: str, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.sleep = sleep

    def search(self, query: str, max_results: int = config.WEB_SEARCH_RESULTS) -> list[SearchResult]:
        params = {"engine": "google", "q": query, "num": max_results, "api_key": self.api_key}
        try:
            resp = request_with_retry(self.session, "GET", config.SERPAPI_SEARCH_URL,
                                      params=params, sleep=self.sleep)
        except requests.RequestException as exc:
            raise SearchError(f"SerpApi network error: {type(exc).__name__}") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        error = str(data.get("error", "") or "")
        if "run out of searches" in error.lower() or "plan limit" in error.lower():
            raise QuotaExceeded(f"SerpApi quota exhausted: {error}")
        if resp.status_code == 401 or "invalid api key" in error.lower():
            raise QuotaExceeded("SerpApi rejected the API key")
        if resp.status_code == 429:
            raise QuotaExceeded("SerpApi rate limit / quota (HTTP 429)")
        if error and "hasn't returned any results" in error.lower():
            return []
        if resp.status_code != 200 or error:
            raise SearchError(f"SerpApi HTTP {resp.status_code}: {error or resp.text[:200]}")
        results = []
        for r in data.get("organic_results", []) or []:
            link = r.get("link", "") or ""
            if not link:
                continue
            if link.startswith("/"):
                link = "https://www.google.com" + link
            match = _SERP_IG_SOURCE_RE.match(str(r.get("source", "") or ""))
            results.append(SearchResult(url=link, title=r.get("title", "") or "",
                                        snippet=r.get("snippet", "") or "", api=self.name,
                                        handle=normalize_handle(match.group(1)) or "" if match else ""))
        return results

    def remaining(self) -> int | None:
        try:
            resp = request_with_retry(self.session, "GET", config.SERPAPI_ACCOUNT_URL,
                                      params={"api_key": self.api_key}, sleep=self.sleep, max_retries=1)
            if resp.status_code != 200:
                return None
            data = resp.json()
        except (requests.RequestException, ValueError):
            return None
        for key in ("total_searches_left", "plan_searches_left"):
            if isinstance(data.get(key), int):
                return data[key]
        return None


class SearchManager:
    """Uses the primary provider until it errors or runs out, then the fallback."""

    def __init__(self, providers: list):
        self.providers = providers
        self.exhausted: set[str] = set()
        self.used: dict[str, int] = {p.name: 0 for p in providers}
        self.warnings: list[str] = []

    def search(self, query: str) -> tuple[list[SearchResult], str]:
        last_error: Exception | None = None
        for provider in self.providers:
            if provider.name in self.exhausted:
                continue
            try:
                results = provider.search(query)
            except QuotaExceeded as exc:
                log.warning("%s", exc)
                self.exhausted.add(provider.name)
                self._warn(f"{provider.name} quota exhausted or key rejected; switched to fallback")
                last_error = exc
                continue
            except SearchError as exc:
                log.warning("%s failed for query %r: %s", provider.name, query, exc)
                last_error = exc
                continue
            self.used[provider.name] += 1
            return results, provider.name
        if all(p.name in self.exhausted for p in self.providers):
            raise AllSearchUnavailable("All search providers are exhausted for this run")
        raise SearchError(f"All providers failed for query {query!r}: {last_error}")

    def check_quota(self, needed: int) -> list[str]:
        """Return warnings about low remaining credits (best effort, never raises)."""
        warnings = []
        total_known = 0
        any_known = False
        for provider in self.providers:
            remaining = provider.remaining()
            if remaining is None:
                continue
            any_known = True
            total_known += max(0, remaining)
            if remaining <= 0:
                warnings.append(f"{provider.name} has no search credits left")
                self.exhausted.add(provider.name)
            elif remaining < config.LOW_QUOTA_THRESHOLD:
                warnings.append(f"low search quota: {provider.name} has {remaining} searches left")
        if any_known and total_known < needed:
            warnings.append(f"search quota ({total_known}) is below queries_per_run ({needed})")
        for w in warnings:
            self._warn(w)
        return warnings

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
