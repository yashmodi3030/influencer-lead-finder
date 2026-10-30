import pytest

from pipeline.handles import (
    extract_handle,
    extract_handle_from_url,
    extract_handles_from_text,
    normalize_handle,
    profile_url,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/fit.with.riya/", "fit.with.riya"),
        ("https://instagram.com/FitWithRiya", "fitwithriya"),
        ("http://m.instagram.com/city_fitness/?hl=en", "city_fitness"),
        ("instagram.com/gym_bro_99", "gym_bro_99"),
        ("https://www.instagram.com/fitwithriya/reels/", "fitwithriya"),
        ("https://www.instagram.com/p/C1abcDEF/", None),
        ("https://www.instagram.com/reel/C1abcDEF/", None),
        ("https://www.instagram.com/reels/C1abcDEF/", None),
        ("https://www.instagram.com/explore/tags/fitness/", None),
        ("https://www.instagram.com/stories/someone/123/", None),
        ("https://www.instagram.com/tags/fitness/", None),
        ("https://www.instagram.com/accounts/login/", None),
        ("https://www.instagram.com/tv/abc/", None),
        ("https://www.instagram.com/", None),
        ("https://notinstagram.com/riya", None),
        ("https://www.facebook.com/riya", None),
        ("https://www.instagram.com/bad..handle/", None),
        ("https://www.instagram.com/" + "a" * 31 + "/", None),
    ],
)
def test_extract_handle_from_url(url, expected):
    assert extract_handle_from_url(url) == expected


def test_title_fallback_for_posts():
    assert extract_handle("https://www.instagram.com/p/XYZ/", "Riya (@Fit.Riya) • Instagram photos") == "fit.riya"
    assert extract_handle("https://www.instagram.com/reel/XYZ/", "fitriya on Instagram: \"Leg day\"") == "fitriya"
    # Title fallback is not used for non-Instagram pages.
    assert extract_handle("https://blog.example.com/top-10", "(@someone) list") is None


def test_normalize_handle():
    assert normalize_handle("@Riya.Fit ") == "riya.fit"
    assert normalize_handle("explore") is None
    assert normalize_handle(".riya") is None
    assert normalize_handle("") is None


def test_extract_handles_from_text_skips_emails():
    text = "Top creators: instagram.com/riya_fit and @daily.gains — mail riya@gmail.com"
    assert extract_handles_from_text(text) == ["riya_fit", "daily.gains"]


def test_profile_url():
    assert profile_url("riya") == "https://www.instagram.com/riya/"
