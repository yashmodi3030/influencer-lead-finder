import pytest
import requests

from pipeline.search import (
    AllSearchUnavailable,
    QuotaExceeded,
    SearchError,
    SearchManager,
    SerpApiClient,
    TavilyClient,
)

from .conftest import FakeResponse, FakeSession

TAVILY_OK = {"results": [{"url": "https://www.instagram.com/riya/", "title": "Riya (@riya)", "content": "bio"}]}
SERP_OK = {"organic_results": [{"link": "https://www.instagram.com/aman/", "title": "Aman", "snippet": "s"}]}


def test_tavily_uses_include_domains_for_site_queries(no_sleep):
    session = FakeSession([FakeResponse(200, TAVILY_OK)])
    client = TavilyClient("key", session=session, sleep=no_sleep)
    results = client.search('site:instagram.com "fitness" "collab"')
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert kwargs["json"]["include_domains"] == ["instagram.com"]
    assert "site:" not in kwargs["json"]["query"]
    assert kwargs["headers"]["Authorization"] == "Bearer key"
    assert results[0].url == "https://www.instagram.com/riya/"
    assert results[0].api == "tavily"


def test_tavily_quota_status(no_sleep):
    client = TavilyClient("key", session=FakeSession([FakeResponse(432, {"detail": "limit"})]), sleep=no_sleep)
    with pytest.raises(QuotaExceeded):
        client.search("q")


def test_tavily_retries_5xx_then_succeeds(no_sleep):
    session = FakeSession([FakeResponse(503, {}), FakeResponse(500, {}), FakeResponse(200, TAVILY_OK)])
    client = TavilyClient("key", session=session, sleep=no_sleep)
    assert len(client.search("q")) == 1
    assert len(session.calls) == 3


def test_tavily_gives_up_after_max_retries(no_sleep):
    session = FakeSession([FakeResponse(503, {})] * 4)
    client = TavilyClient("key", session=session, sleep=no_sleep)
    with pytest.raises(SearchError):
        client.search("q")
    assert len(session.calls) == 4  # first try + 3 retries


def test_serpapi_out_of_searches(no_sleep):
    resp = FakeResponse(429, {"error": "Your account has run out of searches."})
    client = SerpApiClient("key", session=FakeSession([resp] * 4), sleep=no_sleep)
    with pytest.raises(QuotaExceeded):
        client.search("q")


def test_serpapi_no_results_is_empty(no_sleep):
    resp = FakeResponse(200, {"error": "Google hasn't returned any results for this query."})
    client = SerpApiClient("key", session=FakeSession([resp]), sleep=no_sleep)
    assert client.search("q") == []


def _manager(tavily_responses, serp_responses, no_sleep):
    tavily = TavilyClient("t", session=FakeSession(tavily_responses), sleep=no_sleep)
    serp = SerpApiClient("s", session=FakeSession(serp_responses), sleep=no_sleep)
    return SearchManager([tavily, serp])


def test_fallback_to_serpapi_when_tavily_quota_exhausted(no_sleep):
    manager = _manager([FakeResponse(432, {})], [FakeResponse(200, SERP_OK), FakeResponse(200, SERP_OK)], no_sleep)
    results, api = manager.search("q1")
    assert api == "serpapi"
    assert results[0].url == "https://www.instagram.com/aman/"
    # Tavily is not called again once exhausted.
    results, api = manager.search("q2")
    assert api == "serpapi"
    assert manager.used == {"tavily": 0, "serpapi": 2}
    assert any("tavily quota" in w for w in manager.warnings)


def test_fallback_on_tavily_error_but_keep_using_tavily(no_sleep):
    manager = _manager(
        [requests.ConnectionError("boom")] * 4 + [FakeResponse(200, TAVILY_OK)],
        [FakeResponse(200, SERP_OK)],
        no_sleep,
    )
    _, api = manager.search("q1")
    assert api == "serpapi"
    _, api = manager.search("q2")
    assert api == "tavily"


def test_all_exhausted_raises(no_sleep):
    manager = _manager([FakeResponse(432, {})], [FakeResponse(429, {"error": "run out of searches"})] * 4, no_sleep)
    with pytest.raises(AllSearchUnavailable):
        manager.search("q")


def test_check_quota_warnings(no_sleep):
    tavily = TavilyClient("t", session=FakeSession([FakeResponse(200, {"key": {"usage": 990, "limit": 1000}})]),
                          sleep=no_sleep)
    serp = SerpApiClient("s", session=FakeSession([FakeResponse(200, {"total_searches_left": 0})]), sleep=no_sleep)
    manager = SearchManager([tavily, serp])
    warnings = manager.check_quota(needed=30)
    assert "low search quota: tavily has 10 searches left" in warnings
    assert "serpapi has no search credits left" in warnings
    assert any("below queries_per_run" in w for w in warnings)
    assert "serpapi" in manager.exhausted
