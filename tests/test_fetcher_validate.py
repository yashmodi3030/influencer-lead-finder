import dns.exception

from pipeline.fetcher import PoliteFetcher, decode_cfemail, is_blocked, parse_html
from pipeline.validate import INVALID, NO_MX, UNCHECKED, VALID, EmailValidator

from .conftest import FakeResponse, FakeSession

HOME = """
<html><body><script>var x='js@example.com'</script>
<a href="/contact">Contact us</a> <a href="/about-me/">About</a>
<a href="https://other.com/contact">Other</a> <a href="/shop">Shop</a>
<a href="mailto:Hello@RiyaFit.in?subject=hi">Mail</a>
<a href="tel:+91 98765 43210">Call</a>
<a href="https://wa.me/919123456789">WhatsApp</a>
</body></html>
"""


def test_parse_html_extracts_links_and_contacts():
    page, links = parse_html("https://riyafit.in/", HOME)
    assert links == ["https://riyafit.in/contact", "https://riyafit.in/about-me/"]
    assert page.mailto_emails == ["hello@riyafit.in"]
    assert page.link_phones == ["+919876543210", "+919123456789"]
    assert "js@example.com" not in page.text


def test_decode_cfemail():
    # "a@b.co" encoded with key 0x42
    key = 0x42
    encoded = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "a@b.co")
    assert decode_cfemail(encoded) == "a@b.co"


def test_blocked_domains():
    assert is_blocked("https://www.instagram.com/riya")
    assert is_blocked("https://www.youtube.com/@riya/about")
    assert is_blocked("https://m.facebook.com/x")
    assert not is_blocked("https://linktr.ee/riya")


def test_fetch_site_respects_robots_and_one_level():
    pages = {
        "https://riyafit.in/robots.txt": FakeResponse(200, text="User-agent: *\nDisallow: /about-me/"),
        "https://riyafit.in/": FakeResponse(200, text=HOME, headers={"Content-Type": "text/html"},
                                            url="https://riyafit.in/"),
        "https://riyafit.in/contact": FakeResponse(
            200, text='<a href="/contact/deeper">More contact</a> biz: team@riyafit.in',
            headers={"Content-Type": "text/html"}, url="https://riyafit.in/contact"),
    }
    session = FakeSession(router=lambda m, url, kw: pages.get(url) or FakeResponse(404, text="nope"))
    fetcher = PoliteFetcher(session=session, sleep=lambda s: None)
    result = fetcher.fetch_site("https://riyafit.in/")
    assert [p.url for p in result] == ["https://riyafit.in/", "https://riyafit.in/contact"]
    fetched = [c[1] for c in session.calls]
    assert "https://riyafit.in/about-me/" not in fetched  # disallowed by robots.txt
    assert "https://riyafit.in/contact/deeper" not in fetched  # only one level deep
    assert "https://other.com/contact" not in fetched  # other domain
    assert session.calls[1][2]["timeout"] == 10


def test_fetch_never_touches_instagram():
    session = FakeSession([])
    fetcher = PoliteFetcher(session=session)
    assert fetcher.fetch_site("https://instagram.com/riya") == []
    assert session.calls == []


def test_validator_statuses():
    lookups = []

    def has_mx(domain):
        lookups.append(domain)
        if domain == "slow.in":
            raise dns.exception.Timeout()
        return domain == "gmail.com"

    v = EmailValidator(has_mx=has_mx)
    assert v.status("a@gmail.com") == VALID
    assert v.status("b@gmail.com") == VALID
    assert lookups.count("gmail.com") == 1  # cached
    assert v.status("a@nomail.xyz") == NO_MX
    assert v.status("not-an-email") == INVALID
    assert v.status("a@slow.in") == UNCHECKED
