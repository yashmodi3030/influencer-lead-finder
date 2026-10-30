"""HTTP helper: retry 429 / 5xx responses with exponential backoff."""

from __future__ import annotations

import logging
import time
from typing import Callable

import requests

from . import config

log = logging.getLogger(__name__)


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int = config.MAX_RETRIES,
    backoff: float = config.BACKOFF_BASE,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs,
) -> requests.Response:
    """Send a request, retrying up to ``max_retries`` times on 429/5xx or network errors.

    The last response is returned even if it is still a 429/5xx so callers can map the error.
    """
    kwargs.setdefault("timeout", config.HTTP_TIMEOUT)
    for attempt in range(max_retries + 1):
        try:
            response = session.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_retries:
                raise
            delay = backoff * (2**attempt)
            log.debug("Network error (%s); retrying in %.1fs", type(exc).__name__, delay)
            sleep(delay)
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt >= max_retries:
                return response
            delay = backoff * (2**attempt)
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, min(float(retry_after), 60.0))
            log.debug("HTTP %s; retrying in %.1fs", response.status_code, delay)
            sleep(delay)
            continue
        return response
    raise RuntimeError("unreachable")  # pragma: no cover
