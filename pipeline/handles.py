"""Extract and normalise Instagram handles from search-result URLs and titles.

Nothing here contacts instagram.com; we only parse strings returned by search APIs.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

# First path segments on instagram.com that are not user profiles.
RESERVED_PATHS = frozenset(
    {
        "p", "reel", "reels", "tv", "explore", "stories", "tags", "accounts",
        "direct", "about", "developer", "developers", "legal", "privacy", "terms",
        "web", "api", "static", "challenge", "login", "logout", "emails", "session",
        "oauth", "help", "invites", "ar", "lite", "nametag", "directory", "topics",
        "locations", "press", "blog", "business", "creators", "download", "music",
        "your_activity", "graphql", "qr", "s", "threads", "share", "embed", "media",
        "popular",
    }
)

HANDLE_RE = re.compile(r"^(?!.*\.\.)(?!\.)[a-z0-9._]{1,30}(?<!\.)$")

# "Jane Doe (@jane.fit) • Instagram photos and videos"
_TITLE_PAREN_RE = re.compile(r"\(@([A-Za-z0-9._]{1,30})\)")
# "jane.fit on Instagram: ..." (typical title of a post/reel result)
_TITLE_ON_IG_RE = re.compile(r"^\s*@?([A-Za-z0-9._]{1,30}) on Instagram\b")


def normalize_handle(raw: str) -> str | None:
    """Lower-case, strip '@' and whitespace; return None if not a valid username."""
    if not raw:
        return None
    handle = unquote(raw).strip().lstrip("@").strip().lower()
    if not HANDLE_RE.match(handle):
        return None
    if handle in RESERVED_PATHS:
        return None
    return handle


def is_instagram_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return host == "instagram.com" or host.endswith(".instagram.com")


def extract_handle_from_url(url: str) -> str | None:
    """Return the profile handle for an instagram.com profile URL, else None.

    Post, reel, explore, story, tag and account URLs are excluded.
    """
    if not url:
        return None
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if not is_instagram_host(parsed.netloc):
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    first = unquote(segments[0]).lower()
    if first in RESERVED_PATHS or first.startswith("_"):
        return None
    # Profile sub-pages like /handle/reels/ or /handle/tagged/ still identify the profile,
    # but /handle/p/<id>/ is a post – that's fine too, the handle is still the owner.
    return normalize_handle(first)


def extract_handle_from_title(title: str) -> str | None:
    if not title:
        return None
    match = _TITLE_PAREN_RE.search(title) or _TITLE_ON_IG_RE.search(title)
    return normalize_handle(match.group(1)) if match else None


def extract_handle(url: str, title: str = "") -> str | None:
    """Best handle for a search result: the profile URL first, else the result title.

    The title fallback is only used for instagram.com results (e.g. a post URL whose
    title is "<handle> on Instagram: ...").
    """
    handle = extract_handle_from_url(url)
    if handle:
        return handle
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc
    except ValueError:
        return None
    if is_instagram_host(host):
        return extract_handle_from_title(title)
    return None


_IG_URL_IN_TEXT_RE = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/[A-Za-z0-9._/%-]+", re.IGNORECASE)
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9._%+-])@([A-Za-z0-9._]{2,30})(?![A-Za-z0-9-]*\.[a-z]{2,}\b)")


def extract_handles_from_text(text: str, limit: int = 5) -> list[str]:
    """Handles mentioned in free text (instagram.com links first, then @mentions).

    Email addresses are not mistaken for mentions.
    """
    found: list[str] = []
    for match in _IG_URL_IN_TEXT_RE.finditer(text or ""):
        handle = extract_handle_from_url(match.group(0))
        if handle and handle not in found:
            found.append(handle)
    for match in _MENTION_RE.finditer(text or ""):
        handle = normalize_handle(match.group(1).rstrip("."))
        if handle and handle not in found:
            found.append(handle)
    return found[:limit]


def profile_url(handle: str) -> str:
    return f"https://www.instagram.com/{handle}/"
