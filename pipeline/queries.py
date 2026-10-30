"""Search query templates and fair rotation using the QueryLog tab."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping

from . import config

# YouTube video searches (ordered by views within the recent window), so channels are
# found through videos that are currently doing well.
TEMPLATES = (
    "{niche}",
    "{niche} tips",
    "{niche} for beginners",
    "{niche} tutorial",
    "{niche} vlog",
    "{niche} routine",
    "{niche} review",
    "{niche} challenge",
    "best {niche}",
    "{niche} guide",
)


@dataclass(frozen=True)
class PlannedQuery:
    query: str
    niche: str


def build_queries(niches: list[str]) -> list[PlannedQuery]:
    """All template x niche combinations, ordered for fairness.

    Order is template-major: every niche gets template 1 before any niche gets
    template 2, so a small queries_per_run still covers every niche.
    """
    planned: list[PlannedQuery] = []
    seen: set[str] = set()
    for template in TEMPLATES:
        for niche in niches:
            query = template.format(niche=niche)
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            planned.append(PlannedQuery(query=query, niche=niche))
    return planned


def select_queries(
    planned: list[PlannedQuery],
    last_used: Mapping[str, date],
    limit: int,
    today: date,
    reuse_days: int = config.QUERY_REUSE_DAYS,
) -> list[PlannedQuery]:
    """Pick up to ``limit`` queries that were not used in the last ``reuse_days`` days.

    ``last_used`` maps lower-cased query text to the most recent date it ran.
    """
    cutoff = today - timedelta(days=reuse_days)
    fresh = []
    for item in planned:
        used = last_used.get(item.query.lower())
        if used is not None and used > cutoff:
            continue
        fresh.append(item)
    # Never-used queries first, then the least recently used; stable within each group.
    fresh.sort(key=lambda q: last_used.get(q.query.lower(), date.min))
    return fresh[: max(0, limit)]
