"""Email validation: syntax plus a DNS MX lookup (no SMTP probing)."""

from __future__ import annotations

import logging
import re
from typing import Callable

import dns.exception
import dns.resolver

log = logging.getLogger(__name__)

VALID = "valid"
NO_MX = "no_mx"
INVALID = "invalid"
UNCHECKED = "unchecked"  # DNS timed out; neither confirmed nor refuted

_SYNTAX_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}$"
)


def is_valid_syntax(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_SYNTAX_RE.match(email))


def _default_resolver() -> Callable[[str], bool]:
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0

    def has_mx(domain: str) -> bool:
        try:
            answers = resolver.resolve(domain, "MX")
            return any(str(r.exchange) not in (".", "") for r in answers)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return False

    return has_mx


class EmailValidator:
    def __init__(self, has_mx: Callable[[str], bool] | None = None):
        self._has_mx = has_mx or _default_resolver()
        self._cache: dict[str, str] = {}

    def status(self, email: str) -> str:
        if not is_valid_syntax(email):
            return INVALID
        domain = email.rsplit("@", 1)[1].lower()
        if domain not in self._cache:
            try:
                self._cache[domain] = VALID if self._has_mx(domain) else NO_MX
            except dns.exception.Timeout:
                log.debug("MX lookup timed out for %s", domain)
                return UNCHECKED
            except dns.exception.DNSException as exc:
                log.debug("MX lookup failed for %s: %s", domain, exc)
                return UNCHECKED
        return self._cache[domain]
