from datetime import datetime, timedelta, timezone

from pipeline import config
from pipeline.config import RunSettings, parse_settings
from pipeline.main import (
    Pipeline,
    collect_candidates,
    find_contacts,
    main,
    mask_email,
    mask_phone,
    own_links,
    skip_reason,
    subscriber_reason,
    video_reason,
)
from pipeline.metrics import VideoStats
from pipeline.queries import PlannedQuery
from pipeline.sheets import TAB_HEADERS, SheetStore
from pipeline.youtube import QuotaExceeded

from .conftest import FakeSpreadsheet, FakeWorksheet

NOW = datetime(2026, 10, 30, 4, 0, tzinfo=timezone.utc)
SETTINGS = RunSettings(niches=["fitness"])


# -- decisions ---------------------------------------------------------------

def test_skip_reason():
    statuses = {"UCa": "contacted", "UCdnc": "do_not_contact"}
    rejected = {"UCjoe"}
    assert skip_reason("UCa", statuses, rejected) == "already a lead"
    assert skip_reason("UCdnc", statuses, rejected) == "do_not_contact"
    assert skip_reason("UCjoe", statuses, rejected) == "previously rejected"
    assert skip_reason("UCnew", statuses, rejected) is None


def test_subscriber_reason():
    assert subscriber_reason(None, SETTINGS) == "subscriber count hidden"
    assert subscriber_reason(4200, SETTINGS) == "subscribers 4200 < 50000"
    assert subscriber_reason(9_000_000, SETTINGS) == "subscribers 9000000 > 5000000"
    assert subscriber_reason(80_000, SETTINGS) is None


def test_video_reason():
    good = VideoStats(median_views=20000, engagement_rate=3.0, days_since_upload=5, sample_size=10)
    assert video_reason(good, SETTINGS) is None
    assert video_reason(VideoStats(), SETTINGS) == "no public videos"
    stale = VideoStats(median_views=20000, days_since_upload=200, last_upload="2026-04-13", sample_size=3)
    assert video_reason(stale, SETTINGS) == "inactive: last upload 2026-04-13"
    low = VideoStats(median_views=900, days_since_upload=1, sample_size=10)
    assert video_reason(low, SETTINGS) == "median views 900 < 10000"
    dull = VideoStats(median_views=20000, engagement_rate=0.4, days_since_upload=1, sample_size=10)
    assert video_reason(dull, SETTINGS) == "engagement_rate 0.40 < 1.0"
    hidden_likes = VideoStats(median_views=20000, engagement_rate=None, days_since_upload=1, sample_size=10)
    assert video_reason(hidden_likes, SETTINGS) is None


def test_parse_settings_bad_values_warn():
    settings, warnings = parse_settings({"min_subscribers": "lots", "niches": "fitness, yoga", "active": "true"})
    assert settings.min_subscribers == 50000
    assert settings.niches == ["fitness", "yoga"]
    assert settings.active is True
    assert warnings and "min_subscribers" in warnings[0]
    assert "locations" not in config.DEFAULT_SETTINGS


def test_collect_candidates_dedupes_first_niche_wins():
    a = PlannedQuery("fitness tips", "fitness")
    b = PlannedQuery("yoga tips", "yoga")
    results = [(a, [{"video_id": "v1", "channel_id": "UC1"}, {"video_id": "v2", "channel_id": "UC2"}]),
               (b, [{"video_id": "v3", "channel_id": "UC1"}])]
    cands = collect_candidates(results)
    assert set(cands) == {"UC1", "UC2"}
    assert cands["UC1"].niche == "fitness"
    assert cands["UC1"].found_via == "https://www.youtube.com/watch?v=v1"


# -- contacts ----------------------------------------------------------------

class StubValidator:
    def status(self, email):
        return "valid" if email.endswith("gmail.com") else "no_mx"


class StubPage:
    def __init__(self, url, text="", mailto=(), phones=()):
        self.url, self.text = url, text
        self.mailto_emails, self.link_phones = list(mailto), list(phones)


class StubFetcher:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.calls = []

    def fetch_site(self, url):
        self.calls.append(url)
        return self.pages


def channel(cid="UC1", subs=80_000, description="", handle="fitriya"):
    return {"channel_id": cid, "title": f"Channel {cid}", "handle": handle, "description": description,
            "country": "IN", "subscribers": subs, "total_views": 5_000_000, "video_count": 200,
            "uploads_playlist": "UU" + cid[2:]}


