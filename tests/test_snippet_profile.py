from pipeline.search import SerpApiClient
from pipeline.snippet_profile import (
    clean_bio,
    find_website,
    is_profile_page,
    name_from_title,
    parse_count,
    parse_profile_snippet,
)

from .conftest import FakeResponse, FakeSession


def test_parse_count():
    assert parse_count("8,348") == 8348
    assert parse_count("2.7", "K") == 2700
    assert parse_count("15.3", "k") == 15300
    assert parse_count("1.2", "M") == 1_200_000


def test_parse_profile_snippet_formats():
    # Tavily style
    p = parse_profile_snippet(
        "Kiran | Fitness Coach I Personal Trainer",
        "Kiran | Fitness Coach I Personal Trainer | Online. 8,348 followers. 118 following. "
        "Nutrition & Transformation Specialist. Mail: kiran.fit@gmail.com",
    )
    assert p["followers_count"] == 8348 and p["media_count"] is None
    assert p["name"] == "Kiran"
    assert "118" not in p["biography"] and "kiran.fit@gmail.com" in p["biography"]

    p = parse_profile_snippet(
        "Sample Gym (@sample_gym_hq)",
        '11K followers · 34 following · 571 posts · @sample_gym_hq: "Best Gym In Town',
    )
    assert (p["followers_count"], p["media_count"]) == (11000, 571)
    assert p["name"] == "Sample Gym" and p["biography"] == "Best Gym In Town"

    # Google style
    p = parse_profile_snippet(
        "Riya Fit (@riya.fit) • Instagram photos and videos",
        "2.7K+ Followers, 300 Following, 120 Posts - See Instagram photos and videos from Riya Fit (@riya.fit)",
    )
    assert (p["followers_count"], p["media_count"]) == (2700, 120)

    # A page without counts
    assert parse_profile_snippet("x", "Powered by @brand. Since 2019")["followers_count"] is None


def test_clean_bio_drops_page_chrome():
    raw = ("Log In\n\nSign Up\n\n 15.3K followers\n 1,197 following\n\nSam Coach\n\nsam\\_coach\n\n"
           "FOUNDER :- @samgym_official\n\n sam_coach's highlight story picture")
    assert clean_bio(raw) == "Sam Coach sam_coach FOUNDER :- @samgym_official"


def test_name_and_website():
    assert name_from_title("IRON HOUSE GYM (@ironhouse.gym) • Instagram photos and videos") == "IRON HOUSE GYM"
    assert name_from_title("Jane • Instagram photos and videos") == "Jane"
    assert find_website("Coach | linktr.ee/riyafit | mail riya@gmail.com") == "https://linktr.ee/riyafit"
    assert find_website("see https://riya.in/contact.") == "https://riya.in/contact"
    assert find_website("follow instagram.com/other and mail me@x.com") == ""


def test_is_profile_page():
    assert is_profile_page("https://www.instagram.com/riya.fit/", "riya.fit")
    assert is_profile_page("https://instagram.com/riya.fit", "riya.fit")
    assert not is_profile_page("https://www.instagram.com/riya.fit/reels", "riya.fit")
    assert not is_profile_page("https://www.instagram.com/p/abc/", "riya.fit")
    assert not is_profile_page("https://example.com/riya.fit", "riya.fit")


def test_serpapi_takes_handle_from_source_and_fixes_relative_links():
    session = FakeSession([FakeResponse(200, {"organic_results": [
        {"link": "/goto?url=abc", "title": "t", "snippet": "s", "source": "Instagram · gym_lover_asd"},
        {"link": "https://blog.in/x", "title": "t", "snippet": "s", "source": "Findinfluencer"},
    ]})])
    results = SerpApiClient("k", session=session, sleep=lambda s: None).search("q")
    assert results[0].handle == "gym_lover_asd" and results[0].url == "https://www.google.com/goto?url=abc"
    assert results[1].handle == ""
