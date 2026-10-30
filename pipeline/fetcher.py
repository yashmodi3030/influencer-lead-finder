"""Polite fetching of an influencer's own website / link-in-bio page.

Rules: honour robots.txt, 10 s timeout, identify with a UA string, HTML only, size cap,
one level deep, and only follow contact/about-style links on the same domain.
Social networks (Instagram, Facebook, ...) are never fetched.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from . import config
from .contacts import normalize_phone
from .retry import request_with_retry

log = logging.getLogger(__name__)

BLOCKED_DOMAINS = (
    "instagram.com", "facebook.com", "fb.com", "fb.me", "threads.net", "whatsapp.com",
    "wa.me", "twitter.com", "x.com", "tiktok.com", "youtube.com", "youtu.be", "google.com",
)
CONTACT_LINK_RE = re.compile(
    r"contact|about|collab|business|booking|book-now|work-with|workwithme|enquir|inquir|hire",
    re.IGNORECASE,
)
MAX_BYTES = 1_500_000
MAX_SUBPAGES = 3


@dataclass
class PageContent:
    url: str
    text: str
    mailto_emails: list[str] = field(default_factory=list)
    link_phones: list[str] = field(default_factory=list)  # tel: and WhatsApp click-to-chat links


def _bare_host(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str) -> bool:
    host = _bare_host(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def decode_cfemail(encoded: str) -> str | None:
    """Decode Cloudflare's email-protection hex encoding (the address is public on the page)."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except (ValueError, IndexError):
        return None


def parse_html(url: str, html: str) -> tuple[PageContent, list[str]]:
    """Return page content plus same-domain contact/about links (absolute URLs)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    mailto: list[str] = []
    phones: list[str] = []
    links: list[str] = []
    base_host = _bare_host(url)

    for el in soup.select("[data-cfemail]"):
        decoded = decode_cfemail(el.get("data-cfemail", ""))
        if decoded:
            el.replace_with(f" {decoded} ")

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower = href.lower()
        if lower.startswith("mailto:"):
            address = href[7:].split("?")[0].strip()
            if address and address.lower() not in mailto:
                mailto.append(address.lower())
        elif lower.startswith("tel:"):
            number = normalize_phone(href[4:].strip())
            if number and number not in phones:
                phones.append(number)
        elif "wa.me/" in lower or "api.whatsapp.com/send" in lower:
            match = re.search(r"wa\.me/(\d{8,15})|phone=(\d{8,15})", lower)
            if match:
                number = normalize_phone("+" + (match.group(1) or match.group(2)))
                if number and number not in phones:
                    phones.append(number)
        else:
            absolute = urljoin(url, href).split("#")[0]
            if not absolute.startswith(("http://", "https://")):
                continue
            if _bare_host(absolute) != base_host or absolute.rstrip("/") == url.rstrip("/"):
                continue
            label = f"{href} {a.get_text(' ', strip=True)}"
            if CONTACT_LINK_RE.search(label) and absolute not in links:
                links.append(absolute)

    text = soup.get_text(" ", strip=True)
    return PageContent(url=url, text=text, mailto_emails=mailto, link_phones=phones), links


class PoliteFetcher:
    def __init__(self, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, user_agent: str = config.USER_AGENT):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
        self.user_agent = user_agent
        self.sleep = sleep
        self._robots: dict[str, RobotFileParser | None] = {}

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        parser = RobotFileParser()
        try:
            resp = self.session.get(origin + "/robots.txt", timeout=config.HTTP_TIMEOUT)
            if resp.status_code in (401, 403):
                parser.parse(["User-agent: *", "Disallow: /"])
            elif resp.status_code >= 400:
                parser.parse([])  # no robots.txt -> everything allowed
            else:
                parser.parse(resp.text.splitlines())
        except requests.RequestException:
            # Can't read robots.txt -> be conservative and skip the site.
            parser.parse(["User-agent: *", "Disallow: /"])
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if is_blocked(url):
            return False
        parser = self._robots_for(url)
        return parser.can_fetch(self.user_agent, url) if parser else True

    def fetch_page(self, url: str) -> tuple[PageContent, list[str]] | None:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not self.allowed(url):
            log.debug("Skipping %s (robots.txt or blocked domain)", url)
            return None
        try:
            resp = request_with_retry(self.session, "GET", url, sleep=self.sleep,
                                      timeout=config.HTTP_TIMEOUT, stream=True, allow_redirects=True)
        except requests.RequestException as exc:
            log.debug("Fetch failed for %s: %s", url, type(exc).__name__)
            return None
        try:
            if resp.status_code != 200:
                return None
            final_url = resp.url or url
            if is_blocked(final_url):
                return None
            ctype = resp.headers.get("Content-Type", "")
            if ctype and "html" not in ctype.lower():
                return None
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
            raw = b"".join(chunks)
            html = raw.decode(resp.encoding or "utf-8", errors="replace")
        finally:
            resp.close()
        return parse_html(final_url, html)

    def fetch_site(self, url: str) -> list[PageContent]:
        """The page itself plus up to MAX_SUBPAGES same-domain contact/about pages."""
        first = self.fetch_page(url)
        if not first:
            return []
        page, links = first
        pages = [page]
        for link in links[:MAX_SUBPAGES]:
            result = self.fetch_page(link)
            if result:
                pages.append(result[0])  # one level deep: links of sub-pages are ignored
        return pages