def test_own_links_skip_social_and_affiliate():
    desc = ("Insta https://instagram.com/riya | gear https://amzn.to/xyz | https://bit.ly/a "
            "| site https://riyafit.in/contact. | https://linktr.ee/riya")
    assert own_links(desc) == ["https://riyafit.in/contact", "https://linktr.ee/riya"]


def test_find_contacts_from_descriptions():
    ch = channel(description="For business enquiries: riya [at] gmail [dot] com\nWhatsApp 98765 43210")
    videos = [{"video_id": "v9", "description": "collab: other@outlook.com"}]
    fetcher = StubFetcher()
    result = find_contacts(ch, videos, fetcher, StubValidator())
    assert result["email"] == "riya@gmail.com" and result["email_status"] == "valid"
    assert result["phone"] == "+919876543210"
    assert result["contact_source"] == "channel description"
    assert result["source_url"] == "https://www.youtube.com/@fitriya"
    assert fetcher.calls == []  # nothing missing: website not fetched


def test_find_contacts_video_description_and_website():
    ch = channel(description="New videos weekly! https://riyafit.in")
    videos = [{"video_id": "v9", "description": "Business: riya.collabs@gmail.com"}]
    fetcher = StubFetcher([StubPage("https://riyafit.in/contact", "Booking / WhatsApp: +91 91234 56789")])
    result = find_contacts(ch, videos, fetcher, StubValidator())
    assert result["email"] == "riya.collabs@gmail.com"
    assert result["phone"] == "+919123456789"
    assert result["website"] == "https://riyafit.in"
    assert result["contact_source"] == "video description, website"
    assert result["source_url"] == "https://www.youtube.com/watch?v=v9 | https://riyafit.in/contact"


def test_find_contacts_reads_phones_in_channel_country():
    us = channel(description="Business inquiries: (718) 897-6869")
    us["country"] = "US"
    assert find_contacts(us, [], None, StubValidator())["phone"] == "+17188976869"
    no_country = channel(description="Business inquiries: 98765 43210")
    no_country["country"] = ""
    assert find_contacts(no_country, [], None, StubValidator())["phone"] == "+919876543210"


def test_find_contacts_never_guesses():
    result = find_contacts(channel(description="DM for collab. Workout at 5 am, 20 reps"), [], None, StubValidator())
    assert result["email"] == "" and result["phone"] == "" and result["contact_source"] == ""


# -- full run with fakes -----------------------------------------------------

