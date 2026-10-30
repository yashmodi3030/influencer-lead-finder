import json
from datetime import datetime, timezone

import pytest

from pipeline import export_site
from pipeline.export_site import build_payload, decrypt_payload, encrypt_payload, write_site
from pipeline.sheets import TAB_HEADERS, SheetStore

from .conftest import FakeSpreadsheet, FakeWorksheet

FAST = 1000  # PBKDF2 iterations for tests


def test_encrypt_roundtrip_and_wrong_credentials():
    payload = {"leads": [{"handle": "riya", "bio": "Coach 💪"}]}
    blob = encrypt_payload(payload, "alice", "s3cret!", iterations=FAST)
    assert set(blob) == {"v", "kdf", "iter", "salt", "iv", "ct"}
    assert "riya" not in json.dumps(blob)
    assert decrypt_payload(blob, " alice ", "s3cret!") == payload  # username is trimmed
    with pytest.raises(ValueError):
        decrypt_payload(blob, "alice", "wrong")
    with pytest.raises(ValueError):
        decrypt_payload(blob, "bob", "s3cret!")


def test_fresh_salt_and_iv_each_time():
    a = encrypt_payload({}, "u", "p", iterations=FAST)
    b = encrypt_payload({}, "u", "p", iterations=FAST)
    assert a["salt"] != b["salt"] and a["iv"] != b["iv"]


def test_build_payload_reads_without_modifying_sheet():
    leads = FakeWorksheet("Leads", [TAB_HEADERS["Leads"], ["UCriya"] + [""] * 22, [""] * 23])
    runs = FakeWorksheet("RunLog", [TAB_HEADERS["RunLog"], ["r1"] + [""] * 9, ["r2"] + [""] * 9])
    searches = FakeWorksheet("Searches", [TAB_HEADERS["Searches"], ["q1"] + [""] * 7, ["q2", "", "done"] + [""] * 5])
    book = FakeSpreadsheet([leads, runs, searches])
    payload = build_payload(SheetStore(book), now=datetime(2026, 10, 30, tzinfo=timezone.utc),
                            sheet_id="SID", repo="owner/repo")
    assert [s["query"] for s in payload["searches"]] == ["q2", "q1"]  # newest first
    assert payload["sheet_url"] == "https://docs.google.com/spreadsheets/d/SID/edit"
    assert payload["repo"] == "owner/repo" and payload["dispatch_token"] == ""
    assert [l["channel_id"] for l in payload["leads"]] == ["UCriya"]
    assert payload["last_run"]["run_id"] == "r2"
    assert payload["generated_at"] == "2026-10-30T00:00:00+00:00"
    assert book.added == [] and not leads.append_calls


def test_write_site_copies_web_and_never_writes_credentials(tmp_path):
    out = tmp_path / "site"
    write_site(out, encrypt_payload({"leads": []}, "alice", "pw-xyz-123", iterations=FAST))
    assert (out / "index.html").exists() and (out / "app.js").exists()
    for f in out.rglob("*"):
        if f.is_file():
            assert "pw-xyz-123" not in f.read_text(encoding="utf-8", errors="ignore")


def test_main_requires_credentials(monkeypatch):
    monkeypatch.delenv("DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setattr(export_site, "load_config", lambda: None)
    assert export_site.main(["--out", "unused"]) == 1
