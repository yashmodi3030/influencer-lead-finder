"""Extract publicly published emails and phone numbers from text.

Emails are never guessed: only addresses literally present (possibly written in an
obfuscated "name [at] gmail [dot] com" style) are returned.
"""

from __future__ import annotations

import re

import phonenumbers

from . import config

_OPEN = r"[\[\(\{<]"
_CLOSE = r"[\]\)\}>]"

# "[at]", "(at)", "{at}", "<at>", "[ at ]", and the symbol variants "(@)", "[@]".
_BRACKET_AT_RE = re.compile(rf"\s*{_OPEN}\s*(?:at|@)\s*{_CLOSE}\s*", re.IGNORECASE)
_BRACKET_DOT_RE = re.compile(rf"\s*{_OPEN}\s*(?:dot|\.)\s*{_CLOSE}\s*", re.IGNORECASE)
# Plain words are only trusted next to well-known mail providers, to avoid
# turning "I work at gym dot com" into an address.
_MAIL_PROVIDERS = (
    "gmail|googlemail|yahoo|ymail|outlook|hotmail|live|icloud|me|rediffmail|rediff|"
    "protonmail|proton|zoho|aol|gmx|yandex"
)
_PLAIN_AT_RE = re.compile(
    rf"([A-Za-z0-9._%+-]+)\s+at\s+({_MAIL_PROVIDERS})\s*(?:\.|\s+dot\s+)\s*(com|in|co\.in|co\.uk|me|net)\b",
    re.IGNORECASE,
)
_PLAIN_DOT_RE = re.compile(r"(@[A-Za-z0-9-]+)\s+dot\s+([A-Za-z]{2,10})\b", re.IGNORECASE)
_SPACED_AT_RE = re.compile(r"([A-Za-z0-9._%+-])\s+@\s+([A-Za-z0-9-])")

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.[A-Za-z]{2,24})(?![A-Za-z0-9-])"
)

JUNK_DOMAINS = frozenset(
    {
        "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
        "email.com", "test.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
        "sentry.wixpress.com", "godaddy.com", "squarespace.com", "mysite.com",
        "yoursite.com", "website.com", "company.com", "address.com", "abc.com", "xyz.com",
    }
)
JUNK_LOCAL_PARTS = frozenset(
    {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster",
     "example", "test", "user", "username", "name", "email", "youremail", "your.email",
     "yourname", "abuse", "privacy", "webmaster"}
)
FILE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
                   ".ico", ".bmp", ".tiff", ".mp4", ".pdf", ".woff", ".woff2")

PHONE_CONTEXT_KEYWORDS = ("contact", "business", "collab", "booking", "whatsapp",
                          "call", "phone", "mobile", "enquir", "inquir", "reach")


def deobfuscate(text: str) -> str:
    """Rewrite common email obfuscations into plain addresses (for email search only)."""
    if not text:
        return ""
    out = text.replace("＠", "@").replace("․", ".")  # fullwidth @, one-dot leader
    out = _BRACKET_AT_RE.sub("@", out)
    out = _BRACKET_DOT_RE.sub(".", out)
    out = _PLAIN_AT_RE.sub(lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}", out)
    out = _PLAIN_DOT_RE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", out)
    out = _SPACED_AT_RE.sub(r"\1@\2", out)
    return out


def is_junk_email(email: str) -> bool:
    email = email.lower()
    if "@" not in email:
        return True
    local, _, domain = email.rpartition("@")
    if domain in JUNK_DOMAINS or any(domain.endswith("." + d) for d in JUNK_DOMAINS):
        return True
    if local in JUNK_LOCAL_PARTS:
        return True
    if email.endswith(FILE_EXTENSIONS):
        return True
    if re.search(r"@\d+x\.", email):  # image@2x.png style asset names
        return True
    if "u003e" in email or "%20" in email:
        return True
    if re.fullmatch(r"[0-9a-f]{24,}", local):  # hashed tracking ids
        return True
    return False


def extract_emails(text: str) -> list[str]:
    """Unique, lower-cased, non-junk emails in order of appearance."""
    found: list[str] = []
    for match in EMAIL_RE.finditer(deobfuscate(text or "")):
        email = match.group(1).strip(".").lower()
        if ".." in email or is_junk_email(email):
            continue
        if email not in found:
            found.append(email)
    return found


def _iter_phone_matches(text: str, region: str):
    try:
        yield from phonenumbers.PhoneNumberMatcher(text, region, leniency=phonenumbers.Leniency.VALID)
    except Exception:  # pragma: no cover - library edge cases on odd unicode
        return


def normalize_phone(raw: str, region: str = config.DEFAULT_PHONE_REGION) -> str | None:
    """E.164 form of a single phone number string, or None if invalid."""
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def extract_phones(text: str, region: str = config.DEFAULT_PHONE_REGION) -> list[str]:
    phones: list[str] = []
    for match in _iter_phone_matches(text or "", region):
        e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        if e164 not in phones:
            phones.append(e164)
    return phones


def extract_phones_near_keywords(
    text: str,
    keywords: tuple[str, ...] = PHONE_CONTEXT_KEYWORDS,
    window: int = 80,
    region: str = config.DEFAULT_PHONE_REGION,
) -> list[str]:
    """Phones that appear within ``window`` characters of a contact-intent keyword."""
    text = text or ""
    lowered = text.lower()
    phones: list[str] = []
    for match in _iter_phone_matches(text, region):
        context = lowered[max(0, match.start - window): match.end + window]
        if not any(k in context for k in keywords):
            continue
        e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        if e164 not in phones:
            phones.append(e164)
    return phones


_LINK_RE = re.compile(r"https?://[^\s\"'<>()\[\]]+", re.IGNORECASE)
# Affiliate / shortener / store links are never the creator's own contact page.
NON_CONTACT_LINK_DOMAINS = (
    "bit.ly", "amzn.to", "amazon.com", "amazon.in", "geni.us", "tinyurl.com", "t.co", "goo.gl",
    "rebrand.ly", "shorturl.at", "cutt.ly", "spotify.com", "apple.com", "play.google.com",
    "discord.gg", "discord.com", "patreon.com", "paypal.me", "t.me", "telegram.me", "snapchat.com",
    "linkedin.com", "pinterest.com", "reddit.com", "twitch.tv",
)


def find_links(text: str) -> list[str]:
    """http(s) links in text, in order, trailing punctuation removed."""
    links: list[str] = []
    for match in _LINK_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?…")
        if url not in links:
            links.append(url)
    return links
