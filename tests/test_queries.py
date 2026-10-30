from datetime import date, timedelta

from pipeline.queries import TEMPLATES, build_queries, select_queries

TODAY = date(2026, 10, 30)


def test_build_queries_is_template_major_and_fair():
    planned = build_queries(["fitness", "yoga", "fitness"])
    assert len(planned) == len(TEMPLATES) * 2  # duplicate niche ignored
    # Every niche gets template 1 before any template 2.
    assert [q.niche for q in planned[:2]] == ["fitness", "yoga"]
    assert planned[0].query == TEMPLATES[0].format(niche="fitness")
    assert planned[2].query == TEMPLATES[1].format(niche="fitness")
    assert planned[0].query == "fitness"


def test_select_skips_recent_queries_and_respects_limit():
    planned = build_queries(["fitness"])
    recent = {planned[0].query.lower(): TODAY - timedelta(days=3)}
    chosen = select_queries(planned, recent, limit=2, today=TODAY)
    assert [q.query for q in chosen] == [planned[1].query, planned[2].query]


def test_select_reuses_after_7_days_least_recent_first():
    planned = build_queries(["fitness"])[:5]
    log = {
        planned[0].query.lower(): TODAY - timedelta(days=20),
        planned[1].query.lower(): TODAY - timedelta(days=30),
        planned[2].query.lower(): TODAY - timedelta(days=1),
        planned[3].query.lower(): TODAY - timedelta(days=14),
        planned[4].query.lower(): TODAY - timedelta(days=2),
    }
    chosen = select_queries(planned, log, limit=10, today=TODAY)
    assert [q.query for q in chosen] == [planned[1].query, planned[0].query, planned[3].query]


def test_rotation_over_consecutive_runs_covers_everything():
    planned = build_queries(["fitness", "yoga"])
    log = {}
    seen = []
    for day in range(len(TEMPLATES)):
        today = TODAY + timedelta(days=day)
        batch = select_queries(planned, log, limit=2, today=today)
        for q in batch:
            log[q.query.lower()] = today
        seen.extend(q.query for q in batch)
    assert sorted(seen) == sorted(q.query for q in planned)


def test_zero_limit():
    assert select_queries(build_queries(["a"]), {}, limit=0, today=TODAY) == []
