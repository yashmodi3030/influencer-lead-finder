from pipeline.instagram_finder import InstagramFinder, handles_in_text, matches_channel
from pipeline.search import AllSearchUnavailable, SearchResult


def test_handles_in_text():
    text = ("Follow me!\nInstagram: @Riya.Fit\nhttps://www.instagram.com/riya_backup/ "
            "IG - @third_one  mail me at riya@gmail.com  instagr.am/short_link")
    assert handles_in_text(text) == ["riya_backup", "short_link", "riya.fit", "third_one"]
    assert handles_in_text("no social here, write to a@b.com") == []
    assert handles_in_text("instagram.com/p/abc123 and instagram.com/explore") == []


def test_matches_channel():
    assert matches_channel("getfitwithrick", ["getfitwithrick", "Get Fit With Rick"])
    assert matches_channel("rick.fitness", ["", "Rick Fitness"])
    assert matches_channel("seannalewanyj_", ["seannal", "Sean Nalewanyj"])
    assert not matches_channel("randomgym", ["getfitwithrick", "Get Fit With Rick"])
    assert not matches_channel("ab", ["ab"])


class FakeWeb:
    def __init__(self, routes):
        self.routes = routes  # query -> results | Exception
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        item = self.routes.get(query, [])
        if isinstance(item, Exception):
            raise item
        return item, "tavily"


CHANNEL = {"channel_id": "UC1", "title": "Get Fit With Rick", "handle": "getfitwithrick"}


def profile(handle, snippet):
    return SearchResult(f"https://www.instagram.com/{handle}/", f"Rick (@{handle}) • Instagram photos and videos",
                        snippet, "tavily")


def test_handle_from_description_then_bio_from_search_snippet():
    web = FakeWeb({'site:instagram.com "rick.lifts"': [
        SearchResult("https://www.instagram.com/rick.lifts/reels", "x", "reels page", "tavily"),
        profile("rick.lifts", "120K followers · 50 following · Coach | business: rick@gmail.com | WhatsApp 98765 43210"),
    ]})
    finder = InstagramFinder(web, max_lookups=5)
    res = finder.find(CHANNEL, [("Videos weekly", "channel description"), ("IG: @rick.lifts", "video description")],
                      need_contacts=True, region="IN")
    assert (res.handle, res.found_via, res.url) == ("rick.lifts", "video description", "https://www.instagram.com/rick.lifts/")
    assert res.followers == 120000
    assert res.emails == ["rick@gmail.com"] and res.phones == ["+919876543210"]
    assert web.queries == ['site:instagram.com "rick.lifts"'] and finder.lookups_used == 1


def test_no_search_when_contacts_complete():
    web = FakeWeb({})
    finder = InstagramFinder(web, max_lookups=5)
    res = finder.find(CHANNEL, [("insta: @rick.lifts", "channel description")], need_contacts=False, region="IN")
    assert res.handle == "rick.lifts" and web.queries == []


def test_web_search_accepts_only_matching_handle_and_reuses_snippet():
    web = FakeWeb({'site:instagram.com "Get Fit With Rick"': [
        profile("someone_else", "9M followers"),  # wrong person: rejected
        profile("getfitwithrick", "880K followers · Coach · support@getfitwithrick.com"),
    ]})
    finder = InstagramFinder(web, max_lookups=5)
    res = finder.find(CHANNEL, [("no links", "channel description")], need_contacts=True, region="US")
    assert res.handle == "getfitwithrick" and res.found_via == "web search"
    assert res.followers == 880000 and res.emails == ["support@getfitwithrick.com"]
    assert len(web.queries) == 1  # profile snippet came with the first search


def test_budget_and_unavailable_search():
    finder = InstagramFinder(FakeWeb({}), max_lookups=0)
    assert finder.find(CHANNEL, [], need_contacts=True, region="IN").handle == ""
    web = FakeWeb({'site:instagram.com "Get Fit With Rick"': AllSearchUnavailable("all down")})
    finder = InstagramFinder(web, max_lookups=5)
    assert finder.find(CHANNEL, [], need_contacts=True, region="IN").handle == ""
    assert finder.lookups_left == 0  # stops searching for the rest of the run
    assert InstagramFinder(None, max_lookups=5).lookups_left == 0