def video(vid, views, likes=None, comments=0, days_ago=3, description=""):
    return {"video_id": vid, "title": vid, "description": description,
            "published_at": (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
            "views": views, "likes": likes, "comments": comments}


class FakeYouTube:
    def __init__(self, search_results, channels, videos):
        self.search_results = search_results
        self.channel_data = {c["channel_id"]: c for c in channels}
        self.videos = videos  # uploads playlist -> list | Exception
        self.queries = []
        self.video_calls = []
        self.units_used = 0

    def search_videos(self, query, published_after):
        self.queries.append((query, published_after))
        self.units_used += 100
        return self.search_results

    def channels(self, ids):
        self.units_used += 1
        return [self.channel_data[i] for i in ids if i in self.channel_data]

    def recent_videos(self, playlist):
        self.video_calls.append(playlist)
        self.units_used += 2
        item = self.videos.get(playlist, [])
        if isinstance(item, Exception):
            raise item
        return item


def make_book(extra_settings=None, leads_rows=(), rejected_rows=()):
    settings_rows = [["key", "value"]] + [[k, v] for k, v in config.DEFAULT_SETTINGS.items()]
    for key, value in (extra_settings or {}).items():
        settings_rows = [r for r in settings_rows if r[0] != key] + [[key, value]]
    return FakeSpreadsheet([
        FakeWorksheet("Settings", settings_rows),
        FakeWorksheet("Leads", [TAB_HEADERS["Leads"], *leads_rows]),
        FakeWorksheet("Rejected", [TAB_HEADERS["Rejected"], *rejected_rows]),
    ])


def run_pipeline(book, youtube, **kwargs):
    p = Pipeline(config.Config(sheet_id="x", google_service_account_json="{}"), SheetStore(book), youtube,
                 fetcher=None, validator=StubValidator(), now=lambda: NOW, **kwargs)
    return p, p.run()


def hits(*channel_ids):
    return [{"video_id": f"v-{c}", "channel_id": c, "channel_title": c, "title": "t"} for c in channel_ids]


def good_videos(email=""):
    return [video(f"g{i}", 30000, likes=900, comments=100, description=email if i == 0 else "") for i in range(10)]


def test_full_run_writes_batched_results():
    status_col = TAB_HEADERS["Leads"].index("status")
    dnc_row = ["UCdnc"] + [""] * (status_col - 1) + ["do_not_contact", ""]
    book = make_book(leads_rows=[dnc_row], rejected_rows=[["UCold", "Old", "x", "2026-10-01"]])
    yt = FakeYouTube(
        hits("UCgood", "UCsmall", "UCstale", "UClow", "UCdnc", "UCold", "UCbroken"),
        [channel("UCgood", description="Business: good@gmail.com"), channel("UCsmall", subs=3000),
         channel("UCstale"), channel("UClow"), channel("UCdnc"), channel("UCold"), channel("UCbroken")],
        {"UUgood": good_videos(), "UUstale": [video("s", 50000, days_ago=200)],
         "UUlow": [video("l", 800, likes=50)] * 10, "UUbroken": ValueError("boom")},
    )
    p, code = run_pipeline(book, yt, max_queries=2)
    assert code == 0
    assert len(yt.queries) == 2
    assert yt.queries[0][1] == NOW - timedelta(days=90)  # search window
    assert "UUdnc" not in yt.video_calls and "UUold" not in yt.video_calls
    assert "UUsmall" not in yt.video_calls  # rejected on subscribers before spending quota

    rows = book.tabs["Leads"].append_calls[0][0]
    lead = dict(zip(TAB_HEADERS["Leads"], rows[0]))
    assert len(rows) == 1 and lead["channel_id"] == "UCgood"
    assert lead["email"] == "good@gmail.com" and lead["email_status"] == "valid"
    assert lead["subscribers"] == 80000 and lead["median_views"] == 30000
    assert lead["engagement_rate"] == 3.33 and lead["last_upload"] == "2026-10-27"
    assert lead["channel_url"] == "https://www.youtube.com/@fitriya" and lead["status"] == "new"

    reasons = {r[0]: r[2] for r in book.tabs["Rejected"].append_calls[0][0]}
    assert reasons == {"UCsmall": "subscribers 3000 < 50000",
                       "UCstale": "inactive: last upload 2026-04-13",
                       "UClow": "median views 800 < 10000"}

    run = dict(zip(TAB_HEADERS["RunLog"], book.tabs["RunLog"].append_calls[0][0][0]))
    assert run["accepted"] == 1 and run["rejected"] == 3 and run["errors"] == 1
    assert run["found"] == 7 and run["youtube_units_used"] == yt.units_used
    queries = book.tabs["QueryLog"].append_calls[0][0]
    assert [q[1] for q in queries] == ["youtube", "youtube"]


def test_max_new_leads_and_quota_stop():
    book = make_book({"max_new_leads_per_run": "1"})
    yt = FakeYouTube(hits("UC1", "UC2"), [channel("UC1"), channel("UC2")],
                     {"UU1": good_videos(), "UU2": good_videos()})
    p, code = run_pipeline(book, yt, max_queries=1)
    assert code == 0 and p.stats.accepted == 1

    yt = FakeYouTube(hits("UC1", "UC2"), [channel("UC1"), channel("UC2")],
                     {"UU1": QuotaExceeded("quota"), "UU2": good_videos()})
    p, code = run_pipeline(make_book(), yt, max_queries=1)
    assert code == 0 and yt.video_calls == ["UU1"]
    assert any("YouTube stop" in w for w in p.stats.warnings)


def test_inactive_exits_cleanly_with_runlog():
    book = make_book({"active": "FALSE"})
    yt = FakeYouTube([], [], {})
    p, code = run_pipeline(book, yt)
    assert code == 0 and yt.queries == []
    assert "inactive" in book.tabs["RunLog"].rows[1][-1]


def test_dry_run_writes_nothing(capsys):
    book = make_book()
    yt = FakeYouTube(hits("UCgood"), [channel("UCgood")], {"UUgood": good_videos("biz good@gmail.com")})
    p, code = run_pipeline(book, yt, dry_run=True, max_queries=1)
    assert code == 0
    assert all(not ws.append_calls for ws in book.tabs.values())
    assert "RunLog" not in book.tabs  # bootstrap skipped in dry run
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "'g***@gmail.com'" in out
    assert "good@gmail.com" not in out  # contact details never reach (public) logs


def test_masking():
    assert mask_email("riya.fit@gmail.com") == "r***@gmail.com" and mask_email("") == "-"
    assert mask_phone("+919876543210") == "+91******3210" and mask_phone("") == "-"


def test_main_without_credentials_exits_1(monkeypatch, capsys):
    monkeypatch.setattr("pipeline.main.load_config", lambda: config.Config())
    assert main(["--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "GOOGLE_SERVICE_ACCOUNT_JSON and SHEET_ID are required" in out


# -- user-typed searches and Instagram ----------------------------------------

def searches_tab(*rows):
    return FakeWorksheet("Searches", [TAB_HEADERS["Searches"], *rows])


def test_searches_tab_rows_run_first_and_are_marked_done():
    book = make_book()
    book.tabs["Searches"] = searches_tab(["old query", "", "done", "", "", "", "", ""],
                                         ["home workout for women", "fitness", "", "", "", "", "", ""])
    yt = FakeYouTube(hits("UCgood"), [channel("UCgood", description="Business: good@gmail.com")],
                     {"UUgood": good_videos()})
    p, code = run_pipeline(book, yt, max_queries=1)
    assert code == 0
    assert yt.queries[0][0] == "home workout for women" and len(yt.queries) == 2  # + 1 rotation query
    row = dict(zip(TAB_HEADERS["Searches"], book.tabs["Searches"].rows[2]))
    assert row["status"] == "done" and row["channels_found"] == 1 and row["leads_added"] == 1
    assert book.tabs["Searches"].rows[1][2] == "done"  # untouched
    lead = dict(zip(TAB_HEADERS["Leads"], book.tabs["Leads"].append_calls[0][0][0]))
    assert lead["search_query"] == "home workout for women" and lead["niche"] == "fitness"


def test_cli_query_skips_rotation_and_is_recorded():
    book = make_book()
    yt = FakeYouTube(hits("UCgood"), [channel("UCgood")], {"UUgood": good_videos()})
    p, code = run_pipeline(book, yt, queries=["  calisthenics  "])
    assert code == 0 and [q[0] for q in yt.queries] == ["calisthenics"]
    saved = dict(zip(TAB_HEADERS["Searches"], book.tabs["Searches"].append_calls[0][0][0]))
    assert saved["query"] == "calisthenics" and saved["status"] == "done" and saved["leads_added"] == 1


def test_searches_only_without_pending_does_nothing():
    book = make_book()
    yt = FakeYouTube(hits("UCgood"), [channel("UCgood")], {"UUgood": good_videos()})
    p, code = run_pipeline(book, yt, searches_only=True)
    assert code == 0 and yt.queries == [] and not p.changed
    assert not book.tabs["RunLog"].append_calls  # no noise rows every 30 minutes


def test_search_error_is_marked():
    class FailingYouTube(FakeYouTube):
        def search_videos(self, query, published_after):
            raise QuotaExceeded("quota used up")

    book = make_book()
    book.tabs["Searches"] = searches_tab(["yoga for kids", "", "pending", "", "", "", "", ""])
    p, code = run_pipeline(book, FailingYouTube([], [], {}), searches_only=True)
    row = dict(zip(TAB_HEADERS["Searches"], book.tabs["Searches"].rows[1]))
    assert code == 0 and row["status"] == "error" and "quota used up" in row["note"]


class FakeWeb:
    def __init__(self, routes):
        self.routes, self.queries = routes, []

    def search(self, query):
        self.queries.append(query)
        return self.routes.get(query, []), "tavily"


def test_instagram_bio_fills_missing_contacts():
    from pipeline.search import SearchResult

    web = FakeWeb({'site:instagram.com "riya.lifts"': [SearchResult(
        "https://www.instagram.com/riya.lifts/", "Riya (@riya.lifts) • Instagram photos and videos",
        "45K followers · Coach · collabs: riya.collabs@gmail.com · 98765 43210", "tavily")]})
    book = make_book()
    yt = FakeYouTube(hits("UCgood"), [channel("UCgood", description="New videos weekly. Instagram: @riya.lifts")],
                     {"UUgood": good_videos()})
    p, code = run_pipeline(book, yt, max_queries=1, web_search=web)
    lead = dict(zip(TAB_HEADERS["Leads"], book.tabs["Leads"].append_calls[0][0][0]))
    assert lead["instagram"] == "riya.lifts" and lead["instagram_followers"] == 45000
    assert lead["email"] == "riya.collabs@gmail.com" and lead["phone"] == "+919876543210"
    assert lead["contact_source"] == "instagram bio (search snippet)"
    assert lead["source_url"] == "https://www.instagram.com/riya.lifts/"
    run = dict(zip(TAB_HEADERS["RunLog"], book.tabs["RunLog"].append_calls[0][0][0]))
    assert run["web_searches_used"] == 1


def test_github_output(tmp_path, monkeypatch):
    from pipeline.main import write_github_output

    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    write_github_output(True)
    write_github_output(False)
    assert out.read_text() == "changed=true\nchanged=false\n"
