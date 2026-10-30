"""Find a YouTube creator's Instagram account and its public bio contacts.

No Instagram API, login or token is used, and instagram.com is never fetched:

1. The handle comes from the creator's own text first (channel/video descriptions:
   "instagram.com/<handle>" links or "IG: @handle" / "Instagram - @handle").
2. Otherwise one web search looks for the profile, and a result is accepted only if
   the handle clearly matches the channel's name or handle.
3. The profile's bio is read from the search engine's snippet of the profile page,
   and only emails/phones literally present there are kept.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import urlparse

from .contacts import extract_emails, extract_phones
from .handles import extract_handle, extract_handle_from_url, is_instagram_host, normalize_handle, profile_url
from .search import AllSearchUnavailable, SearchError
from .snippet_profile import is_profile_page, parse_profile_snippet

log = logging.getLogger(__name__)

_IG_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?instagr(?:am\.com|\.am)/[A-Za-z0-9._/%-]+", re.IGNORECASE)
_IG_LABEL_RE = re.compile(
    r"\b(?:ig|insta|instagram)\b\s*(?:[:\-–—|]|👉|➡️|►|>)?\s*@([A-Za-z0-9._]{1,30})", re.IGNORECASE)


@dataclass
class InstagramResult:
    handle: str = ""
    url: str = ""
    followers: int | None = None
    bio: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    found_via: str = ""  # "channel description", "video description" or "web search"


def handles_in_text(text: str) -> list[str]:
    """Instagram handles the creator wrote: profile links first, then 'IG: @handle' labels."""
    found: list[str] = []
    for match in _IG_LINK_RE.finditer(text or ""):
        url = match.group(0).replace("instagr.am/", "instagram.com/")
        handle = extract_handle_from_url(url)
        if handle and handle not in found:
            found.append(handle)
    for match in _IG_LABEL_RE.finditer(text or ""):
        handle = normalize_handle(match.group(1).rstrip("."))
        if handle and handle not in found:
            found.append(handle)
    return found


def _on_instagram(url: str) -> bool:
    try:
        return is_instagram_host(urlparse(url).netloc)
    except ValueError:
        return False


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def matches_channel(handle: str, names: list[str]) -> bool:
    """Is this Instagram handle plausibly the same creator (by name/handle similarity)?"""
    h = _norm(handle)
    if len(h) < 3:
        return False
    for name in names:
        n = _norm(name)
        if len(n) < 3:
            continue
        if h == n or (min(len(h), len(n)) >= 5 and (h in n or n in h)):
            return True
        if SequenceMatcher(None, h, n).ratio() >= 0.85:
            return True
    return False


class InstagramFinder:
    def __init__(self, search, max_lookups: int):
        """``search`` is a SearchManager (or None: only handles written by the creator are used)."""
        self.search = search
        self.lookups_left = max_lookups if search is not None else 0
        self.lookups_used = 0

    def _search(self, query: str):
        if self.lookups_left <= 0:
            return None
        self.lookups_left -= 1
        self.lookups_used += 1
        try:
            results, _ = self.search.search(query)
            return results
        except AllSearchUnavailable as exc:
            log.warning("Web search unavailable: %s", exc)
            self.lookups_left = 0
        except SearchError as exc:
            log.warning("Instagram lookup failed: %s", exc)
        return None

    def find(self, channel: dict, texts: list[tuple[str, str]], need_contacts: bool,
             region: str) -> InstagramResult:
        """``texts`` are (text, source) pairs written by the creator (descriptions).

        Web searches are spent only when contacts are still missing (``need_contacts``).
        """
        result = InstagramResult()
        for text, source in texts:
            handles = handles_in_text(text)
            if handles:
                result.handle, result.found_via = handles[0], source
                break

        names = [channel.get("handle", ""), channel.get("title", "")]
        profile_hit = None  # (title, snippet) of the profile page, if a search returned it
        if not result.handle and need_contacts and channel.get("title"):
            results = self._search(f'site:instagram.com "{channel["title"]}"') or []
            for r in results:
                own = r.handle or extract_handle(r.url, r.title)
                if not own or not matches_channel(own, names):
                    continue
                if not (r.handle or _on_instagram(r.url)):
                    continue
                result.handle, result.found_via = own, "web search"
                if is_profile_page(r.url, own):
                    profile_hit = (r.title, r.snippet)
                break

        if not result.handle:
            return result
        result.url = profile_url(result.handle)
        if not need_contacts:
            return result

        if profile_hit is None:
            results = self._search(f'site:instagram.com "{result.handle}"') or []
            for r in results:
                if is_profile_page(r.url, result.handle) and r.snippet:
                    profile_hit = (r.title, r.snippet)
                    break
        if profile_hit is None:
            return result

        prof = parse_profile_snippet(*profile_hit)
        result.followers = prof["followers_count"]
        result.bio = prof["biography"]
        text = f"{profile_hit[0]}\n{profile_hit[1]}"
        result.emails = extract_emails(text)
        result.phones = extract_phones(prof["biography"], region=region)
        return result

