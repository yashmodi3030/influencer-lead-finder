"""Logging configuration that never prints secrets."""

from __future__ import annotations

import logging
import os
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"(access_token=)[^&\s]+"),
    re.compile(r"(api_key=)[^&\s]+"),
    re.compile(r"([?&]key=)[^&\s]+"),
    re.compile(r"(input_token=)[^&\s]+"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"),
]


class RedactingFilter(logging.Filter):
    """Masks tokens/keys that might appear in URLs or exception messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def setup_logging(level: str | None = None) -> logging.Logger:
    level_name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level_name, logging.INFO))
    # Third-party libraries are noisy at INFO.
    for noisy in ("urllib3", "google", "gspread"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("pipeline")
