import pytest

from pipeline.contacts import (
    deobfuscate,
    extract_emails,
    extract_phones,
    extract_phones_near_keywords,
    is_junk_email,
    normalize_phone,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Collab: riya.fit@gmail.com", ["riya.fit@gmail.com"]),
        ("\U0001f4e9 RIYA.Fit@Gmail.com.", ["riya.fit@gmail.com"]),
        ("mail: riya [at] gmail [dot] com", ["riya@gmail.com"]),
        ("riya(at)yahoo(dot)in", ["riya@yahoo.in"]),
        ("riya {at} outlook {dot} com", ["riya@outlook.com"]),
        ("riya at gmail dot com", ["riya@gmail.com"]),
        ("riya at gmail.com", ["riya@gmail.com"]),
        ("riya @ gmail.com", ["riya@gmail.com"]),
        ("riya＠gmail.com", ["riya@gmail.com"]),
        ("biz: team[at]riyafit[dot]in", ["team@riyafit.in"]),
        ("a@gmail.com and A@gmail.com b@x.co", ["a@gmail.com", "b@x.co"]),
    ],
)
def test_extract_emails(text, expected):
    assert extract_emails(text) == expected


def test_plain_at_is_not_overeager():
    assert extract_emails("I work at gym dot com in town") == []
    assert extract_emails("Coach at Gold's Gym. DM for collab") == []


@pytest.mark.parametrize(
    "email",
    [
        "logo@2x.png", "icon@example.com", "noreply@brand.com", "abc@sentry.io",
        "605a7baede844d278b89dc95ae0a9123@sentry-next.wixpress.com", "user@domain.com",
        "photo@site.jpg",
    ],
)
def test_junk_emails(email):
    assert is_junk_email(email)
    assert extract_emails(f"see {email} here") == []


def test_real_email_not_junk():
    assert not is_junk_email("bookings@riyafit.in")


def test_deobfuscate_keeps_normal_text():
    assert deobfuscate("Leg day at 6am") == "Leg day at 6am"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("98765 43210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("0612 222 3344", "+916122223344"),
        ("12345", None),
        ("+91 98765", None),
    ],
)
def test_normalize_phone_indian(raw, expected):
    assert normalize_phone(raw) == expected


def test_extract_phones_from_bio():
    bio = "Fitness coach | Online\nWhatsApp 98765 43210 / +91-91234-56789"
    assert extract_phones(bio) == ["+919876543210", "+919123456789"]


def test_phones_near_keywords_only():
    text = ("Our gym opened in 2019 with 12000 members. Reg no 98765 43210 issued to the trust. "
            + "x" * 120
            + " For bookings and collab call 91234 56789 anytime.")
    assert extract_phones_near_keywords(text) == ["+919123456789"]
