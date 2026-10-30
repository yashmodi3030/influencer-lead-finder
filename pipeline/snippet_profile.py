"""Profile facts from search-engine snippets of an account's own instagram.com page.

Used when no Instagram Graph API credentials are configured ("search-only mode").
Search engines show profile pages as e.g. "Name (@handle) • Instagram photos and videos"
with text like "8,348 followers. 118 following. 571 posts. <bio>". We only parse the
strings the search API returned; nothing here contacts instagram.com.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .handles import extract_handle_from_url, is_instagram_host

_COUNT = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([KkMm]\b)?\+?"
_FOLLOWERS_RE = re.compile(_COUNT + r"\s*followers\b", re.IGNORECASE)
_POSTS_RE = re.compile(_COUNT + r"\s*posts\b", re.IGNORECASE)
_STATS_RE = re.compile(_COUNT + r"\s*(?:followers|following|posts)\b[\s.,·•-]*", re.IGNORECASE)
_SEE_PHOTOS_RE = re.compile(r"-?\s*See Instagram photos and videos from[^\n]*", re.IGNORECASE)
_OWNER_PREFIX_RE = re.compile(r"^@?[A-Za-z0-9._]{1,30}\s*:\s*[\"“]")
_NOISE_LINE_RE = re.compile(
    r"highlight story picture|^log in$|^sign up$|^video by |^photo by |may be an image|"
    r"^\s*\S+'s profile picture",
    re.IGNORECASE,
)
_TITLE_SUFFIX_RE = re.compile(r"\s*[•·|-]\s*Instagram( photos and videos)?\s*$", re.IGNORECASE)
_WEBSITE_RE = re.compile(
    r"(?:https?://|www\.)[^\s\"'<>“”]+|"
    r"\b(?:[a-z0-9-]+\.)+(?:ee|com|in|me|io|co|bio|link|page|site|app|xyz|net|org)/[^\s\"'<>“”]*",
    re.IGNORECASE,
)
MAX_BIO_CHARS = 500


def parse_count(number: str, suffix: str | None = None) -> int:
    """'8,348' -> 8348, ('2.7', 'K') -> 2700, ('1.2', 'M') -> 1200000."""
    value = float(number.replace(",", ""))
    multiplier = {"k": 1_000, "m": 1_000_000}.get((suffix or "").lower(), 1)
    return int(round(value * multiplier))


def _first_count(pattern: re.Pattern, text: str) -> int | None:
    match = pattern.search(text or "")
    return parse_count(match.group(1), match.group(2)) if match else None


def is_profile_page(url: str, handle: str) -> bool:
    """True for instagram.com/<handle>/ itself (not a post, reel list or tag page)."""
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return False
    if not is_instagram_host(parsed.netloc):
        return False
    segments = [s for s in parsed.path.split("/") if s]
    return len(segments) == 1 and extract_handle_from_url(url) == handle


def clean_bio(snippet: str) -> str:
    text = (snippet or "").replace("\\_", "_")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or _NOISE_LINE_RE.search(line):
            continue
        lines.append(line)
    text = " ".join(lines)
    text = _SEE_PHOTOS_RE.sub("", text)
    text = _STATS_RE.sub("", text)
    text = _OWNER_PREFIX_RE.sub("", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" \"”·•-")
    return text[:MAX_BIO_CHARS]


def name_from_title(title: str) -> str:
    title = (title or "").strip()
    if "(@" in title:
        return title.split("(@", 1)[0].strip()
    title = _TITLE_SUFFIX_RE.sub("", title)
    return title.split("|", 1)[0].strip()


def find_website(text: str) -> str:
    """First link in the bio that is not Instagram itself (e.g. linktr.ee/x, mysite.in)."""
    for match in _WEBSITE_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)…")
        if "@" in url:
            continue
        full = url if "://" in url else "https://" + url
        try:
            host = urlparse(full).netloc
        except ValueError:
            continue
        if host and not is_instagram_host(host):
            return full
    return ""


def parse_profile_snippet(title: str, snippet: str) -> dict:
    """Profile dict shaped like the Graph API response (missing values are None/'')."""
    text = f"{title}\n{snippet}"
    bio = clean_bio(snippet)
    return {
        "name": name_from_title(title),
        "followers_count": _first_count(_FOLLOWERS_RE, text),
        "media_count": _first_count(_POSTS_RE, text),
        "biography": bio,
        "website": find_website(bio),
    }
